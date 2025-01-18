from __future__ import annotations

import random
import signal
import string
import time
import typing as t
import uuid
from queue import Queue

import globus_compute_sdk as gc
import globus_sdk
import pytest
import responses
from globus_compute_endpoint import engines
from globus_compute_endpoint.engines.base import GlobusComputeEngineBase
from globus_compute_sdk.sdk.web_client import WebClient
from globus_compute_sdk.serialize import ComputeSerializer
from parsl.launchers import SimpleLauncher
from parsl.providers import LocalProvider

from .utils import create_task_packer


@pytest.fixture(autouse=True)
def verify_all_tests_reset_signals():
    orig_sig_handlers = [
        (sig, signal.getsignal(sig))
        for sig in range(1, 21)
        if sig not in (signal.SIGKILL, signal.SIGSTOP)  # the uncatchables
    ]
    yield
    handlers = [(sig, sigh, signal.getsignal(sig)) for sig, sigh in orig_sig_handlers]
    for sig, bef, aft in handlers:
        assert bef == aft, f"Signal {sig} not reset"


@pytest.fixture
def reset_signals():
    # As identified by an interaction with Parsl during our UTs, ensure that we
    # return the signals after each test.  Interprocess play is fun ...
    orig_sig_handlers = [
        (sig, signal.getsignal(sig))
        for sig in range(1, 21)
        if sig not in (signal.SIGKILL, signal.SIGSTOP)  # the uncatchables
    ]

    yield
    for sig, sigh in orig_sig_handlers:
        signal.signal(sig, sigh)


@pytest.fixture(scope="session")
def endpoint_uuid():
    return str(uuid.UUID(int=0))


@pytest.fixture(scope="session")
def default_endpoint_id():
    return str(uuid.UUID(int=1))


@pytest.fixture(scope="session")
def other_endpoint_id():
    return str(uuid.UUID(int=2))


@pytest.fixture
def ep_uuid() -> str:
    return str(uuid.uuid4())


@pytest.fixture(scope="session")
def tod_session_num():
    yield round(time.time()) % 86400


class FakeLoginManager:
    def ensure_logged_in(self) -> None: ...

    def logout(self) -> bool: ...

    def get_auth_client(self) -> globus_sdk.AuthClient:
        return globus_sdk.AuthClient(authorizer=globus_sdk.NullAuthorizer())

    def get_web_client(self, *, base_url: str | None = None) -> WebClient:
        return WebClient(
            base_url="https://compute.api.globus.org",
            authorizer=globus_sdk.NullAuthorizer(),
        )


@pytest.fixture
def get_standard_compute_client():
    responses.add(
        method=responses.GET,
        url="https://compute.api.globus.org/v2/version",
        headers={"Content-Type": "application/json"},
        json={"api": "0.4.0", "min_ep_version": "0.0.0", "min_sdk_version": "0.0.0"},
    )

    def func():
        return gc.Client(
            login_manager=FakeLoginManager(),
            do_version_check=False,
        )

    return func


###
# Engines
###


@pytest.fixture
def engine_heartbeat() -> int:
    return 1


@pytest.fixture
def nodeslist(num=100):
    limit = random.randint(2, num)
    yield [f"NODE{node_i}" for node_i in range(1, limit)]


@pytest.fixture
def engine_runner(
    request, tmp_path, engine_heartbeat, nodeslist, reporting_period=0.1
) -> t.Callable:
    engines_to_shutdown = []

    def _runner(engine_type: t.Type[GlobusComputeEngineBase], **kwargs):
        ep_id = uuid.uuid4()
        queue = Queue()
        if engine_type is engines.ProcessPoolEngine:
            k = dict(max_workers=2)
        elif engine_type is engines.ThreadPoolEngine:
            k = dict(max_workers=2)
        elif engine_type is engines.GlobusComputeEngine:
            k = dict(
                address="::1",
                heartbeat_period=engine_heartbeat,
                heartbeat_threshold=2,
                job_status_kwargs=dict(max_idletime=0, strategy_period=0.1),
            )
        elif engine_type is engines.GlobusMPIEngine:
            nodefile_path = tmp_path / "pbs_nodefile"
            nodes_string = "\n".join(nodeslist)
            worker_init = f"""
                echo -e "{nodes_string}" > {nodefile_path} ;
                export PBS_NODEFILE={nodefile_path}
                """

            k = dict(
                address="::1",
                heartbeat_period=engine_heartbeat,
                heartbeat_threshold=1,
                mpi_launcher="mpiexec",
                provider=LocalProvider(
                    init_blocks=1,
                    min_blocks=1,
                    max_blocks=1,
                    worker_init=worker_init,
                    launcher=SimpleLauncher(),
                ),
            )
        else:
            raise NotImplementedError(f"Unimplemented: {engine_type.__name__}")
        k.update(**kwargs)
        engine = engine_type(**k)
        engine._status_report_thread.reporting_period = reporting_period
        engine.start(
            endpoint_id=ep_id, run_dir=str(tmp_path), results_passthrough=queue
        )
        if hasattr(engine, "job_status_poller"):
            engine.job_status_poller._thread.name = f"{request.node.name}-ERUNNER"
        engines_to_shutdown.append(engine)
        return engine

    yield _runner
    for ngin in engines_to_shutdown:
        ngin.shutdown(block=True)


###
# Misc
###


def randomstring_impl(length=5, alphabet=string.ascii_letters):
    return "".join(random.choice(alphabet) for _ in range(length))


@pytest.fixture
def randomstring():
    return randomstring_impl


@pytest.fixture
def noop():
    def _wrapped(*args, **kwargs):
        pass

    return _wrapped


@pytest.fixture
def task_uuid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def container_uuid() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture(scope="module")
def serde():
    return ComputeSerializer()


@pytest.fixture
def ez_pack_task(serde, task_uuid, container_uuid):
    return create_task_packer(serde, task_uuid, container_uuid)


@pytest.fixture
def htex_warns():
    with pytest.warns(DeprecationWarning) as pyt_w:
        yield

    def _warned(msg: str) -> bool:
        test = "HighThroughputEngine is deprecated" in msg
        test &= "Please use GlobusComputeEngine instead" in msg
        return test

    assert any(_warned(str(w)) for w in pyt_w.list)
