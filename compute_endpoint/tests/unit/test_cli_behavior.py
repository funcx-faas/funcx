from __future__ import annotations

import json
import logging
import os
import pathlib
import random
import shlex
import sys
import typing as t
import uuid
from unittest import mock

import globus_sdk
import pytest
import yaml
from click import ClickException
from click.testing import CliRunner
from globus_compute_endpoint.cli import (
    _AUTH_POLICY_DEFAULT_DESC,
    _AUTH_POLICY_DEFAULT_NAME,
    _do_login,
    _do_logout_endpoints,
    app,
    create_or_choose_auth_project,
    get_globus_app_with_scopes,
    init_config_dir,
)
from globus_compute_endpoint.endpoint.config import UserEndpointConfig
from globus_compute_endpoint.endpoint.config.utils import load_config_yaml
from globus_compute_endpoint.endpoint.endpoint import Endpoint
from globus_compute_endpoint.engines import ThreadPoolEngine
from globus_compute_endpoint.logging_config import setup_logging
from globus_compute_sdk.sdk.auth.auth_client import ComputeAuthClient
from globus_compute_sdk.sdk.auth.globus_app import UserApp
from globus_compute_sdk.sdk.compute_dir import ensure_compute_dir
from globus_compute_sdk.sdk.web_client import WebClient
from globus_sdk import MISSING, AuthClient
from pytest_mock import MockFixture

_MOCK_BASE = "globus_compute_endpoint.cli."


@pytest.fixture
def funcx_dir_path(tmp_path):
    yield tmp_path / "funcx-dir"


@pytest.fixture
def ep_name(randomstring):
    yield randomstring()


@pytest.fixture
def mock_app(mocker: MockFixture):
    mock_app = mock.Mock(spec=UserApp)
    mocker.patch(f"{_MOCK_BASE}get_globus_app_with_scopes", return_value=mock_app)
    return mock_app


@pytest.fixture
def mock_auth_client(mocker: MockFixture):
    mock_auth_client = mock.Mock(spec=ComputeAuthClient)
    mocker.patch(f"{_MOCK_BASE}ComputeAuthClient", return_value=mock_auth_client)
    return mock_auth_client


@pytest.fixture
def mock_command_ensure(funcx_dir_path):
    with mock.patch(f"{_MOCK_BASE}CommandState.ensure") as m_state:
        mock_state = mock.Mock()
        mock_state.endpoint_config_dir = funcx_dir_path
        m_state.return_value = mock_state

        yield mock_state


@pytest.fixture
def mock_cli_state(funcx_dir_path, mock_command_ensure, ep_name):
    with mock.patch(f"{_MOCK_BASE}Endpoint") as mock_ep:
        mock_ep.return_value = mock_ep
        mock_ep.get_endpoint_by_name_or_uuid.return_value = (
            mock_command_ensure.endpoint_config_dir / ep_name
        )
        yield mock_ep, mock_command_ensure


@pytest.fixture
def make_endpoint_dir(mock_command_ensure, ep_name):
    def func(name=ep_name, ep_uuid=None):
        ep_dir = mock_command_ensure.endpoint_config_dir / name
        ep_dir.mkdir(parents=True, exist_ok=True)
        if ep_uuid is not None:
            ep_json = ep_dir / "endpoint.json"
            ep_json.write_text(json.dumps({"endpoint_id": ep_uuid}))
        ep_config = Endpoint._config_file_path(ep_dir)
        ep_template = Endpoint.user_config_template_path(ep_dir)
        ep_config.write_text(
            """
display_name: null
engine:
    type: GlobusComputeEngine
    address: 127.0.0.1
    provider:
        type: LocalProvider
        init_blocks: 1
        min_blocks: 0
        max_blocks: 1
            """
        )
        ep_template.write_text(
            """
heartbeat_period: {{ heartbeat }}
engine:
    type: GlobusComputeEngine
    address: 127.0.0.1
    provider:
        type: LocalProvider
        init_blocks: 1
        min_blocks: 0
        max_blocks: 1
            """
        )
        return ep_dir

    return func


@pytest.fixture
def cli_runner():
    return CliRunner(mix_stderr=False)


@pytest.fixture
def run_line(cli_runner):
    def func(argline, *, assert_exit_code: int | None = 0, stdin=None):
        args = shlex.split(argline) if isinstance(argline, str) else argline

        if stdin is None:
            stdin = "{}"  # silence some logs; incurred by invoke's sys.stdin choice
        result = cli_runner.invoke(app, args, input=stdin)
        setup_logging()
        if assert_exit_code is not None:
            assert result.exit_code == assert_exit_code, (result.stdout, result.stderr)
        return result

    return func


@pytest.mark.parametrize("dir_exists", [True, False])
@pytest.mark.parametrize("user_dir", ["/my/dir", None, ""])
def test_init_config_dir(fs, dir_exists, user_dir):
    config_dirname = pathlib.Path.home() / ".globus_compute"

    if dir_exists:
        fs.create_dir(config_dirname)

    if user_dir is not None:
        config_dirname = pathlib.Path(user_dir)
        with mock.patch.dict(
            os.environ, {"GLOBUS_COMPUTE_USER_DIR": str(config_dirname)}
        ):
            dirname = init_config_dir()
    else:
        dirname = init_config_dir()

    assert dirname == config_dirname


def test_init_config_dir_file_conflict(fs):
    filename = pathlib.Path.home() / ".globus_compute"
    fs.create_file(filename)

    with pytest.raises(ClickException) as exc:
        init_config_dir()

    assert "Error creating directory" in str(exc)


def test_init_config_dir_permission_error(fs):
    parent_dirname = pathlib.Path("/parent/dir/")
    config_dirname = parent_dirname / "config"

    fs.create_dir(parent_dirname)
    os.chmod(parent_dirname, 0o000)

    with pytest.raises(ClickException) as exc:
        with mock.patch.dict(
            os.environ, {"GLOBUS_COMPUTE_USER_DIR": str(config_dirname)}
        ):
            init_config_dir()

    assert "Permission denied" in str(exc)


def test_get_globus_app_with_scopes(mocker: MockFixture):
    mock_stdin = mocker.patch.object(sys, "stdin")
    mock_stdin.isatty.return_value = True
    mock_stdin.closed = False

    app = get_globus_app_with_scopes()

    scopes = []
    for scope_list in app.scope_requirements.values():
        for scope in scope_list:
            scopes.append(str(scope))
    app.config.token_storage.close()  # is a SQLiteStorage object

    assert len(scopes) > 0
    assert all(str(s) in scopes for s in WebClient.default_scope_requirements)
    assert all(str(s) in scopes for s in ComputeAuthClient.default_scope_requirements)


@pytest.mark.parametrize("exception_type", [ValueError, RuntimeError])
def test_get_globus_app_with_scopes_handles_exception(
    exception_type: Exception, mocker: MockFixture
):
    mocker.patch(
        f"{_MOCK_BASE}get_globus_app", side_effect=exception_type("Test exception")
    )
    with pytest.raises(ClickException) as exc:
        get_globus_app_with_scopes()
    assert "Test exception" in str(exc)


def test_start_ep_corrupt(run_line, mock_cli_state, make_endpoint_dir, ep_name):
    make_endpoint_dir()
    mock_ep, mock_state = mock_cli_state
    conf = mock_state.endpoint_config_dir / ep_name / "config.yaml"
    conf.unlink()
    res = run_line(f"start {ep_name}", assert_exit_code=1)
    assert "corrupted?" in res.stderr


def test_start_endpoint_no_such_ep(run_line, mock_cli_state, ep_name):
    res = run_line(f"start {ep_name}", assert_exit_code=1)
    mock_ep, _ = mock_cli_state
    mock_ep.start_endpoint.assert_not_called()
    assert "no endpoint configuration on this machine at " in res.stderr
    assert ep_name in res.stderr


def test_start_endpoint_existing_ep(
    run_line, mock_cli_state, make_endpoint_dir, ep_name
):
    make_endpoint_dir()
    run_line(f"start {ep_name}")
    mock_ep, _ = mock_cli_state
    mock_ep.start_endpoint.assert_called_once()


@pytest.mark.parametrize("cli_cmd", ["configure"])
def test_endpoint_uuid_name_not_supported(run_line, cli_cmd):
    ep_uuid_name = uuid.uuid4()
    res = run_line(f"{cli_cmd} {ep_uuid_name}", assert_exit_code=2)
    assert (
        cli_cmd in res.stderr
        and "requires an endpoint name that is not a UUID" in res.stderr
    )


@pytest.mark.parametrize(
    "stdin_data",
    [
        (False, "..."),
        (False, "()"),
        (False, json.dumps([1, 2, 3])),
        (False, json.dumps("abc")),
        (True, "{}"),
        (True, json.dumps({"amqp_creds": {}})),
        (True, json.dumps({"config": "myconfig"})),
        (True, json.dumps({"amqp_creds": {}, "config": ""})),
        (True, json.dumps({"amqp_creds": {"a": 1}, "config": "myconfig"})),
        (True, json.dumps({"amqp_creds": {}, "config": "myconfig"})),
    ],
)
def test_start_ep_reads_stdin(
    mocker, run_line, mock_cli_state, make_endpoint_dir, stdin_data, ep_name
):
    data_is_valid, data = stdin_data

    conf = UserEndpointConfig(executors=[ThreadPoolEngine])
    mock_load_conf = mocker.patch(f"{_MOCK_BASE}load_config_yaml")
    mock_load_conf.return_value = conf
    mock_get_config = mocker.patch(f"{_MOCK_BASE}get_config")
    mock_get_config.return_value = conf

    mock_log = mocker.patch(f"{_MOCK_BASE}log")
    mock_sys = mocker.patch(f"{_MOCK_BASE}sys")
    mock_sys.stdin.closed = False
    mock_sys.stdin.isatty.return_value = False
    mock_sys.stdin.read.return_value = data

    make_endpoint_dir()

    run_line(f"start {ep_name}")
    mock_ep, _ = mock_cli_state
    assert mock_ep.start_endpoint.called
    reg_info_found = mock_ep.start_endpoint.call_args[0][5]

    if data_is_valid:
        data_dict = json.loads(data)
        reg_info = data_dict.get("amqp_creds", {})
        config_str = data_dict.get("config", None)

        assert reg_info_found == reg_info
        if config_str:
            config_str_found = mock_load_conf.call_args[0][0]
            assert config_str_found == config_str

    else:
        assert mock_get_config.called
        assert mock_log.debug.called
        a, k = mock_log.debug.call_args
        assert "Invalid info on stdin" in a[0]
        assert reg_info_found == {}


@pytest.mark.parametrize("fn_count", range(-1, 5))
def test_start_ep_stdin_allowed_fns_overrides_conf(
    mocker, run_line, mock_cli_state, make_endpoint_dir, ep_name, fn_count
):
    if fn_count == -1:
        allowed_fns = None
    else:
        allowed_fns = tuple(str(uuid.uuid4()) for _ in range(fn_count))

    conf = UserEndpointConfig(executors=[ThreadPoolEngine])
    conf.allowed_functions = [uuid.uuid4() for _ in range(5)]  # to be overridden
    mock_get_config = mocker.patch(f"{_MOCK_BASE}get_config")
    mock_get_config.return_value = conf

    mock_sys = mocker.patch(f"{_MOCK_BASE}sys")
    mock_sys.stdin.closed = False
    mock_sys.stdin.isatty.return_value = False
    mock_sys.stdin.read.return_value = json.dumps({"allowed_functions": allowed_fns})

    make_endpoint_dir()

    run_line(f"start {ep_name}")
    mock_ep, _ = mock_cli_state
    assert mock_ep.start_endpoint.called
    (_, _, found_conf, *_), _k = mock_ep.start_endpoint.call_args
    assert found_conf.allowed_functions == allowed_fns, "allowed field not overridden!"


@pytest.mark.parametrize("use_uuid", (True, False))
@mock.patch(f"{_MOCK_BASE}get_config")
def test_stop_endpoint(
    get_config,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    use_uuid,
):
    ep_uuid = str(uuid.uuid4()) if use_uuid else None
    make_endpoint_dir(ep_uuid=ep_uuid)
    run_line(f"stop {ep_uuid if use_uuid else ep_name}")
    mock_ep, _ = mock_cli_state
    mock_ep.stop_endpoint.assert_called_once()


def test_restart_endpoint_does_start_and_stop(
    run_line, mock_cli_state, make_endpoint_dir, ep_name
):
    make_endpoint_dir()
    run_line(f"restart {ep_name}")

    mock_ep, _ = mock_cli_state
    mock_ep.stop_endpoint.assert_called_once()
    mock_ep.start_endpoint.assert_called_once()


@mock.patch(f"{_MOCK_BASE}setup_logging")
def test_debug_configurable(mock_setup_log, run_line, mock_cli_state, ep_name):
    mock_ep, mock_ensure = mock_cli_state

    mock_ensure.debug = False
    ep_dir = mock_ensure.endpoint_config_dir / ep_name
    ep_dir.mkdir(parents=True)
    config = {"debug": False, "engine": {"type": "ThreadPoolEngine"}}
    data = {"config": yaml.safe_dump(config)}

    run_line(f"start {ep_name}", stdin=json.dumps(data), assert_exit_code=None)

    _a, k = mock_setup_log.call_args
    assert mock_ensure.debug is False, "Verify test setup"
    assert "debug" in k
    assert k["debug"] is False, "Null test: stays false"

    mock_setup_log.reset_mock()
    config["debug"] = True
    data["config"] = yaml.safe_dump(config)

    run_line(f"start {ep_name}", stdin=json.dumps(data), assert_exit_code=None)

    _a, k = mock_setup_log.call_args
    assert mock_ensure.debug is False, "Verify test setup"
    assert "debug" in k
    assert k["debug"] is True, "Expect config sets debug"


@mock.patch(f"{_MOCK_BASE}setup_logging")
def test_cli_debug_overrides_config(mock_setup_log, run_line, mock_cli_state, ep_name):
    mock_ep, mock_ensure = mock_cli_state

    mock_ensure.debug = True
    ep_dir = mock_ensure.endpoint_config_dir / ep_name
    ep_dir.mkdir(parents=True)
    config = {"debug": False, "engine": {"type": "ThreadPoolEngine"}}
    data = {"config": yaml.safe_dump(config)}

    run_line(f"start {ep_name}", stdin=json.dumps(data), assert_exit_code=None)

    _a, k = mock_setup_log.call_args
    assert mock_ensure.debug is True, "Verify test setup"
    assert "debug" in k
    assert k["debug"] is True, "Expect --debug flag overrides config"


def test_configure_validates_name(mock_command_ensure, run_line):
    compute_dir = mock_command_ensure.endpoint_config_dir
    compute_dir.mkdir(parents=True, exist_ok=True)

    run_line("configure ValidName")
    run_line("configure 'Invalid name with spaces'", assert_exit_code=1)


@pytest.mark.parametrize(
    "display_test",
    [
        ["ep0", None],
        ["ep1", "ep/ .1"],
        ["ep2", "abc 😎 /.great"],
    ],
)
def test_start_ep_display_name_in_config(
    run_line, mock_command_ensure, make_endpoint_dir, display_test
):
    ep_name, display_name = display_test

    conf = mock_command_ensure.endpoint_config_dir / ep_name / "config.yaml"
    configure_arg = ""
    if display_name is not None:
        configure_arg = f" --display-name '{display_name}'"
    run_line(f"configure {ep_name}{configure_arg}")

    with open(conf) as f:
        conf_dict = yaml.safe_load(f)

    assert conf_dict["display_name"] == display_name


@pytest.mark.parametrize(
    ("ep_name", "set_ha"),
    (
        ["ep0", None],
        ["ep1", False],
        ["ep2", True],
    ),
)
def test_start_ep_high_assurance_in_config(
    run_line, mock_command_ensure, make_endpoint_dir, ep_name, set_ha
):
    conf = mock_command_ensure.endpoint_config_dir / ep_name / "config.yaml"
    configure_arg = ""
    auth_policy = str(uuid.uuid4())
    sub_id = str(uuid.uuid4())
    if set_ha is not None:
        configure_arg = (
            f" --subscription-id {sub_id} --auth-policy "
            f"{auth_policy} {'--high-assurance' if set_ha else ''}"
        )
    run_line(f"configure {ep_name}{configure_arg}")

    with open(conf) as f:
        conf_dict = yaml.safe_load(f)

    if set_ha is True:
        assert conf_dict["high_assurance"] == set_ha
    else:
        assert conf_dict.get("high_assurance", False) is False


def test_configure_ep_auth_policy_in_config(
    run_line, mock_command_ensure, make_endpoint_dir
):
    ep_name = "my-ep"
    auth_policy = str(uuid.uuid4())
    conf = mock_command_ensure.endpoint_config_dir / ep_name / "config.yaml"

    run_line(f"configure {ep_name} --auth-policy {auth_policy}")

    with open(conf) as f:
        conf_dict = yaml.safe_load(f)

    assert conf_dict["authentication_policy"] == auth_policy


def test_configure_ep_subscription_id_in_config(
    run_line, mock_command_ensure, make_endpoint_dir
):
    ep_name = "my-ep"
    subscription_id = str(uuid.uuid4())
    conf = mock_command_ensure.endpoint_config_dir / ep_name / "config.yaml"

    run_line(f"configure {ep_name} --subscription-id {subscription_id}")

    with open(conf) as f:
        conf_dict = yaml.safe_load(f)

    assert conf_dict["subscription_id"] == subscription_id


@pytest.mark.parametrize("display_name", [None, "None"])
def test_config_yaml_display_none(run_line, mock_command_ensure, display_name):
    ep_name = "test_display_none"

    conf = mock_command_ensure.endpoint_config_dir / ep_name / "config.yaml"

    config_cmd = f"configure {ep_name}"
    if display_name is not None:
        config_cmd += f" --display-name {display_name}"
    run_line(config_cmd)

    conf_dict = dict(yaml.safe_load(conf.read_text()))
    conf_dict["engine"]["address"] = "::1"  # avoid unnecessary DNS lookup
    conf = load_config_yaml(yaml.safe_dump(conf_dict))

    assert conf.display_name is None, conf.display_name


def test_start_ep_incorrect_config_yaml(
    run_line, mock_cli_state, make_endpoint_dir, ep_name
):
    make_endpoint_dir()
    mock_ep, mock_state = mock_cli_state
    conf = mock_state.endpoint_config_dir / ep_name / "config.yaml"

    conf.write_text("asdf")
    res = run_line(f"start {ep_name}", assert_exit_code=1)
    assert "Invalid config syntax" in res.stderr


def test_start_ep_incorrect_config_py(
    run_line, mock_cli_state, make_endpoint_dir, ep_name
):
    make_endpoint_dir()
    mock_ep, mock_state = mock_cli_state
    conf = mock_state.endpoint_config_dir / ep_name / "config.py"

    conf.write_text("asa asd df = 5")  # fail the import
    with mock.patch(f"{_MOCK_BASE}log"):
        with mock.patch(
            "globus_compute_endpoint.endpoint.config.utils.log"
        ) as mock_util_log:
            res = run_line(f"start {ep_name}", assert_exit_code=1)
    a, _ = mock_util_log.exception.call_args
    assert "might be out of date" in a[0]
    assert isinstance(res.exception, SyntaxError)

    # `coverage` demands a valid syntax file.  FBOW, then, the ordering and
    # commingling of these two tests is intentional.  Bit of a meta problem ...
    conf.unlink()
    conf.write_text("asdf = 5")  # syntactically correct
    res = run_line(f"start {ep_name}", assert_exit_code=1)
    assert "modified incorrectly?" in res.stderr


@mock.patch("globus_compute_endpoint.endpoint.config.utils.load_config_yaml")
def test_start_ep_config_py_takes_precedence(
    load_config_yaml, run_line, mock_cli_state, make_endpoint_dir, ep_name
):
    ep_dir = make_endpoint_dir()
    conf_py = ep_dir / "config.py"
    mock_ep, *_ = mock_cli_state
    conf_py.write_text(
        "from globus_compute_endpoint.endpoint.config import UserEndpointConfig"
        "\nfrom globus_compute_endpoint.engines import ThreadPoolEngine"
        "\nconfig = UserEndpointConfig(executors=[ThreadPoolEngine()])"
    )

    run_line(f"start {ep_name}")
    assert mock_ep.start_endpoint.called
    assert not load_config_yaml.called, "Key outcome: config.py takes precedence"


def test_single_user_requires_engine_configured(mock_command_ensure, ep_name, run_line):
    ep_dir = mock_command_ensure.endpoint_config_dir / ep_name
    ep_dir.mkdir(parents=True)
    data = {"config": ""}

    config = {}
    data["config"] = yaml.safe_dump(config)
    rc = run_line(f"start {ep_name}", stdin=json.dumps(data), assert_exit_code=1)
    assert "validation error" in rc.stderr
    assert "engine\n  field required" in rc.stderr

    config = {"multi_user": False}
    data["config"] = yaml.safe_dump(config)
    rc = run_line(f"start {ep_name}", stdin=json.dumps(data), assert_exit_code=1)
    assert "validation error" in rc.stderr
    assert "engine\n  field required" in rc.stderr


def test_multi_user_config_enforces_no_engine(mock_command_ensure, ep_name, run_line):
    ep_dir = mock_command_ensure.endpoint_config_dir / ep_name
    ep_dir.mkdir(parents=True)
    data = {"config": ""}

    config = {"engine": {"type": "ThreadPoolEngine"}, "multi_user": True}
    data["config"] = yaml.safe_dump(config)
    rc = run_line(f"start {ep_name}", stdin=json.dumps(data), assert_exit_code=1)

    assert "validation error" in rc.stderr, (rc.stdout, rc.stderr)
    assert "engine\n" in rc.stderr, (rc.stdout, rc.stderr)


@pytest.mark.parametrize("use_uuid", (True, False))
@mock.patch(f"{_MOCK_BASE}get_config")
@mock.patch(f"{_MOCK_BASE}Endpoint.get_endpoint_id")
def test_delete_endpoint(
    get_endpoint_id,
    get_config,
    run_line,
    mock_cli_state,
    ep_name,
    ep_uuid,
    make_endpoint_dir,
    use_uuid,
):
    get_endpoint_id.return_value = ep_uuid

    make_endpoint_dir(ep_uuid=ep_uuid)
    run_line(f"delete {ep_uuid if use_uuid else ep_name} --yes")
    mock_ep, _ = mock_cli_state
    mock_ep.delete_endpoint.assert_called_once()
    assert mock_ep.delete_endpoint.call_args[1]["ep_uuid"] == ep_uuid
    if use_uuid:
        get_endpoint_id.assert_not_called()
    else:
        get_endpoint_id.assert_called()


@mock.patch("globus_compute_endpoint.endpoint.endpoint.Endpoint.get_funcx_client")
def test_delete_endpoint_with_malformed_config_sc28515(
    mock_func, fs, run_line, ep_name
):
    compute_dir = ensure_compute_dir()
    conf_dir = compute_dir / ep_name
    conf_dir.mkdir()
    config = {"engine": {"type": "ThreadPoolEngine"}}
    (conf_dir / "config.yaml").write_text(yaml.safe_dump(config))
    assert conf_dir.exists() and conf_dir.is_dir()
    run_line(f"delete {ep_name} --yes --force")
    assert not conf_dir.exists()


@pytest.mark.parametrize("die_with_parent", [True, False])
@mock.patch(f"{_MOCK_BASE}get_config")
def test_die_with_parent_detached(
    mock_get_config,
    run_line,
    mock_cli_state,
    die_with_parent,
    ep_name,
    make_endpoint_dir,
):
    config = UserEndpointConfig(executors=[ThreadPoolEngine()])
    mock_get_config.return_value = config
    make_endpoint_dir()

    if die_with_parent:
        run_line(f"start {ep_name} --die-with-parent")
    else:
        run_line(f"start {ep_name}")
    assert config.detach_endpoint is (not die_with_parent)


def test_python_exec(mocker: MockFixture, run_line: t.Callable):
    mock_execvpe = mocker.patch("os.execvpe")
    run_line("python-exec path.to.module arg --option val")
    mock_execvpe.assert_called_with(
        sys.executable,
        [sys.executable, "-m", "path.to.module", "arg", "--option", "val"],
        os.environ,
    )


_test_name_or_uuid_decorator__data = [
    ("foo", str(uuid.uuid4())),
    ("123", str(uuid.uuid4())),
    ("nice_normal_name", str(uuid.uuid4())),
]


@pytest.mark.parametrize("name,uuid", _test_name_or_uuid_decorator__data)
def test_name_or_uuid_decorator(tmp_path, mocker, run_line, name, uuid):
    gc_conf_dir = tmp_path / ".globus_compute"
    gc_conf_dir.mkdir()
    for n, u in _test_name_or_uuid_decorator__data:
        ep_conf_dir = gc_conf_dir / n
        ep_conf_dir.mkdir()
        ep_json = ep_conf_dir / "endpoint.json"
        ep_json.write_text(json.dumps({"endpoint_id": u}))
        # dummy config.yaml so that Endpoint._get_ep_dirs finds this
        (ep_conf_dir / "config.yaml").write_text("")

    mock__do_start_endpoint = mocker.patch(f"{_MOCK_BASE}_do_start_endpoint")

    run_line(f"-c {gc_conf_dir} start {name}")
    run_line(f"-c {gc_conf_dir} start {uuid}")

    assert mock__do_start_endpoint.call_count == 2

    first_result, second_result = (
        call.kwargs["ep_dir"] for call in mock__do_start_endpoint.call_args_list
    )

    assert first_result == second_result

    assert first_result is not None
    assert second_result is not None


@pytest.mark.parametrize(
    "data",
    [
        ("foo", "no endpoint configuration on this machine"),
        (str(uuid.uuid4()), "no endpoint configuration on this machine with ID"),
    ],
)
def test_get_endpoint_by_name_or_uuid_error_message(run_line, data):
    value, error = data

    result = run_line(f"start {value}", assert_exit_code=1)

    assert error in result.stderr


@pytest.mark.parametrize(
    "data",
    [
        ("start", "start_endpoint", '{"error":"invalid_grant"}'),
        ("start", "start_endpoint", '{"error":"something else"}'),
        ("start", "start_endpoint", ""),
        ("stop", "stop_endpoint", "err_msg"),
        ("delete --yes", "delete_endpoint", "err_msg"),
    ],
)
def test_handle_globus_auth_error(
    mocker: MockFixture,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    data: tuple[str, str],
):
    cmd, ep_method, auth_err_msg = data
    mock_ep, _ = mock_cli_state
    make_endpoint_dir()

    mock_log = mocker.patch("globus_compute_endpoint.exception_handling.log")
    mock_resp = mock.MagicMock(
        status_code=400,
        reason="Bad Request",
        text=auth_err_msg,
    )
    mocker.patch.object(
        mock_ep,
        ep_method,
        side_effect=globus_sdk.AuthAPIError(r=mock_resp),
    )

    res = run_line(f"{cmd} {ep_name}", assert_exit_code=os.EX_NOPERM)

    err_msg = "An Auth API error occurred."
    a, k = mock_log.warning.call_args

    assert err_msg in res.stdout
    assert err_msg in a[0]
    assert "400" in res.stdout
    assert "400" in a[0]

    additional_details = "credentials may have expired"
    if "invalid_grant" in auth_err_msg:
        assert additional_details in res.stdout
    else:
        assert additional_details not in res.stdout


@pytest.mark.parametrize("exit_exc", (None, SystemExit(), SystemExit(0)))
def test_happy_path_exit_no_amqp_msg(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    exit_exc,
):
    mock_ep, _ = mock_cli_state
    mock_send = mocker.patch(f"{_MOCK_BASE}send_endpoint_startup_failure_to_amqp")
    make_endpoint_dir()

    stdin = json.dumps({"amqp_creds": {"some": "data"}})
    if exit_exc is not None:
        mock_ep.start_endpoint.side_effect = exit_exc
    run_line(f"start {ep_name}", assert_exit_code=0, stdin=stdin)
    assert mock_ep.start_endpoint.called
    assert not mock_send.called


@pytest.mark.parametrize(
    "ec,exit_exc",
    (
        (1, SystemExit("Death!")),
        (5, SystemExit(5)),
        (1, RuntimeError("fool!")),
        (1, MemoryError("Oh no!")),
        (1, AssertionError("mistake")),
        (1, Exception("Generally no good.")),
    ),
)
def test_fail_exit_sends_amqp_msg(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    ec,
    exit_exc,
):
    mock_ep, _ = mock_cli_state
    mock_send = mocker.patch(f"{_MOCK_BASE}send_endpoint_startup_failure_to_amqp")
    make_endpoint_dir()

    stdin = json.dumps({"amqp_creds": {"some": "data"}})
    mock_ep.start_endpoint.side_effect = exit_exc
    run_line(f"start {ep_name}", assert_exit_code=ec, stdin=stdin)
    assert mock_ep.start_endpoint.called
    assert mock_send.called


@pytest.mark.parametrize("force", [True, False])
@pytest.mark.parametrize("login_required", [True, False])
def test_login(
    force: bool,
    login_required: bool,
    caplog: pytest.LogCaptureFixture,
    mocker: MockFixture,
    mock_cli_state,
):
    mock_app = mock.Mock(spec=UserApp)
    mock_app.login_required.return_value = login_required
    mocker.patch(f"{_MOCK_BASE}get_globus_app_with_scopes", return_value=mock_app)
    caplog.set_level(logging.INFO)

    _do_login(force=force)

    if login_required or force:
        assert mock_app.login.call_count == 1
    else:
        assert mock_app.login.call_count == 0
        assert "Already logged in" in caplog.text


@pytest.mark.parametrize("force", [True, False], ids=["forced", "unforced"])
def test_login_handles_partial_client_login_state(monkeypatch, force):
    monkeypatch.setenv("GLOBUS_COMPUTE_CLIENT_SECRET", "some_uuid")
    with pytest.raises(ClickException) as e:
        _do_login(force)
    assert "both environment variables" in str(e)


@pytest.mark.parametrize("force", [True, False])
@pytest.mark.parametrize("running_endpoints", [{}, {"my_ep": {"status": "Running"}}])
def test_logout(
    force: bool,
    running_endpoints: dict,
    caplog: pytest.LogCaptureFixture,
    mocker: MockFixture,
    mock_cli_state,
):
    mock_app = mock.Mock(spec=UserApp)
    mocker.patch(f"{_MOCK_BASE}get_globus_app_with_scopes", return_value=mock_app)
    mocker.patch(
        f"{_MOCK_BASE}Endpoint.get_running_endpoints", return_value=running_endpoints
    )
    caplog.set_level(logging.INFO)

    _do_logout_endpoints(force=force)

    if running_endpoints and not force:
        assert "endpoints are currently running" in caplog.text
    elif not running_endpoints or force:
        assert mock_app.logout.call_count == 1
        assert "Logout succeeded" in caplog.text
    else:
        assert mock_app.logout.call_count == 0


@pytest.mark.parametrize("ap_project_id", [None, "foo"])
@pytest.mark.parametrize("ap_display_name", [None, "foo"])
@pytest.mark.parametrize("ap_description", [None, "foo"])
@pytest.mark.parametrize("ap_allowed", [None, "foo"])
@pytest.mark.parametrize("ap_exclude", [None, "foo"])
@pytest.mark.parametrize("ap_timeout", [None, "1"])
def test_configure_ep_auth_policy_mutually_exclusive(
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    ap_project_id,
    ap_display_name,
    ap_description,
    ap_allowed,
    ap_exclude,
    ap_timeout,
):
    params = "--auth-policy=foo"
    expected_exit_code = 0
    if ap_project_id:
        params += f" --auth-policy-project-id={ap_project_id}"
        expected_exit_code = 1
    if ap_display_name:
        params += f" --auth-policy-display-name={ap_display_name}"
        expected_exit_code = 1
    if ap_description:
        params += f" --auth-policy-description={ap_description}"
        expected_exit_code = 1
    if ap_allowed:
        params += f" --allowed-domains={ap_allowed}"
        expected_exit_code = 1
    if ap_exclude:
        params += f" --excluded-domains={ap_exclude}"
        expected_exit_code = 1
    if ap_timeout:
        params += f" --auth-timeout={ap_timeout}"
        expected_exit_code = 1

    res = run_line(f"configure {params} {ep_name}", assert_exit_code=expected_exit_code)

    if expected_exit_code == 1:
        assert "at the same time" in res.stderr


def test_configure_ep_auth_policy_defaults(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    mock_app: UserApp,
    mock_auth_client: ComputeAuthClient,
):
    mock_create_auth_policy = mocker.patch(f"{_MOCK_BASE}create_auth_policy")

    run_line(f"configure --auth-policy-project-id=foo {ep_name}")

    assert mock_create_auth_policy.call_args.kwargs == {
        "ac": mock_auth_client,
        "project_id": "foo",
        "display_name": _AUTH_POLICY_DEFAULT_NAME,
        "description": _AUTH_POLICY_DEFAULT_DESC,
        "include_domains": MISSING,
        "exclude_domains": MISSING,
        "timeout": MISSING,
    }


def test_configure_ep_auth_param_parse(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    mock_app: UserApp,
    mock_auth_client: ComputeAuthClient,
):
    mock_create_auth_policy = mocker.patch(f"{_MOCK_BASE}create_auth_policy")
    params = " ".join(
        [
            "--auth-policy-project-id=p123",
            "--auth-policy-display-name='my awesome policy'",
            "--auth-policy-description='policy desc'",
            "--allowed-domains=xyz.com,example.org",
            "--excluded-domains=nope.com",
            "--auth-timeout=30",
        ]
    )

    run_line(f"configure {params} {ep_name}")

    assert mock_create_auth_policy.call_args.kwargs == {
        "ac": mock_auth_client,
        "project_id": "p123",
        "display_name": "my awesome policy",
        "description": "policy desc",
        "include_domains": ["xyz.com", "example.org"],
        "exclude_domains": ["nope.com"],
        "timeout": 30,
    }


def test_choose_auth_project(
    mocker,
    randomstring,
):
    mock_user_input_select = mocker.patch(f"{_MOCK_BASE}user_input_select")
    mock_user_input_select.side_effect = lambda _p, o: random.choice(o)

    get_projects_response = [
        {"id": str(uuid.uuid4()), "display_name": randomstring()} for _ in range(5)
    ]
    mock_ac = mocker.Mock(spec=AuthClient)
    mock_ac.get_projects.return_value = get_projects_response

    proj_id = create_or_choose_auth_project(mock_ac)

    assert uuid.UUID(proj_id)
    assert proj_id in [p["id"] for p in get_projects_response]


@pytest.mark.parametrize("has_projects", [True, False])
def test_configure_ep_auth_policy_creates_or_chooses_project(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    mock_app: UserApp,
    mock_auth_client: ComputeAuthClient,
    randomstring,
    has_projects,
):
    class StopTest(Exception):
        pass

    if has_projects:
        mock_auth_client.get_projects.return_value = [
            {"id": str(uuid.uuid4()), "display_name": randomstring()} for _ in range(5)
        ]
        # stop test when choosing a project
        mocker.patch(f"{_MOCK_BASE}user_input_select", side_effect=StopTest)
    else:
        mock_auth_client.get_projects.return_value = []
        mock_input = mocker.patch("builtins.input")
        mock_input.return_value = "y"  # break out of interact loop asap
        # stop test when creating a project
        mock_auth_client.create_project.side_effect = StopTest

    res = run_line(
        f"configure --auth-policy-display-name=foo {ep_name}", assert_exit_code=1
    )

    assert isinstance(res.exception, StopTest)


@pytest.mark.parametrize("timeout", [None] + list(range(5)))
def test_configure_ep_auth_policy_timeout_sets_ha(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    mock_app: UserApp,
    mock_auth_client: ComputeAuthClient,
    timeout,
):
    mock_auth_client.create_policy.return_value = {"policy": {"id": "foo"}}

    if timeout is None:
        line = f"configure --auth-policy-project-id=bar {ep_name}"
    else:
        line = (
            f"configure --auth-policy-project-id=bar --auth-timeout={timeout} {ep_name}"
        )

    run_line(line)

    assert mock_auth_client.create_policy.called
    assert mock_auth_client.create_policy.call_args.kwargs["high_assurance"] == bool(
        timeout
    )


@pytest.mark.parametrize(
    ("is_ha", "policy_id", "auth_desc", "allowed_domains", "sub_id", "exc_text"),
    (
        (
            [True, "pid", None, None, "sub_id", None],
            [True, "pid", "desc", "globus.org", "sub_id", "Cannot specify an existing"],
            [True, "pid", "desc", None, "sub_id", "Cannot specify an existing"],
            [True, None, None, None, "sub_id", "require both a HA policy and a HA sub"],
            [True, "pid", None, None, None, "require both a HA policy and a HA sub"],
            [
                True,
                None,
                "auth_desc",
                "globus.org",
                None,
                "require both a HA policy and a HA sub",
            ],
            [False, None, "auth_desc", "globus.org", None, None],
            [False, "pid", None, None, None, None],
        )
    ),
)
def test_configure_ha_ep_requirements(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    mock_app,
    mock_auth_client,
    is_ha,
    policy_id,
    auth_desc,
    allowed_domains,
    sub_id,
    exc_text,
):
    mock_auth_client.create_policy.return_value = {"policy": {"id": "foo"}}
    mock_auth_client.get_projects.return_value = []
    mocker.patch(f"{_MOCK_BASE}create_or_choose_auth_project")

    mock_ep, _ = mock_cli_state

    args = ["configure"]
    if is_ha:
        args.append("--high-assurance")
    if policy_id:
        args.append(f"--auth-policy {policy_id}")
    if auth_desc:
        args.append(f"--auth-policy-description {auth_desc}")
    if allowed_domains:
        args.append(f"--allowed-domains {allowed_domains}")
    if sub_id:
        args.append(f"--subscription-id {sub_id}")

    args.append("ep_name")

    line = " ".join(args)
    if exc_text:
        res = run_line(line, assert_exit_code=1)
        assert exc_text in res.stderr
    else:
        run_line(line)
        assert mock_ep.configure_endpoint.called


@pytest.mark.parametrize(
    ("delete_cmd", "use_uuid", "exit_code", "delete_done"),
    [
        ("delete --yes {ep_info}", True, 0, True),
        ("delete --force {ep_info}", False, None, False),
        ("delete --yes --force {ep_info}", False, 0, True),
    ],
)
def test_delete_endpoint_local(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    ep_name,
    delete_cmd,
    use_uuid,
    exit_code,
    delete_done,
):
    mock_ep_cls = mocker.patch(f"{_MOCK_BASE}Endpoint")
    mocker.patch(f"{_MOCK_BASE}get_config")
    ep_info = str(uuid.uuid4()) if use_uuid else ep_name
    make_endpoint_dir(ep_uuid=ep_info if use_uuid else None)
    run_line(delete_cmd.format(ep_info=ep_info), assert_exit_code=exit_code)
    assert delete_done == bool(mock_ep_cls.delete_endpoint.called)


def test_delete_endpoint_local_uuid(
    mocker,
    run_line,
    mock_cli_state,
):
    mock_ep_cls = mocker.patch(f"{_MOCK_BASE}Endpoint")
    mock_ep_cls.get_endpoint_dir_by_uuid.return_value = None
    mocker.patch(f"{_MOCK_BASE}get_config")
    mocker.patch("click.confirm").return_value = True
    run_line(f"delete {uuid.uuid4()}", assert_exit_code=1)
    assert not mock_ep_cls.delete_endpoint.called


@pytest.mark.parametrize(
    ("delete_args", "err_msg"),
    [
        (
            "--yes fake_uuid",
            "no endpoint configuration",
        ),
    ],
)
def test_delete_endpoint_no_local_config(
    mocker,
    run_line,
    mock_cli_state,
    make_endpoint_dir,
    delete_args,
    err_msg,
):
    mock_ep_cls = mocker.patch(f"{_MOCK_BASE}Endpoint")
    line = f"delete --yes {delete_args}"
    result = run_line(line, assert_exit_code=1)
    assert not mock_ep_cls.delete_endpoint.called
    assert err_msg in result.stderr
