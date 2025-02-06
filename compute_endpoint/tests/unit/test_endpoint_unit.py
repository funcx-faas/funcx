from __future__ import annotations

import functools
import io
import json
import logging
import os
import pathlib
import random
import uuid
from collections import namedtuple
from contextlib import redirect_stdout
from datetime import datetime
from http import HTTPStatus
from types import SimpleNamespace
from unittest import mock

import pytest
import requests
from globus_compute_endpoint.endpoint import endpoint
from globus_compute_endpoint.endpoint.config import UserEndpointConfig
from globus_compute_endpoint.endpoint.config.utils import (
    get_config,
    load_user_config_template,
    render_config_user_template,
    serialize_config,
)
from globus_compute_endpoint.endpoint.endpoint import Endpoint
from globus_compute_endpoint.engines import (
    GlobusComputeEngine,
    ProcessPoolEngine,
    ThreadPoolEngine,
)
from globus_compute_sdk import Client
from globus_sdk import GlobusAPIError, NetworkError

_mock_base = "globus_compute_endpoint.endpoint.endpoint."

# bloody line length ...
_whitespace_msg = "no whitespace (spaces, newlines, tabs), slashes, or prefixed '.'"


@pytest.fixture
def mock_log():
    with mock.patch(f"{_mock_base}log", spec=logging.Logger) as m:
        m.getEffectiveLevel.return_value = logging.DEBUG
        yield m


@pytest.fixture
def mock_daemon():
    with mock.patch(f"{_mock_base}daemon") as m:
        yield m


@pytest.fixture
def mock_gcc(mock_reg_info):
    _gcc = mock.Mock(spec=Client)
    _gcc.register_endpoint.return_value = mock_reg_info
    return _gcc


@pytest.fixture
def mock_get_client(mock_gcc):
    with mock.patch(f"{_mock_base}Endpoint.get_funcx_client") as m:
        m.return_value = mock_gcc
        yield m


@pytest.fixture
def mock_launch():
    with mock.patch(f"{_mock_base}Endpoint.daemon_launch") as m:
        yield m


@pytest.fixture
def conf():
    _conf = UserEndpointConfig(executors=[ThreadPoolEngine])
    _conf.source_content = "# test source content"
    _conf.source_content += "\nengine:\n  type: ThreadPoolEngine"
    yield _conf


@pytest.fixture
def mock_ep_data(fs, conf):
    ep = endpoint.Endpoint()
    ep_dir = pathlib.Path("/some/path/mock_endpoint")
    ep_dir.mkdir(parents=True, exist_ok=True)
    log_to_console = False
    no_color = True
    yield ep, ep_dir, log_to_console, no_color, conf


@pytest.fixture
def mock_ep_buf():
    buf = io.StringIO()
    Endpoint.get_endpoints = mock.Mock()
    Endpoint.get_endpoints.return_value = {}

    Endpoint.print_endpoint_table = functools.partial(
        Endpoint.print_endpoint_table, conf_dir="unused", ofile=buf
    )
    yield buf


@pytest.fixture
def umask():
    orig_umask = os.umask(0)
    os.umask(orig_umask)

    def _wrapped_umask(new_umask: int | None) -> int:
        if new_umask is None:
            return orig_umask
        return os.umask(new_umask)

    yield _wrapped_umask

    os.umask(orig_umask)


@pytest.fixture
def uname(randomstring):
    return randomstring()


@pytest.fixture
def pword(randomstring):
    return randomstring()


@pytest.fixture
def mock_reg_info(ep_uuid, uname, pword):
    c_url = f"amqp://{uname}:{pword}@some.domain"
    yield {
        "endpoint_id": ep_uuid,
        "task_queue_info": {"connection_url": f"{c_url}:1234"},
        "result_queue_info": {"connection_url": c_url},
        "heartbeat_queue_info": {"connection_url": c_url},
    }


def test_start_endpoint_no_reg_provided_registers(
    mock_daemon, mock_launch, mock_log, mock_get_client, mock_ep_data, ep_uuid
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)

    ep_json_p = ep_dir / "endpoint.json"
    assert not ep_json_p.exists(), "Verify test setup"

    ep.start_endpoint(*ep_args, reg_info={}, ep_info={})
    assert mock_launch.called

    ep_data = json.load(ep_json_p.open())
    assert ep_data["endpoint_id"] == ep_uuid, "Expect id saved for reregistrations"


def test_endpoint_needs_no_client_if_reg_info(
    mock_get_client, mock_daemon, mock_launch, mock_ep_data, mock_reg_info, ep_uuid
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)

    ep.start_endpoint(*ep_args, reg_info=mock_reg_info, ep_info={})
    assert not mock_get_client.called, "No need for Client!"
    assert mock_launch.called, "Registration given; should start"

    mock_launch.reset_mock()
    ep.start_endpoint(*ep_args, reg_info={}, ep_info={})
    assert mock_get_client.called, "Need registration info, need Client"
    assert mock_launch.called, "Collects registration; should start"


def test_start_endpoint_redacts_url_creds_from_logs(
    mock_daemon,
    mock_launch,
    mock_log,
    mock_ep_data,
    mock_reg_info,
    ep_uuid,
    uname,
    pword,
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)
    ep.start_endpoint(*ep_args, reg_info=mock_reg_info, ep_info={})
    assert mock_launch.called, "Should launch successfully"

    debug_args = "\n".join(str((a, k)) for a, k in mock_log.debug.call_args_list)
    assert "Registration information: " in debug_args
    assert uname not in debug_args
    assert pword not in debug_args


def test_start_endpoint_populates_ep_static_info(
    mock_daemon, mock_ep_data, mock_reg_info, ep_uuid, randomstring
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)
    canary_value = randomstring()
    ep_info = {"canary": canary_value}
    with mock.patch(f"{_mock_base}Endpoint.daemon_launch") as mock_launch:
        ep.start_endpoint(*ep_args, reg_info=mock_reg_info, ep_info=ep_info)
    assert mock_launch.called, "Should launch successfully"

    (*_, found), _k = mock_launch.call_args
    assert found is ep_info
    assert found["canary"] == canary_value, "Should *add* data, no overwrite"

    found_start_iso = datetime.fromisoformat(found["start_iso"])
    found_start_unix = found["start_unix"]
    assert found_start_iso.tzinfo is not None, "Expect human readable time, host tz"
    assert found_start_iso.timestamp() == found_start_unix, "Expect unixtime variant"
    assert found["posix_uid"] == os.getuid()
    assert found["posix_gid"] == os.getgid()
    assert found["posix_groups"] == os.getgroups()
    assert found["posix_pid"] == os.getpid()
    assert found["posix_sid"] == os.getsid(os.getpid())
    assert found["config_raw"] == ep_conf.source_content


def test_start_endpoint_network_error(
    mock_log, mock_gcc, mock_get_client, mock_ep_data, ep_uuid
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)

    mock_gcc.register_endpoint.side_effect = NetworkError("foo", Exception)

    f = io.StringIO()
    f.isatty = lambda: True
    with redirect_stdout(f):
        with pytest.raises(SystemExit) as pytest_exc:
            ep.start_endpoint(*ep_args, reg_info={}, ep_info={})

    assert pytest_exc.value.code == os.EX_TEMPFAIL
    assert "exception while attempting" in mock_log.exception.call_args[0][0]
    assert "unable to reach the Globus Compute" in mock_log.critical.call_args[0][0]
    assert "unable to reach the Globus Compute" in f.getvalue()  # stdout


def test_delete_endpoint_network_error(
    mock_log, mock_gcc, mock_get_client, mock_ep_data, ep_uuid
):
    ep, ep_dir, *_, ep_conf = mock_ep_data

    mock_gcc.delete_endpoint.side_effect = NetworkError("foo", Exception)

    f = io.StringIO()
    with redirect_stdout(f):
        with pytest.raises(SystemExit) as pytest_exc:
            ep.delete_endpoint(ep_dir, ep_conf, ep_uuid=ep_uuid)

    assert pytest_exc.value.code == os.EX_TEMPFAIL
    assert mock_gcc.delete_endpoint.called, "Verify test: was kernel invoked?"
    assert "could not be deleted from the web" in mock_log.warning.call_args[0][0]
    assert "unable to reach the Globus Compute" in mock_log.critical.call_args[0][0]
    assert "unable to reach the Globus Compute" in f.getvalue()  # stdout


def test_register_endpoint_invalid_response(
    mock_log, ep_uuid, other_endpoint_id, mock_gcc, mock_get_client, mock_ep_data
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)

    mock_gcc.register_endpoint.return_value = {"endpoint_id": other_endpoint_id}

    with pytest.raises(SystemExit) as pytest_exc:
        ep.start_endpoint(*ep_args, reg_info={}, ep_info={})

    assert pytest_exc.value.code == os.EX_SOFTWARE
    a, _k = mock_log.error.call_args
    for expected in (
        "mismatched endpoint id",
        "Expected",
        "received",
        ep_uuid,
        other_endpoint_id,
    ):
        assert expected in a[0], "Expect contextually helpful info in .error() call"


@pytest.mark.parametrize(
    "exit_code,status_code",
    (
        (os.EX_UNAVAILABLE, HTTPStatus.CONFLICT),
        (os.EX_UNAVAILABLE, HTTPStatus.LOCKED),
        (os.EX_UNAVAILABLE, HTTPStatus.NOT_FOUND),
        (os.EX_DATAERR, HTTPStatus.BAD_REQUEST),
        (os.EX_DATAERR, HTTPStatus.UNPROCESSABLE_ENTITY),
        ("Error", 418),  # IM_A_TEAPOT
    ),
)
def test_register_endpoint_blocked(
    mock_log,
    mock_gcc,
    mock_get_client,
    mock_ep_data,
    randomstring,
    exit_code,
    status_code,
    ep_uuid,
):
    """
    Check to ensure endpoint registration escalates up with API error
    """
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)

    some_err = randomstring()
    res = requests.Response()
    res.headers = {"Content-Type": "application/json"}
    res._content = json.dumps({"msg": some_err}).encode()
    res.status_code = status_code
    res.request = requests.Request("POST")

    mock_gcc.register_endpoint.side_effect = GlobusAPIError(res)

    f = io.StringIO()
    f.isatty = lambda: True
    with redirect_stdout(f):
        with pytest.raises((GlobusAPIError, SystemExit)) as pytexc:
            ep.start_endpoint(*ep_args, reg_info={}, ep_info={})
        stdout_msg = f.getvalue()

    assert mock_log.warning.called
    a, *_ = mock_log.warning.call_args
    assert some_err in str(a), "Expected upstream response still shared"

    assert some_err in stdout_msg, f"Expecting error message in stdout ({stdout_msg})"
    assert pytexc.value.code == exit_code, "Expecting meaningful exit code"

    if exit_code == "Error":
        # The other route tests SystemExit; nominally this route is an unhandled
        # traceback -- good.  We should _not_ blanket hide all exceptions.
        assert pytexc.value.http_status == status_code


def test_register_endpoint_already_active(mock_get_client, mock_ep_data, ep_uuid):
    """Check to ensure endpoint already active message prints to console"""
    pid_active = {
        "exists": True,
        "active": True,
    }
    f = io.StringIO()
    f.isatty = lambda: True

    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)
    with mock.patch(f"{_mock_base}Endpoint.check_pidfile", return_value=pid_active):
        with redirect_stdout(f):
            with pytest.raises(SystemExit) as pytest_exc:
                ep.start_endpoint(*ep_args, reg_info={}, ep_info={})
    assert "is already active" in f.getvalue()
    assert pytest_exc.value.code == -1


def test_list_endpoints_none_configured(mock_ep_buf):
    buf = mock_ep_buf
    Endpoint.print_endpoint_table()
    assert "No endpoints configured" in buf.getvalue()
    assert "Hint:" in buf.getvalue()
    assert "globus-compute-endpoint configure" in buf.getvalue()


def test_list_endpoints_no_id_yet(mock_ep_buf, randomstring):
    buf = mock_ep_buf
    expected_col_length = random.randint(2, 30)
    Endpoint.get_endpoints.return_value = {
        "default": {"status": randomstring(length=expected_col_length), "id": None}
    }
    Endpoint.print_endpoint_table()
    assert Endpoint.get_endpoints.return_value["default"]["status"] in buf.getvalue()
    assert "| Endpoint ID |" in buf.getvalue(), "Expecting column shrinks to size"


@pytest.mark.parametrize("term_size", ((30, 5), (50, 5), (67, 5), (72, 5), (120, 5)))
def test_list_endpoints_long_names_wrapped(
    mock_ep_buf, mocker, term_size, randomstring
):
    buf = mock_ep_buf
    tsize = namedtuple("terminal_size", ["columns", "lines"])(*term_size)
    mock_shutil = mocker.patch("globus_compute_endpoint.endpoint.endpoint.shutil")
    mock_shutil.get_terminal_size.return_value = tsize

    def rand_length_str(min_=2, max_=30):
        return randomstring(length=random.randint(min_, max_))

    expected_data = {
        rand_length_str(100, 110): {"status": rand_length_str(), "id": uuid.uuid4()},
        rand_length_str(100, 110): {"status": rand_length_str(), "id": uuid.uuid4()},
        rand_length_str(100, 110): {"status": rand_length_str(), "id": uuid.uuid4()},
        rand_length_str(100, 110): {"status": rand_length_str(), "id": None},
        rand_length_str(100, 110): {"status": rand_length_str(), "id": uuid.uuid4()},
    }
    Endpoint.get_endpoints.return_value = expected_data

    Endpoint.print_endpoint_table()

    for ep_name, ep in expected_data.items():
        assert ep["status"] in buf.getvalue(), "expected no wrapping of status"
        assert str(ep["id"]) in buf.getvalue(), "expected no wrapping of id"
        assert ep_name not in buf.getvalue(), "expected only name column is wrapped"


@pytest.mark.parametrize(
    "pid_info",
    [
        [False, None, False, False],
        [True, "", True, False],
        [True, "123", True, False],
    ],
)
def test_pid_file_check(pid_info, fs):
    has_file, pid_content, should_exist, should_active = pid_info

    ep_dir = pathlib.Path(".")
    if has_file:
        (ep_dir / "daemon.pid").write_text(pid_content)

    pid_status = Endpoint.check_pidfile(ep_dir)
    assert should_exist == pid_status["exists"]
    assert should_active == pid_status["active"]


@pytest.mark.parametrize(
    "engine_cls", (GlobusComputeEngine, ThreadPoolEngine, ProcessPoolEngine)
)
def test_endpoint_get_metadata(mocker, engine_cls):
    mock_data = {
        "endpoint_version": "106.7",
        "hostname": "oneohtrix.never",
        "local_user": "daniel",
    }

    mocker.patch(
        "globus_compute_endpoint.endpoint.endpoint.__version__",
        mock_data["endpoint_version"],
    )

    mock_fqdn = mocker.patch("globus_compute_endpoint.endpoint.endpoint.socket.getfqdn")
    mock_fqdn.return_value = mock_data["hostname"]

    mock_pwuid = mocker.patch("globus_compute_endpoint.endpoint.endpoint.pwd.getpwuid")
    mock_pwuid.return_value = SimpleNamespace(pw_name=mock_data["local_user"])

    k = {}
    if engine_cls is GlobusComputeEngine:
        k["address"] = "::1"
    executors = [engine_cls(**k)]
    test_config = UserEndpointConfig(executors=executors)
    test_config.source_content = "foo: bar"
    meta = Endpoint.get_metadata(test_config)

    for e in test_config.executors:
        e.shutdown()

    for k, v in mock_data.items():
        assert meta[k] == v

    assert meta["endpoint_config"] == test_config.source_content
    config = meta["config"]
    assert len(config["executors"]) == 1
    assert config["executors"][0]["type"] == engine_cls.__name__
    if engine_cls is GlobusComputeEngine:
        assert config["executors"][0]["executor"]["provider"]["type"] == "LocalProvider"


@pytest.mark.parametrize("env", [None, "blar", "local", "production"])
def test_endpoint_sets_process_title(
    randomstring, mock_ep_data, env, mock_reg_info, ep_uuid
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_conf.environment = env

    orig_proc_title = randomstring()

    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)
    with mock.patch(f"{_mock_base}setproctitle", spec=True) as mock_spt:
        mock_spt.getproctitle.return_value = orig_proc_title
        mock_spt.setproctitle.side_effect = StopIteration("Sentinel")
        with pytest.raises(StopIteration, match="Sentinel"):
            ep.start_endpoint(*ep_args, reg_info=mock_reg_info, ep_info={})

    a, _k = mock_spt.setproctitle.call_args
    assert a[0].startswith(
        "Globus Compute Endpoint"
    ), "Expect easily identifiable process name"
    assert f"{ep_uuid}, {ep_dir.name}" in a[0], "Expect easily match process to ep conf"
    if not env:
        assert " - " not in a[0], "Default is not 'do not show env' for prod"
    else:
        assert f" - {env}" in a[0], "Expected environment name in title"
    assert a[0].endswith(f"[{orig_proc_title}]"), "Save original cmdline for debugging"


@pytest.mark.parametrize("port", [random.randint(0, 65535)])
def test_endpoint_respects_port(mock_ep_data, port, mock_reg_info, ep_uuid):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_conf.amqp_port = port

    tq_url = mock_reg_info["task_queue_info"]["connection_url"]
    rq_url = mock_reg_info["result_queue_info"]["connection_url"]
    hbq_url = mock_reg_info["heartbeat_queue_info"]["connection_url"]

    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)
    with mock.patch(f"{_mock_base}update_url_port", spec=True) as mock_upd:
        mock_upd.side_effect = (None, None, StopIteration("Sentinel"))
        with pytest.raises(StopIteration, match="Sentinel"):
            ep.start_endpoint(*ep_args, reg_info=mock_reg_info, ep_info={})

    for (a, _), exp_url in zip(mock_upd.call_args_list, (tq_url, rq_url, hbq_url)):
        assert a == (exp_url, port)


def test_endpoint_sets_owner_only_access(tmp_path, umask):
    umask(0)
    ep_dir = tmp_path / "new_endpoint_dir"
    Endpoint.init_endpoint_dir(ep_dir)

    assert ep_dir.stat().st_mode & 0o777 == 0o700, "Expected user-only access"


def test_endpoint_config_handles_umask_gracefully(tmp_path, umask):
    umask(0o777)  # No access whatsoever
    ep_dir = tmp_path / "new_endpoint_dir"
    Endpoint.init_endpoint_dir(ep_dir)

    assert ep_dir.stat().st_mode & 0o777 == 0o300, "Should honor user-read bit"
    ep_dir.chmod(0o700)  # necessary for test to cleanup after itself


def test_mu_endpoint_user_ep_yamls_world_readable(tmp_path):
    ep_dir = tmp_path / "new_endpoint_dir"
    Endpoint.init_endpoint_dir(ep_dir, multi_user=True)

    user_tmpl_path = Endpoint.user_config_template_path(ep_dir)
    user_env_path = Endpoint._user_environment_path(ep_dir)

    assert user_env_path != user_tmpl_path, "Dev typo while developing"
    for p in (user_tmpl_path, user_env_path):
        assert p.exists()
        assert p.stat().st_mode & 0o444 == 0o444, "Minimum world readable"
    assert ep_dir.stat().st_mode & 0o111 == 0o111, "Minimum world executable"


def test_mu_endpoint_user_ep_sensible_default(tmp_path):
    ep_dir = tmp_path / "new_endpoint_dir"
    Endpoint.init_endpoint_dir(ep_dir, multi_user=True)
    parent_cfg = get_config(ep_dir)

    tmpl_str, schema = load_user_config_template(ep_dir)
    # Doesn't crash; loads yaml, jinja template has defaults
    render_config_user_template(parent_cfg, tmpl_str, schema, {})


def test_always_prints_endpoint_id_to_terminal(
    mock_daemon, mock_launch, mocker, mock_ep_data, mock_reg_info
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_id = str(uuid.uuid4())

    mock_dup2 = mocker.patch(f"{_mock_base}os.dup2")
    mock_dup2.return_value = 0
    mock_sys = mocker.patch(f"{_mock_base}sys")

    expected_text = f"Starting endpoint; registered ID: {ep_id}"

    reg_info = {**mock_reg_info, "endpoint_id": ep_id}

    mock_sys.stdout.isatty.return_value = True
    ep.start_endpoint(
        ep_dir, ep_id, ep_conf, log_to_console, no_color, reg_info, ep_info={}
    )

    assert mock_sys.stdout.write.called
    assert not mock_sys.stderr.write.called
    assert any(expected_text == a[0] for a, _ in mock_sys.stdout.write.call_args_list)

    mock_sys.reset_mock()
    mock_sys.stdout.isatty.return_value = False
    mock_sys.stderr.isatty.return_value = True
    ep.start_endpoint(
        ep_dir, ep_id, ep_conf, log_to_console, no_color, reg_info, ep_info={}
    )

    assert not mock_sys.stdout.write.called
    assert mock_sys.stderr.write.called
    assert any(expected_text == a[0] for a, _ in mock_sys.stderr.write.call_args_list)
    mock_sys.reset_mock()
    mock_sys.stderr.isatty.return_value = False
    ep.start_endpoint(
        ep_dir, ep_id, ep_conf, log_to_console, no_color, reg_info, ep_info={}
    )

    assert not mock_sys.stdout.write.called
    assert not mock_sys.stderr.write.called


def test_serialize_config_field_types():
    fns = [str(uuid.uuid4()) for _ in range(5)]

    ep_config = UserEndpointConfig(executors=[GlobusComputeEngine(address="::1")])
    ep_config._hidden_attr = "123"
    ep_config.rando_attr = "howdy"
    ep_config.allowed_functions = fns
    ep_config.heartbeat_threshold = float("inf")

    class Foo:
        def __init__(self, foo):
            self._foo = foo

        @property
        def foo(self):
            return self._foo

    # Testing support for properties
    ep_config.environment = Foo("bar")

    result = serialize_config(ep_config)

    # Objects with a __dict__ attr are expanded
    assert "type" in result["executors"][0]["executor"]["provider"]

    # Only constructor parameters should be included
    assert "_hidden_attr" not in result
    assert "rando_attr" not in result

    # Most values should retain their type
    assert isinstance(result["allowed_functions"], list)
    assert len(result["allowed_functions"]) == len(fns)
    assert result["allowed_functions"] == fns
    assert isinstance(result["heartbeat_period"], int)
    assert isinstance(result["detach_endpoint"], bool)
    assert result["environment"]["foo"] == "bar"

    # Others should not
    assert isinstance(result["heartbeat_threshold"], str)


@pytest.mark.parametrize(
    "ep_path_name",
    (
        (True, "nice_normal_name", None, None),
        (True, "12345AnotherValid_name", None, None),
        (False, "", False, "Received no endpoint name"),
        (True, "a" * 128, None, None),
        (False, "a" * 129, False, "must be less than 129 characters (length: "),
        (False, "../ep_name", True, "no '..' or '/' characters"),
        (False, "./ep_name", True, "no '..' or '/' characters"),
        (False, "/ep_name", True, "no '..' or '/' characters"),
        (False, ".initial_dot_hidden_directories_disallowed", True, _whitespace_msg),
        (False, "contains...\r...other_whitespace", True, _whitespace_msg),
        (False, "contains...\v...other_whitespace", True, _whitespace_msg),
        (False, "contains...\t...other_whitespace", True, _whitespace_msg),
        (False, "contains...\n...other_whitespace", True, _whitespace_msg),
        (False, " NoSpaces", True, _whitespace_msg),
        (False, "No Spaces", True, _whitespace_msg),
        (False, "NoSpaces ", True, _whitespace_msg),
        (False, "NoEsc\\apes", True, _whitespace_msg),
        (False, "No'singlequotes'", True, _whitespace_msg),
        (False, 'No"doublequotes"', True, _whitespace_msg),
    ),
)
def test_validate_endpoint_name(ep_path_name):
    is_valid, name, shows_before_after, err_msg = ep_path_name
    if not is_valid:
        with pytest.raises(ValueError) as pyt_exc:
            Endpoint.validate_endpoint_name(name)
        assert err_msg in str(pyt_exc.value)
        if shows_before_after:
            assert "Requested: " in str(pyt_exc.value), "Should show what was received"
            assert name in str(pyt_exc.value), "Should show what was received"
            assert "Reduced to: " in str(pyt_exc.value), "Should show potential fix"
    else:
        Endpoint.validate_endpoint_name(name)


_test_get_endpoint_dir_by_uuid__data = [
    ("foo", str(uuid.uuid4()), True),
    ("non-existent", str(uuid.uuid4()), False),
]


@pytest.mark.parametrize("name,uuid,exists", _test_get_endpoint_dir_by_uuid__data)
def test_get_endpoint_dir_by_uuid(tmp_path, name, uuid, exists):
    gc_conf_dir = tmp_path / ".globus_compute"
    gc_conf_dir.mkdir()
    for n, u, e in _test_get_endpoint_dir_by_uuid__data:
        if not e:
            continue
        ep_conf_dir = gc_conf_dir / n
        ep_conf_dir.mkdir()
        ep_json = ep_conf_dir / "endpoint.json"
        ep_json.write_text(json.dumps({"endpoint_id": u}))
        # dummy config.yaml so that Endpoint._get_ep_dirs finds this
        (ep_conf_dir / "config.yaml").write_text("")

    result = Endpoint.get_endpoint_dir_by_uuid(gc_conf_dir, uuid)
    assert exists is (result is not None)


@pytest.mark.parametrize("json_exists", [True, False])
def test_get_endpoint_id(tmp_path: pathlib.Path, json_exists: bool, ep_uuid):
    if json_exists:
        ep_json = tmp_path / "endpoint.json"
        ep_json.write_text(json.dumps({"endpoint_id": ep_uuid}))

    ret = Endpoint.get_endpoint_id(endpoint_dir=tmp_path)

    if json_exists:
        assert ret == ep_uuid
    else:
        assert ret is None


def test_handles_provided_endpoint_id_no_json(
    mock_daemon,
    mock_launch,
    mock_gcc,
    mock_get_client,
    mock_ep_data: tuple[Endpoint, pathlib.Path, bool, bool, UserEndpointConfig],
    mock_reg_info: dict,
    ep_uuid,
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    ep_args = (ep_dir, ep_uuid, ep_conf, log_to_console, no_color)

    mock_gcc.register_endpoint.return_value = mock_reg_info
    ep.start_endpoint(*ep_args, reg_info={}, ep_info={})

    _a, k = mock_gcc.register_endpoint.call_args
    assert k["endpoint_id"] == ep_uuid


def test_handles_provided_endpoint_id_with_json(
    mock_daemon,
    mock_launch,
    mock_gcc,
    mock_get_client,
    mock_ep_data: tuple[Endpoint, pathlib.Path, bool, bool, UserEndpointConfig],
    mock_reg_info: dict,
    ep_uuid,
):
    ep, ep_dir, log_to_console, no_color, ep_conf = mock_ep_data
    provided_ep_uuid_str = str(uuid.uuid4())
    ep_args = (ep_dir, provided_ep_uuid_str, ep_conf, log_to_console, no_color)

    ep_json = ep_dir / "endpoint.json"
    ep_json.write_text(json.dumps({"endpoint_id": ep_uuid}))
    ep.start_endpoint(*ep_args, reg_info={}, ep_info={})

    _a, k = mock_gcc.register_endpoint.call_args
    assert k["endpoint_id"] == ep_uuid


def test_delete_remote_endpoint_no_local_offline(
    mock_gcc, mock_get_client, mock_ep_data, ep_uuid
):
    ep = mock_ep_data[0]
    mock_gcc.get_endpoint_status.return_value = {"status": "offline"}
    ep.delete_endpoint(None, None, force=False, ep_uuid=ep_uuid)
    assert mock_gcc.delete_endpoint.called
    assert mock_gcc.delete_endpoint.call_args[0][0] == ep_uuid


@pytest.mark.parametrize("ep_status", ("online", "offline"))
def test_delete_endpoint_with_uuid_happy(
    ep_status: str,
    mock_gcc,
    mock_get_client,
    mock_log,
    mock_ep_data: tuple[Endpoint, pathlib.Path, bool, bool, UserEndpointConfig],
    ep_uuid,
):
    ep, ep_dir, *_, ep_config = mock_ep_data
    mock_gcc.get_endpoint_status.return_value = {"status": ep_status}
    online = ep_status == "online"

    with mock.patch(f"{_mock_base}Endpoint.stop_endpoint") as mock_stop:
        ep.delete_endpoint(ep_dir, ep_config, force=online, ep_uuid=ep_uuid)

    assert mock_stop.called is online
    assert mock_gcc.delete_endpoint.called
    assert "deleted from the web service" in mock_log.info.call_args_list[0].args[0]
    assert "has been deleted" in mock_log.info.call_args_list[1].args[0]


@pytest.mark.parametrize(
    (
        "no_uuid",
        "ep_status",
        "log_msg",
        "exc",
        "exit_code",
    ),
    [
        (
            False,
            "online",
            "currently running",
            None,
            -1,
        ),
        (
            True,
            "offline",
            "Name or UUID is needed to delete an Endpoint",
            None,
            -1,
        ),
        (
            False,
            "online",
            "blah xyz",
            NetworkError("blah xyz", Exception("something")),
            os.EX_TEMPFAIL,
        ),
    ],
)
def test_delete_endpoint_with_uuid_errors(
    mock_log,
    no_uuid: bool,
    ep_status: str,
    log_msg: str | None,
    exc: Exception | None,
    exit_code: bool,
    mock_gcc,
    mock_get_client,
    mock_ep_data: tuple[Endpoint, pathlib.Path, bool, bool, UserEndpointConfig],
):
    ep = mock_ep_data[0]
    ep_uuid = None if no_uuid else str(uuid.uuid4())

    if exc:
        mock_gcc.get_endpoint_status.side_effect = exc
    else:
        mock_gcc.get_endpoint_status.return_value = {"status": ep_status}

    with pytest.raises(SystemExit) as pyt_exc:
        ep.delete_endpoint(None, None, ep_uuid=ep_uuid)

    assert pyt_exc.value.code == exit_code
    assert log_msg in mock_log.warning.call_args[0][0]
