import inspect
import sys
import uuid
from unittest import mock

import globus_compute_sdk as gc
import pytest
from globus_compute_sdk import ContainerSpec, __version__
from globus_compute_sdk.errors import TaskExecutionFailed
from globus_compute_sdk.sdk.auth.auth_client import ComputeAuthClient
from globus_compute_sdk.sdk.login_manager import LoginManager
from globus_compute_sdk.sdk.utils import get_env_details
from globus_compute_sdk.sdk.web_client import (
    FunctionRegistrationData,
    FunctionRegistrationMetadata,
    WebClient,
    _get_packed_code,
)
from globus_compute_sdk.serialize import ComputeSerializer
from globus_compute_sdk.serialize.concretes import SELECTABLE_STRATEGIES
from globus_sdk import UserApp
from globus_sdk import __version__ as __version_globus__
from pytest_mock import MockerFixture

_MOCK_BASE = "globus_compute_sdk.sdk.client."


@pytest.fixture(autouse=True)
def _clear_sdk_env(monkeypatch):
    monkeypatch.delenv("GLOBUS_COMPUTE_ENVIRONMENT", raising=False)


@pytest.fixture
def gcc():
    _gcc = gc.Client(
        do_version_check=False,
        login_manager=mock.Mock(spec=LoginManager),
    )

    yield _gcc


def funk():
    return "Funky"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"foo": "bar"},
        {"environment": "dev", "fx_authorizer": "blah"},
        {"asynchronous": True},
    ],
)
def test_client_warns_on_unknown_kwargs(kwargs, mocker: MockerFixture):
    mocker.patch(f"{_MOCK_BASE}ComputeAuthClient")
    mocker.patch(f"{_MOCK_BASE}WebClient")

    known_kwargs = [
        "funcx_home",
        "environment",
        "local_compute_services",
        "do_version_check",
        "code_serialization_strategy",
        "data_serialization_strategy",
        "login_manager",
        "app",
    ]
    unknown_kwargs = [k for k in kwargs if k not in known_kwargs]

    with pytest.warns(UserWarning) as warnings:
        _ = gc.Client(do_version_check=False, app=mock.Mock(spec=UserApp), **kwargs)

    assert len(warnings) == len(unknown_kwargs)
    for warning in warnings:
        assert any(k in str(warning.message) for k in unknown_kwargs)


@pytest.mark.parametrize("env", [None, "local", "dev", "production"])
@pytest.mark.parametrize("usage_method", ["env_var", "param"])
def test_client_init_sets_addresses_by_env(
    monkeypatch, env, usage_method, randomstring, mocker: MockerFixture
):
    mocker.patch(f"{_MOCK_BASE}ComputeAuthClient")
    mocker.patch(f"{_MOCK_BASE}WebClient")

    if env in (None, "production"):
        web_uri = "https://compute.api.globus.org"
    elif env == "dev":
        web_uri = "https://api.dev.funcx.org"
    elif env == "local":
        web_uri = "http://localhost:5000"
    else:
        raise NotImplementedError

    # default kwargs: turn off external interactions
    kwargs = {
        "app": mock.Mock(spec=UserApp),
        "do_version_check": False,
    }
    # either pass the env as a param or set it in the environment
    if usage_method == "param":
        kwargs["environment"] = env
    elif usage_method == "env_var":
        if env is not None:
            monkeypatch.setenv("GLOBUS_SDK_ENVIRONMENT", env)
    else:
        raise NotImplementedError

    client = gc.Client(**kwargs)

    # finally, confirm that the address was set correctly
    assert client.web_service_address == web_uri


@pytest.mark.parametrize(
    "api_data",
    [
        {"status": "Success"},  # success without result|exception
        {"status": "FAILED"},  # failure without result|exception
        "abc123",  # string, but not valid JSON
    ],
)
def test_update_task_table_on_invalid_data(gcc, api_data):
    with pytest.raises(ValueError):
        gcc._update_task_table(api_data, "task-id-foo")


def test_update_task_table_on_exception(gcc):
    api_data = {
        "status": "success",
        "exception": "foo-bar-baz",
        "completion_t": "1.1",
        "task_id": "task-id-foo",
    }

    with pytest.raises(TaskExecutionFailed) as excinfo:
        gcc._update_task_table(api_data, "task-id-foo")
    assert "foo-bar-baz" in str(excinfo.value)


def test_update_task_table_simple_object(gcc, randomstring):
    serde = ComputeSerializer()
    task_id = "some_task_id"

    payload = randomstring()
    data = {"task_id": task_id, "status": "success", "completion_t": "1.1"}
    data["result"] = serde.serialize(payload)

    st = gcc._update_task_table(data, task_id)
    assert not st["pending"]
    assert st["result"] == payload
    assert "exception" not in st


def test_pending_tasks_always_fetched(gcc):
    should_fetch_01 = str(uuid.uuid4())
    should_fetch_02 = str(uuid.uuid4())
    no_fetch = str(uuid.uuid4())

    gcc.web_client = mock.MagicMock()
    gcc._task_status_table.update(
        {
            should_fetch_01: {"pending": True, "task_id": should_fetch_01},
            no_fetch: {"pending": False, "task_id": no_fetch},
        }
    )
    task_id_list = [no_fetch, should_fetch_01, should_fetch_02]

    # bulk avenue
    gcc.get_batch_result(task_id_list)

    args, _ = gcc.web_client.get_batch_status.call_args
    assert should_fetch_01 in args[0]
    assert should_fetch_02 in args[0]
    assert no_fetch not in args[0]

    # individual avenue
    for should_fetch, sf in (
        (True, should_fetch_01),
        (True, should_fetch_02),
        (False, no_fetch),
    ):
        gcc.web_client.get_task.reset_mock()
        gcc.get_task(sf)
        assert should_fetch is gcc.web_client.get_task.called
        if should_fetch:
            args, _ = gcc.web_client.get_task.call_args
            assert sf == args[0]


@pytest.mark.parametrize("create_result_queue", [True, False, None])
def test_batch_created_websocket_queue(gcc, create_result_queue):
    eid = str(uuid.uuid4())
    fid = str(uuid.uuid4())

    if create_result_queue is None:
        batch = gcc.create_batch()
    else:
        batch = gcc.create_batch(create_websocket_queue=create_result_queue)

    batch.add(fid, (1,))
    batch.add(fid, (2,))

    gcc.batch_run(eid, batch)

    assert gcc.web_client.submit.called
    *_, submit_data = gcc.web_client.submit.call_args[0]
    assert "create_queue" in submit_data
    assert submit_data["create_queue"] is bool(create_result_queue)


@pytest.mark.parametrize(
    "strategy", [s for s in SELECTABLE_STRATEGIES if not s.for_code]
)
def test_batch_respects_serialization_strategy(gcc, strategy):
    gcc.fx_serializer = ComputeSerializer(strategy_data=strategy())

    fn_id = str(uuid.uuid4())
    args = (1, 2, 3)
    kwargs = {"a": "b", "c": "d"}

    batch = gcc.create_batch()
    batch.add(fn_id, args, kwargs)
    tasks = batch.prepare()["tasks"]

    ser = ComputeSerializer(strategy_data=strategy())
    expected = {fn_id: [ser.pack_buffers([ser.serialize(args), ser.serialize(kwargs)])]}

    assert tasks == expected


def test_batch_includes_user_runtime_info(gcc):
    batch = gcc.create_batch()
    payload = batch.prepare()

    assert payload["user_runtime"] == {
        "globus_compute_sdk_version": __version__,
        "globus_sdk_version": __version_globus__,
        "python_version": sys.version,
    }


def test_build_container(mocker, gcc, randomstring):
    expected_container_id = randomstring()
    mock_data = mocker.Mock(data={"container_id": expected_container_id})
    gcc.web_client.post.return_value = mock_data
    spec = ContainerSpec(
        name="MyContainer",
        pip=["matplotlib==3.5.1", "numpy==1.18.5"],
        python_version="3.8",
        payload_url="https://github.com/funcx-faas/funcx-container-service.git",
    )

    container_id = gcc.build_container(spec)

    assert container_id == expected_container_id
    assert gcc.web_client.post.called
    a, k = gcc.web_client.post.call_args
    assert a[0] == "/v2/containers/build"
    assert k == {"data": spec.to_json()}


def test_container_build_status(gcc, randomstring):
    expected_status = randomstring()

    class MockData(dict):
        def __init__(self):
            super().__init__()
            self["status"] = expected_status
            self.http_status = 200

    gcc.web_client.get.return_value = MockData()
    status = gcc.get_container_build_status("123-434")
    assert status == expected_status


def test_container_build_status_not_found(gcc, randomstring):
    class MockData(dict):
        def __init__(self):
            super().__init__()
            self.http_status = 404

    gcc.web_client.get.return_value = MockData()

    look_for = randomstring()
    with pytest.raises(ValueError) as excinfo:
        gcc.get_container_build_status(look_for)

    assert excinfo.value.args[0] == f"Container ID {look_for} not found"


def test_container_build_status_failure(gcc):
    class MockData(dict):
        def __init__(self):
            super().__init__()
            self.http_status = 500
            self.http_reason = "This is a reason"

    gcc.web_client.get.return_value = MockData()

    with pytest.raises(SystemError) as excinfo:
        gcc.get_container_build_status("123-434")

    assert type(excinfo.value) is SystemError


def test_register_function(gcc):
    gcc.web_client = mock.MagicMock()

    metadata = {"python_version": "3.11.3", "sdk_version": "2.3.3"}
    gcc.register_function(funk, metadata=metadata)

    a, _ = gcc.web_client.register_function.call_args
    func_data = a[0]
    assert isinstance(func_data, FunctionRegistrationData)
    assert func_data.function_code is not None
    assert isinstance(func_data.metadata, FunctionRegistrationMetadata)
    assert func_data.metadata.python_version == metadata["python_version"]
    assert func_data.metadata.sdk_version == metadata["sdk_version"]


@pytest.mark.parametrize("dep_arg", ["searchable", "function_name"])
def test_register_function_deprecated_args(gcc, dep_arg):
    gcc.web_client = mock.MagicMock()

    with pytest.deprecated_call() as pyt_wrn:
        gcc.register_function(funk, **{dep_arg: "foo"})

    warning = pyt_wrn.pop(DeprecationWarning)
    assert "deprecated" in str(warning).lower()
    assert dep_arg in str(warning)


def _docstring_test_case_no_docstring():
    pass


def _docstring_test_case_single_line():
    """This is a docstring"""


def _docstring_test_case_multi_line():
    """This is a docstring
    that spans multiple lines
    and those lines are indented
    """


def _docstring_test_case_real_world():
    """
    Register a task function with this Executor's cache.

    All function execution submissions (i.e., ``.submit()``) communicate which
    pre-registered function to execute on the endpoint by the function's
    identifier, the ``function_id``.  This method makes the appropriate API
    call to the Globus Compute web services to first register the task function, and
    then stores the returned ``function_id`` in the Executor's cache.

    In the standard workflow, ``.submit()`` will automatically handle invoking
    this method, so the common use-case will not need to use this method.
    However, some advanced use-cases may need to fine-tune the registration
    of a function and so may manually set the registration arguments via this
    method.

    If a function has already been registered (perhaps in a previous
    iteration), the upstream API call may be avoided by specifying the known
    ``function_id``.

    If a function already exists in the Executor's cache, this method will
    raise a ValueError to help track down the errant double registration
    attempt.

    :param fn: function to be registered for remote execution
    :param function_id: if specified, associate the ``function_id`` to the
        ``fn`` immediately, short-circuiting the upstream registration call.
    :param func_register_kwargs: all other keyword arguments are passed to
        the ``Client.register_function()``.
    :returns: the function's ``function_id`` string, as returned by
        registration upstream
    :raises ValueError: raised if a function has already been registered with
        this Executor
    """


@pytest.mark.parametrize(
    "func",
    [
        _docstring_test_case_no_docstring,
        _docstring_test_case_single_line,
        _docstring_test_case_multi_line,
        _docstring_test_case_real_world,
    ],
)
def test_register_function_docstring(gcc, func):
    gcc.web_client = mock.MagicMock()

    gcc.register_function(func)
    expected = inspect.getdoc(func)

    a, _ = gcc.web_client.register_function.call_args
    func_data = a[0]
    assert func_data.description == expected


def test_register_function_no_metadata(gcc):
    gcc.web_client = mock.MagicMock()

    gcc.register_function(funk)

    a, _ = gcc.web_client.register_function.call_args
    func_data = a[0]
    assert isinstance(func_data, FunctionRegistrationData)
    assert func_data.metadata is None


def test_register_function_no_function(gcc):
    gcc.web_client = mock.MagicMock()

    with pytest.raises(ValueError) as pyt_exc:
        gcc.register_function(None)

    assert "either" in str(pyt_exc).lower()
    assert "'function'" in str(pyt_exc).lower()
    assert "'function_name'" in str(pyt_exc).lower()
    assert "'function_code'" in str(pyt_exc).lower()


def test_function_registration_data_function():
    frd = FunctionRegistrationData(function=funk)

    assert frd.function_name == "funk"
    assert frd.function_code == _get_packed_code(funk)


def test_function_registration_data_function_name_and_code():
    frd = FunctionRegistrationData(
        function=None, function_name="foo", function_code="bar"
    )

    assert frd.function_name == "foo"
    assert frd.function_code == "bar"


@pytest.mark.parametrize(
    "function_name, function_code", [("foo", None), (None, "bar"), (None, None)]
)
def test_function_registration_data_must_have_both_function_name_and_function_code(
    function_name, function_code
):
    with pytest.raises(ValueError) as pyt_exc:
        FunctionRegistrationData(
            function=None, function_name=function_name, function_code=function_code
        )

    assert "either" in str(pyt_exc).lower()
    assert "'function'" in str(pyt_exc).lower()
    assert "'function_name'" in str(pyt_exc).lower()
    assert "'function_code'" in str(pyt_exc).lower()


def test_function_registration_data_cant_have_both_function_and_name_code(randomstring):
    with pytest.raises(ValueError) as pyt_exc:
        FunctionRegistrationData(
            function=funk, function_name=randomstring(), function_code=randomstring()
        )

    assert "cannot specify" in str(pyt_exc).lower()
    assert "'function'" in str(pyt_exc).lower()
    assert "'function_name'" in str(pyt_exc).lower()
    assert "'function_code'" in str(pyt_exc).lower()


def test_get_function(gcc):
    func_uuid_str = str(uuid.uuid4())
    gcc.web_client = mock.MagicMock()

    gcc.get_function(func_uuid_str)

    gcc.web_client.get_function.assert_called_with(func_uuid_str)


def test_get_allowed_functions(gcc):
    ep_uuid_str = str(uuid.uuid4())
    gcc.web_client = mock.MagicMock()

    gcc.get_allowed_functions(ep_uuid_str)

    gcc.web_client.get_allowed_functions.assert_called_with(ep_uuid_str)


def test_delete_function(gcc):
    func_uuid_str = str(uuid.uuid4())
    gcc.web_client = mock.MagicMock()

    gcc.delete_function(func_uuid_str)

    gcc.web_client.delete_function.assert_called_with(func_uuid_str)


def test_missing_task_info(gcc):
    tid1, tid2 = str(uuid.uuid4()), str(uuid.uuid4())
    tid1_reason = "XYZ tid1"

    mock_resp = {
        "response": "batch",
        "results": {
            tid1: {
                "task_id": tid1,
                "status": "failed",
                "reason": tid1_reason,
            },
            tid2: {
                "task_id": tid2,
                "status": "failed",
            },
        },
    }
    gcc.web_client.get_batch_status.return_value = mock_resp

    res = gcc.get_batch_result([tid1, tid2])  # dev note: gcc.gbr, not gcc.web_client

    assert tid1 in res
    assert res[tid1]["pending"] is False
    assert res[tid1]["reason"] == tid1_reason
    assert tid2 in res
    assert res[tid2]["reason"] == "unknown"


@pytest.mark.parametrize(
    ("worker_dill", "worker_py", "should_warn"),
    [
        # None means use the same dill/python version as the SDK
        # Impossible dill versions should always warn
        [None, None, False],
        ["000.dill", "1.1.8", True],
        ["000.dill", None, True],
        [None, "1.1.8", True],
    ],
)
def test_version_mismatch_from_details(
    mocker,
    gcc,
    mock_response,
    worker_dill,
    worker_py,
    should_warn,
):
    # Returned env same as client by default
    sdk_details = get_env_details()
    sdk_py = sdk_details["python_version"]

    if worker_dill is None:
        worker_dill = sdk_details["dill_version"]

    task_py = worker_py if worker_py else sdk_py

    tid = str(uuid.uuid4())
    result = "some data"
    returned_task = {
        "task_id": tid,
        "status": "success",
        "result": gcc.fx_serializer.serialize(result),
        "completion_t": "1677183605.212898",
        "details": {
            "os": "Linux-5.19.0-1025-aws-x86_64-with-glibc2.35",
            "dill_version": worker_dill,
            "python_version": task_py,
            "globus_compute_sdk_version": "2.3.2",
            "task_transitions": {
                "execution-start": 1692742841.843334,
                "execution-end": 1692742846.123456,
            },
        },
    }

    mock_warn = mocker.patch("globus_compute_sdk.sdk.client.warnings")
    gcc.web_client.get_task.return_value = mock_response(200, returned_task)

    assert gcc.get_result(tid) == result

    assert mock_warn.warn.called == should_warn
    if should_warn:
        a, *_ = mock_warn.warn.call_args
        assert "Environment differences detected" in a[0]
        assert f"Workers: Python {task_py}/Dill {worker_dill}" in a[0]


@pytest.mark.parametrize("should_fail", [True, False])
def test_version_mismatch_warns_on_failure_and_success(
    mocker, gcc, mock_response, should_fail
):
    tid = str(uuid.uuid4())
    result = "some data"
    stack_trace = "Some Stack Trace\n  Line 40 stack trace"
    returned_task = {
        "task_id": tid,
        "status": "success",
        "completion_t": "1677183605.212898",
        "details": {
            "os": "Linux-5.19.0-1025-aws-x86_64-with-glibc2.35",
            "python_version": "python-bad-version",
            "dill_version": "dill-bad-version",
            "globus_compute_sdk_version": "2.3.2",
            "task_transitions": {
                "execution-start": 1692742841.843334,
                "execution-end": 1692742846.123456,
            },
        },
    }
    if should_fail:
        returned_task["exception"] = stack_trace
    else:
        returned_task["result"] = gcc.fx_serializer.serialize(result)

    mock_warn = mocker.patch("globus_compute_sdk.sdk.client.warnings")
    gcc.web_client.get_task.return_value = mock_response(200, returned_task)

    if should_fail:
        with pytest.raises(TaskExecutionFailed) as exc_info:
            gcc.get_task(tid)
        assert "Some Stack Trace" in str(exc_info)
    else:
        task_res = gcc.get_result(tid)
        assert task_res == result

    assert mock_warn.warn.called
    assert "Environment differences detected" in mock_warn.warn.call_args[0][0]


@pytest.mark.parametrize(
    "ep_ids",
    [
        [
            "00000000-1111-2222-3333-444444444444",
            "00000000-1111-2222-3333-444444444444",
        ],
        [
            "00000000-1111-2222-3333-444444444444",
            "00000000-1111-2222-3333-444444444444",
            "00000000-1111-2222-3333-555555555555",
        ],
        [
            "00000000-1111-2222-3333-444444444444",
            "00000000-1111-2222-3333-555555555555",
            "00000000-1111-2222-3333-666666666666",
        ],
    ],
)
def test_version_mismatch_only_warns_once_per_ep(mocker, gcc, mock_response, ep_ids):
    result = "some data"
    returned_task = {
        "status": "success",
        "result": gcc.fx_serializer.serialize(result),
        "completion_t": "1677183605.212898",
        "exception": "Some Stack Trace\n  Line 40 stack trace",
        "details": {
            "os": "Linux-5.19.0-1025-aws-x86_64-with-glibc2.35",
            "python_version": "python-bad-version",
            "dill_version": "dill-bad-version",
            "globus_compute_sdk_version": "2.3.2",
            "task_transitions": {
                "execution-start": 1692742841.843334,
                "execution-end": 1692742846.123456,
            },
        },
    }

    mock_warn = mocker.patch("globus_compute_sdk.sdk.client.warnings")

    for ep_id in ep_ids:
        tid = str(uuid.uuid4())
        returned_task["task_id"] = tid
        returned_task["details"]["endpoint_id"] = ep_id
        gcc.web_client.get_task.return_value = mock_response(200, returned_task)

        assert gcc.get_result(tid) == result

    assert mock_warn.warn.call_count == len(set(ep_ids))


def test_client_globus_app_and_login_manager_mutually_exclusive():
    app = mock.Mock(spec=UserApp)
    login_manager = mock.Mock(spec=LoginManager)
    with pytest.raises(ValueError) as excinfo:
        gc.Client(do_version_check=False, login_manager=login_manager, app=app)
    assert "'app' and 'login_manager' are mutually exclusive" in str(excinfo.value)


@pytest.mark.parametrize("custom_app", [True, False])
def test_client_handles_globus_app(
    custom_app: bool, mocker: MockerFixture, randomstring
):
    mock_auth_client = mocker.patch(f"{_MOCK_BASE}ComputeAuthClient")
    mock_auth_client.return_value = mock.Mock(spec=ComputeAuthClient)
    mock_web_client = mocker.patch(f"{_MOCK_BASE}WebClient")
    mock_web_client.return_value = mock.Mock(spec=WebClient)
    mock_get_globus_app = mocker.patch(f"{_MOCK_BASE}get_globus_app")

    mock_app = mock.Mock(spec=UserApp)
    env = randomstring()
    kwargs = {"environment": env}
    if custom_app:
        kwargs["app"] = mock_app
    else:
        # Client will default to using a GlobusApp
        mock_get_globus_app.return_value = mock_app

    client = gc.Client(do_version_check=False, **kwargs)

    if custom_app:
        mock_get_globus_app.assert_not_called()
    else:
        mock_get_globus_app.assert_called_once_with(environment=env)

    assert client.app is mock_app
    assert client.web_client is mock_web_client.return_value
    mock_web_client.assert_called_once_with(
        base_url=client.web_service_address, app=mock_app
    )
    assert client.auth_client is mock_auth_client.return_value
    mock_auth_client.assert_called_once_with(app=mock_app)


def test_client_handles_login_manager():
    mock_lm = mock.Mock(spec=LoginManager)
    client = gc.Client(do_version_check=False, login_manager=mock_lm)
    assert client.login_manager is mock_lm
    assert mock_lm.get_auth_client.call_count == 1
    assert mock_lm.get_web_client.call_count == 1
    assert mock_lm.get_web_client.call_args[1]["base_url"] == client.web_service_address


def test_client_logout_with_app(mocker):
    mocker.patch(f"{_MOCK_BASE}ComputeAuthClient")
    mocker.patch(f"{_MOCK_BASE}WebClient")
    mock_app = mock.Mock(spec=UserApp)
    client = gc.Client(do_version_check=False, app=mock_app)
    client.logout()
    assert mock_app.logout.called


def test_client_logout_with_login_manager():
    mock_lm = mock.Mock(spec=LoginManager)
    client = gc.Client(do_version_check=False, login_manager=mock_lm)
    client.logout()
    assert mock_lm.logout.called
