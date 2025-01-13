import concurrent.futures
import logging
import pathlib
import random
import time
from queue import Queue
from unittest import mock

import pytest
from globus_compute_common import messagepack
from globus_compute_common.messagepack.message_types import TaskTransition
from globus_compute_common.tasks import ActorName, TaskState
from globus_compute_endpoint.endpoint.config import UserEndpointConfig
from globus_compute_endpoint.endpoint.config.utils import serialize_config
from globus_compute_endpoint.engines import (
    GlobusComputeEngine,
    GlobusMPIEngine,
    ProcessPoolEngine,
    ThreadPoolEngine,
)
from globus_compute_endpoint.engines.base import GlobusComputeEngineBase
from globus_compute_sdk.serialize.concretes import SELECTABLE_STRATEGIES
from parsl import HighThroughputExecutor
from parsl.executors.high_throughput.interchange import ManagerLost
from parsl.providers import KubernetesProvider
from tests.utils import double, get_cwd, kill_manager

logger = logging.getLogger(__name__)


def test_result_message_packing(serde, task_uuid):
    exec_start = TaskTransition(
        timestamp=time.time_ns(), state=TaskState.EXEC_START, actor=ActorName.WORKER
    )

    result = random.randint(0, 1000)

    exec_end = TaskTransition(
        timestamp=time.time_ns(), state=TaskState.EXEC_END, actor=ActorName.WORKER
    )
    result_message = dict(
        task_id=task_uuid,
        data=serde.serialize(result),
        task_statuses=[exec_start, exec_end],
    )

    mResult = messagepack.message_types.Result(**result_message)
    assert isinstance(mResult, messagepack.message_types.Result)
    packed_result = messagepack.pack(mResult)
    assert isinstance(packed_result, bytes)

    unpacked = messagepack.unpack(packed_result)
    assert isinstance(unpacked, messagepack.message_types.Result)

    assert unpacked.task_id == task_uuid
    assert serde.deserialize(unpacked.data) == result


@pytest.mark.parametrize(
    "engine_type", (ProcessPoolEngine, ThreadPoolEngine, GlobusComputeEngine)
)
def test_engine_start(
    engine_type: GlobusComputeEngineBase, engine_runner, endpoint_uuid, tmp_path
):
    """Engine.submit should fail before engine is started"""

    engine = engine_type()
    assert not engine._engine_ready, "Engine should not be ready before start"

    engine.executor = mock.Mock()
    engine.executor.status_polling_interval = 0

    # task submit should raise Exception if it was not started
    with pytest.raises(RuntimeError):
        engine.submit(str(endpoint_uuid), b"", {})

    engine.start(endpoint_id=endpoint_uuid, run_dir=tmp_path)
    assert engine._engine_ready, "Engine should be ready after start"

    engine.shutdown()


@pytest.mark.parametrize(
    "engine_type", (ProcessPoolEngine, ThreadPoolEngine, GlobusComputeEngine)
)
def test_engine_submit(engine_type: GlobusComputeEngineBase, engine_runner):
    """Test engine.submit with multiple engines"""
    engine = engine_runner(engine_type)

    param = random.randint(1, 100)
    resource_spec = {}
    future = engine._submit(double, resource_spec, param)
    assert isinstance(future, concurrent.futures.Future)

    # 5-seconds is nominally "overkill," but gc on CI appears to need (at least) >1s
    assert future.result(timeout=5) == param * 2


@pytest.mark.parametrize(
    "engine_type", (ProcessPoolEngine, ThreadPoolEngine, GlobusComputeEngine)
)
def test_engine_working_dir(
    engine_type: GlobusComputeEngineBase,
    engine_runner,
    ez_pack_task,
    serde,
    task_uuid,
):
    """working dir remains constant across multiple fn invocations
    This test requires submitting the task payload so that the execute_task
    wrapper is used which switches into the working_dir, which created
    working_dir nesting when relative paths were used.
    """
    engine = engine_runner(engine_type)

    task_args = (str(task_uuid), ez_pack_task(get_cwd), {})

    future1 = engine.submit(*task_args)
    unpacked1 = messagepack.unpack(future1.result())  # blocks; avoid race condition

    future2 = engine.submit(*task_args)  # exact same task
    unpacked2 = messagepack.unpack(future2.result())

    # data is enough for test, but in error case, be kind to dev
    cwd1 = serde.deserialize(unpacked1.data)
    cwd2 = serde.deserialize(unpacked2.data)
    assert cwd1 == cwd2, "working dir should be idempotent"


@pytest.mark.parametrize(
    "engine_type", (ProcessPoolEngine, ThreadPoolEngine, GlobusComputeEngine)
)
def test_engine_submit_internal(
    engine_type: GlobusComputeEngineBase, engine_runner, serde, task_uuid, ez_pack_task
):
    engine = engine_runner(engine_type)

    q = engine.results_passthrough
    task_bytes = ez_pack_task(double, 3)
    future = engine.submit(str(task_uuid), task_bytes, resource_specification={})
    packed_result = future.result()

    # Confirm that the future got the right answer
    assert isinstance(packed_result, bytes)
    result = messagepack.unpack(packed_result)
    assert isinstance(result, messagepack.message_types.Result)
    assert result.task_id == task_uuid

    # Confirm that the same result got back though the queue
    for _i in range(3):
        q_msg = q.get(timeout=0.1)
        assert isinstance(q_msg, dict)

        packed_result_q = q_msg["message"]
        result = messagepack.unpack(packed_result_q)
        # Handle a sneaky EPStatusReport that popped in ahead of the result
        if isinstance(result, messagepack.message_types.EPStatusReport):
            continue

        passed_task_id = q_msg["task_id"]
        # At this point the message should be the result
        assert (
            packed_result == packed_result_q
        ), "Result from passthrough_q and future should match"

        assert passed_task_id == str(task_uuid)
        assert result.task_id == task_uuid
        final_result = serde.deserialize(result.data)
        assert final_result == 6
        break


@pytest.mark.parametrize(
    "engine_type",
    (ProcessPoolEngine, ThreadPoolEngine, GlobusComputeEngine),
)
def test_allowed_serializers_passthrough_to_serde(engine_type, engine_runner):
    engine = engine_runner(engine_type, allowed_serializers=SELECTABLE_STRATEGIES)

    assert engine.serde is not None
    assert engine.serde.allowed_deserializer_types == set(SELECTABLE_STRATEGIES)


def test_gc_engine_system_failure(ez_pack_task, task_uuid, engine_runner):
    """Test behavior of engine failure killing task"""
    engine = engine_runner(GlobusComputeEngine, max_retries_on_system_failure=0)

    task_bytes = ez_pack_task(kill_manager)
    future = engine.submit(str(task_uuid), task_bytes, {})

    assert isinstance(future, concurrent.futures.Future)
    with pytest.raises(ManagerLost):
        future.result()


def test_serialized_engine_config_has_provider():
    loopback = "::1"
    ep_config = UserEndpointConfig(executors=[GlobusComputeEngine(address=loopback)])

    res = serialize_config(ep_config)
    executor = res["executors"][0].get("executor") or res["executors"][0]

    assert executor.get("provider")


def test_gcengine_compute_launch_cmd():
    engine = GlobusComputeEngine(address="::1")
    assert engine.executor.launch_cmd.startswith(
        "globus-compute-endpoint python-exec"
        " parsl.executors.high_throughput.process_worker_pool"
    )
    assert "process_worker_pool.py" not in engine.executor.launch_cmd


def test_gcengine_compute_interchange_launch_cmd():
    engine = GlobusComputeEngine(address="::1")
    assert engine.executor.interchange_launch_cmd[:3] == [
        "globus-compute-endpoint",
        "python-exec",
        "parsl.executors.high_throughput.interchange",
    ]
    assert "interchange.py" not in engine.executor.interchange_launch_cmd


def test_gcengine_pass_through_to_executor(randomstring):
    args = ("arg1", 2)
    kwargs = {
        "label": "VroomEngine",
        "address": "::1",
        "encrypted": False,
        "max_workers_per_node": 1,
        "foo": "bar",
        randomstring(): randomstring(),
    }

    m = mock.Mock(launch_cmd="")
    with mock.patch.object(GlobusComputeEngine, "_ExecutorClass") as mock_ex:
        mock_ex.__name__ = randomstring()
        mock_ex.return_value = m
        GlobusComputeEngine(*args, **kwargs)
        kwargs["label"] = f"{kwargs['label']}-{mock_ex.__name__}"
    a, k = mock_ex.call_args
    assert a == args
    assert kwargs == k


def test_gcengine_start_pass_through_to_executor(tmp_path: pathlib.Path, endpoint_uuid):
    mock_ex = mock.Mock(
        status_polling_interval=5,
        launch_cmd="foo-bar",
        interchange_launch_cmd="foo-bar",
    )

    scripts_dir = str(tmp_path / "submit_scripts")

    with mock.patch.object(GlobusComputeEngine, "_ExecutorClass", mock.Mock):
        engine = GlobusComputeEngine(executor=mock_ex)

    assert mock_ex.run_dir != tmp_path
    assert mock_ex.provider.script_dir != scripts_dir

    engine.start(
        endpoint_id=endpoint_uuid, run_dir=tmp_path, results_passthrough=Queue()
    )
    engine.shutdown()

    assert mock_ex.run_dir == tmp_path
    assert mock_ex.provider.script_dir == scripts_dir


def test_gcengine_start_provider_without_channel(tmp_path: pathlib.Path, endpoint_uuid):
    mock_executor = mock.Mock(spec=HighThroughputExecutor)
    mock_executor.status_polling_interval = 5
    mock_executor.provider = mock.Mock(spec=KubernetesProvider)
    mock_executor.launch_cmd = "foo-bar"
    mock_executor.interchange_launch_cmd = "foo-bar"

    assert not hasattr(mock_executor.provider, "channel"), "Verify test setup"

    engine = GlobusComputeEngine(executor=mock_executor)
    engine.start(
        endpoint_id=endpoint_uuid, run_dir=tmp_path, results_passthrough=Queue()
    )
    engine.shutdown()


@pytest.mark.parametrize("encrypted", (True, False))
def test_gcengine_encrypted(encrypted: bool, engine_runner):
    engine: GlobusComputeEngine = engine_runner(
        GlobusComputeEngine, encrypted=encrypted
    )
    assert engine.encrypted is engine.executor.encrypted is encrypted

    with pytest.raises(AttributeError):
        engine.encrypted = not encrypted

    engine.executor.encrypted = not encrypted
    assert engine.encrypted is not encrypted


def test_gcengine_new_executor_not_exceptional():
    gce = GlobusComputeEngine(address="::1")
    assert gce.executor_exception is None, "Expect no exception from fresh Executor"


def test_gcengine_executor_exception_passthrough(randomstring):
    gce = GlobusComputeEngine(address="::1")
    exc_text = randomstring()
    gce.executor.set_bad_state_and_fail_all(ZeroDivisionError(exc_text))
    assert isinstance(gce.executor_exception, ZeroDivisionError)
    assert exc_text in str(gce.executor_exception)


def test_gcengine_bad_state_futures_failed_immediately(randomstring, task_uuid):
    gce = GlobusComputeEngine(address="::1")
    gce._engine_ready = True
    exc_text = randomstring()
    gce.executor.set_bad_state_and_fail_all(ZeroDivisionError(exc_text))

    taskb = b"some packed task bytes"
    futs = [
        gce.submit(task_id=str(task_uuid), packed_task=taskb, resource_specification={})
        for _ in range(5)
    ]

    assert all(f.done() for f in futs), "Expect immediate completion for failed state"
    assert all(exc_text in str(f.exception()) for f in futs)


def test_gcengine_exception_report_from_bad_state(task_uuid):
    gce = GlobusComputeEngine(address="::1")
    gce._engine_ready = True
    gce.executor.set_bad_state_and_fail_all(ZeroDivisionError())

    gce.submit(
        task_id=str(task_uuid), resource_specification={}, packed_task=b"MOCK_TASK"
    )

    result = None
    for _i in range(10):
        q_msg = gce.results_passthrough.get(timeout=5)
        packed_result_q = q_msg["message"]
        result = messagepack.unpack(packed_result_q)
        if isinstance(result, messagepack.message_types.Result):
            break

    assert result.task_id == task_uuid
    assert result.error_details.code == "RemoteExecutionError"
    assert "ZeroDivisionError" in result.data


def test_gcengine_rejects_mpi_mode(randomstring):
    with pytest.raises(ValueError) as pyt_exc_1:
        GlobusComputeEngine(enable_mpi_mode=True, address="::1")

    assert "is not supported" in str(pyt_exc_1)

    with pytest.raises(ValueError) as pyt_exc_2:
        GlobusComputeEngine(mpi_launcher=randomstring(), address="::1")

    assert "is not supported" in str(pyt_exc_2)


def test_gcengine_rejects_resource_specification(task_uuid):
    with pytest.raises(ValueError) as pyt_exc:
        gce = GlobusComputeEngine(address="::1")
        gce._engine_ready = True
        gce.submit(
            str(task_uuid),
            packed_task=b"packed_task",
            resource_specification={"foo": "bar"},
        ).result()

    assert "is not supported" in str(pyt_exc)


def test_gcmpiengine_default_executor(randomstring):
    with mock.patch.object(GlobusMPIEngine, "_ExecutorClass") as mock_ex:
        mock_ex.__name__ = randomstring()
        mock_ex.return_value = mock.Mock(launch_cmd="")
        engine = GlobusMPIEngine()

    assert mock_ex.called, "Expect Parsl's MPIExecutor as default option"

    exp_en_label = GlobusMPIEngine.__name__
    exp_ex_label = f"{exp_en_label}-{mock_ex.__name__}"
    assert engine.label == exp_en_label, "Expect reasonable, explanatory default"
    a, k = mock_ex.call_args
    assert k["label"] == exp_ex_label, "Expect distinct executor label"
    assert k["encrypted"] is True, "Expect encrypted by default"


def test_gcmpiengine_accepts_resource_specification(task_uuid, randomstring):
    spec = {"some": "conf", "sentinel": randomstring()}
    with mock.patch.object(GlobusMPIEngine, "_ExecutorClass") as mock_ex:
        mock_ex.__name__ = "ClassName"
        mock_ex.return_value = mock.Mock(launch_cmd="")
        engine = GlobusMPIEngine(address="::1")
        engine._engine_ready = True
        engine.submit(str(task_uuid), b"some task", resource_specification=spec)

    assert engine.executor.submit.called, "Verify test: correct internal method invoked"

    a, _k = engine.executor.submit.call_args
    assert spec in a
