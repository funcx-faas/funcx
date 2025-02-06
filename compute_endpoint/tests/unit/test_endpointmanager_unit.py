import fcntl
import io
import json
import logging
import os
import pathlib
import pwd
import queue
import random
import re
import resource
import shutil
import signal
import sys
import time
import typing as t
import uuid
from collections import namedtuple
from concurrent.futures import Future
from contextlib import redirect_stdout
from http import HTTPStatus
from unittest import mock

import pika
import pytest as pytest
import responses
import yaml
from globus_compute_common.messagepack import unpack
from globus_compute_common.messagepack.message_types import EPStatusReport
from globus_compute_endpoint.endpoint.config import ManagerEndpointConfig
from globus_compute_endpoint.endpoint.config.config import MINIMUM_HEARTBEAT
from globus_compute_endpoint.endpoint.endpoint import Endpoint
from globus_compute_endpoint.endpoint.rabbit_mq import (
    CommandQueueSubscriber,
    ResultPublisher,
)
from globus_compute_endpoint.endpoint.utils import _redact_url_creds
from globus_sdk import GlobusAPIError, NetworkError

try:
    import pyprctl
except AttributeError:
    pytest.skip(allow_module_level=True)
else:
    # these imports also import pyprctl later
    from globus_compute_endpoint.endpoint.endpoint_manager import (
        EndpointManager,
        InvalidUserError,
        MappedPosixIdentity,
    )


_MOCK_BASE = "globus_compute_endpoint.endpoint.endpoint_manager."
_GOOD_EC = 89  # SPoA for "good/happy-path" exit code

_mock_rootuser_rec = pwd.struct_passwd(
    ("root", "", 0, 0, "Mock Root User", "/mock_root", "/bin/false")
)
_mock_localuser_rec = pwd.struct_passwd(
    (
        "a_local_user",
        "",
        12345,
        67890,
        "Mock Regular User",
        "/usr/home/...",
        "/bin/false",
    )
)

_ep_info_known_keys = (
    "posix_ppid",
    "globus_candidate_identities",
    "globus_matched_identity",
)


class MockPamError(Exception):
    def __init__(self, *a, **k):
        pass


def mock_ensure_compute_dir():
    return pathlib.Path(_mock_localuser_rec.pw_dir) / ".globus_compute"


@pytest.fixture
def mock_log():
    with mock.patch(f"{_MOCK_BASE}log", spec=logging.Logger) as m:
        m.getEffectiveLevel.return_value = logging.DEBUG
        yield m


@pytest.fixture
def conf_dir(fs, request):
    conf_dir = pathlib.Path(f"/{request.node.name}/mock_endpoint")
    conf_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    g_and_o_perms = (conf_dir.stat().st_mode | 0o00) & 0o77  # get G and O perms
    assert g_and_o_perms == 0, "Tests should work with protective permissions"
    yield conf_dir
    if conf_dir.exists():
        g_and_o_perms = (conf_dir.stat().st_mode | 0o00) & 0o77  # get G and O perms
        assert g_and_o_perms == 0, "Code should not change permissions"


@pytest.fixture
def identity_map_path(conf_dir):
    im_path = conf_dir / "some_identity_mapping_configuration.json"
    im_path.write_text("[]")
    yield im_path


@pytest.fixture
def mock_conf(identity_map_path):
    yield ManagerEndpointConfig(multi_user=True)


@pytest.fixture
def mock_conf_root(identity_map_path):
    to_mock = "globus_compute_endpoint.endpoint.config.config.is_privileged"
    with mock.patch(to_mock) as m:
        m.return_value = True
        yield ManagerEndpointConfig(
            multi_user=True, identity_mapping_config_path=identity_map_path
        )


@pytest.fixture(autouse=True)
def user_conf_template(conf_dir):
    template = Endpoint.user_config_template_path(conf_dir)
    template.write_text(
        """
heartbeat_period: {{ heartbeat }}
engine:
    type: GlobusComputeEngine
    provider:
        type: LocalProvider
        init_blocks: 1
        min_blocks: 0
        max_blocks: 1
        """
    )


@pytest.fixture(autouse=True)
def mock_setproctitle(mocker, randomstring):
    orig_proc_title = randomstring()
    mock_spt = mocker.patch(f"{_MOCK_BASE}setproctitle")
    mock_spt.getproctitle.return_value = orig_proc_title
    yield mock_spt, orig_proc_title


@pytest.fixture
def mock_reg_info(ep_uuid) -> str:
    yield {
        "endpoint_id": ep_uuid,
        "command_queue_info": {"connection_url": "", "queue": ""},
        "result_queue_info": {
            "connection_url": "",
            "queue": "",
            "queue_publish_kwargs": {},
        },
        "heartbeat_queue_info": {
            "connection_url": "",
            "queue": "",
            "queue_publish_kwargs": {},
        },
    }


@pytest.fixture
def mock_client(mocker, ep_uuid, mock_reg_info):
    mock_gcc = mock.Mock()
    mock_gcc.register_endpoint.return_value = mock_reg_info
    mocker.patch("globus_compute_sdk.Client", return_value=mock_gcc)
    yield ep_uuid, mock_gcc


@pytest.fixture
def mock_auth_client(mocker):
    mock_ac = mock.Mock()
    mocker.patch(f"{_MOCK_BASE}ComputeAuthClient", return_value=mock_ac)
    yield mock_ac


@pytest.fixture(autouse=True)
def mock_pim(request):
    if "no_mock_pim" in request.keywords:
        yield
        return

    with mock.patch(f"{_MOCK_BASE}PosixIdentityMapper") as mock_pim:
        mock_pim.return_value = mock_pim
        yield mock_pim


@pytest.fixture
def mock_props():
    yield pika.BasicProperties(
        content_type="application/json",
        content_encoding="utf-8",
        timestamp=round(time.time()),
        expiration="10000",
    )


@pytest.fixture
def epmanager_as_user(mocker, conf_dir, mock_client, mock_auth_client, mock_conf):
    mock_os = mocker.patch(f"{_MOCK_BASE}os")
    mock_os.getuid.return_value = _mock_localuser_rec.pw_uid
    mock_os.getgid.return_value = _mock_localuser_rec.pw_gid

    mock_os.getppid.return_value = 3333
    mock_os.getpid.return_value = 44444444
    mock_os.fork.return_value = 0
    mock_os.pipe.return_value = 40, 41
    mock_os.dup2.side_effect = (0, 1, 2, AssertionError("dup2: unexpected?"))
    mock_os.open.side_effect = (4, 5, AssertionError("open: unexpected?"))

    mock_pwd = mocker.patch(f"{_MOCK_BASE}pwd")
    mock_pwd.getpwnam.side_effect = AssertionError(
        "getpwnam: unprivileged should not care"
    )
    mock_pwd.getpwuid.side_effect = (
        _mock_localuser_rec,  # Initial "who am I?"
        _mock_localuser_rec,  # Registration's get_metadata()
        AssertionError("getpwuid: should not request user in event loop!"),
    )

    mocker.patch(f"{_MOCK_BASE}is_privileged", return_value=False)

    ep_uuid, _ = mock_client

    # Needed to mock the pipe buffer size
    mocker.patch.object(fcntl, "fcntl", return_value=8192)

    ident = "some_identity_uuid"
    mock_auth_client.userinfo.return_value = {"identity_set": [{"sub": ident}]}

    mock_conf.identity_mapping_config_path = None
    em = EndpointManager(conf_dir, ep_uuid, mock_conf)
    assert em.identity_mapper is None

    em._command_queue = mock.Mock()
    em._command_stop_event.set()

    yield conf_dir, mock_conf, mock_client, mock_os, mock_pwd, em
    assert em.identity_mapper is None
    em.request_shutdown(None, None)


@pytest.fixture
def epmanager_as_root(
    mocker, conf_dir, mock_conf_root, mock_client, mock_auth_client, mock_pim
):
    mock_os = mocker.patch(f"{_MOCK_BASE}os")
    mock_os.getppid.return_value = 1111
    mock_os.getpid.return_value = 22222222
    mock_os.getuid.return_value = 0
    mock_os.getgid.return_value = 0
    mock_os.setuid.side_effect = PermissionError("[unit test] Operation not permitted")

    mock_os.fork.return_value = 0
    mock_os.pipe.return_value = 40, 41
    mock_os.dup2.side_effect = (0, 1, 2, AssertionError("dup2: unexpected?"))
    mock_os.open.side_effect = (4, 5, AssertionError("open: unexpected?"))

    mock_pwd = mocker.patch(f"{_MOCK_BASE}pwd")
    mock_pwd.getpwnam.side_effect = (
        _mock_localuser_rec,
        AssertionError("getpwnam: Test whoops!"),
    )
    mock_pwd.getpwuid.side_effect = (
        _mock_rootuser_rec,
        _mock_localuser_rec,
        _mock_localuser_rec,
        AssertionError("getpwuid: Test whoops!"),
    )

    mocker.patch(f"{_MOCK_BASE}is_privileged", return_value=True)
    mocker.patch(
        f"{_MOCK_BASE}GC.sdk.compute_dir.ensure_compute_dir",
        side_effect=mock_ensure_compute_dir,
    )

    ep_uuid, _ = mock_client

    # Needed to mock the pipe buffer size
    mocker.patch.object(fcntl, "fcntl", return_value=8192)

    ident = "epmanager_some_identity"
    mock_auth_client.userinfo.return_value = {"identity_set": [{"sub": ident}]}

    em = EndpointManager(conf_dir, ep_uuid, mock_conf_root)
    em._command = mock.Mock(spec=CommandQueueSubscriber)
    em._heartbeat_publisher = mock.Mock(spec=ResultPublisher)

    yield conf_dir, mock_conf_root, mock_client, mock_os, mock_pwd, em
    if em.identity_mapper:
        em.identity_mapper.stop_watching()
    em.request_shutdown(None, None)


@pytest.fixture
def ident():
    return "successful_exec_some_identity"


@pytest.fixture
def command_payload(ident):
    return {
        "globus_username": "a@example.com",
        "globus_effective_identity": 1,
        "globus_identity_set": [ident],
        "command": "cmd_start_endpoint",
        "kwargs": {"name": "some_ep_name", "user_opts": {"heartbeat": 10}},
    }


@pytest.fixture
def successful_exec_from_mocked_root(
    mocker,
    epmanager_as_root,
    mock_auth_client,
    user_conf_template,
    mock_props,
    ident,
    command_payload,
):
    conf_dir, mock_conf, mock_client, mock_os, mock_pwd, em = epmanager_as_root

    mock_auth_client.userinfo.return_value = {"identity_set": [{"sub": ident}]}

    queue_item = (1, mock_props, json.dumps(command_payload).encode())

    em.identity_mapper = mock.Mock()
    em.identity_mapper.map_identities.return_value = [
        [{ident: ["typicalglobusname@somehost.org"]}]
    ]
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]

    yield mock_os, conf_dir, mock_conf, mock_client, mock_pwd, em


@pytest.fixture
def register_endpoint_failure_response(endpoint_uuid: uuid.UUID):
    def create_response(
        endpoint_id: uuid.UUID = endpoint_uuid,
        status_code: int = 200,
        err_msg: str = "some error msg",
    ):
        responses.add(
            method=responses.POST,
            url="https://compute.api.globus.org/v3/endpoints",
            headers={"Content-Type": "application/json"},
            json={"error": err_msg},
            status=status_code,
        )
        responses.add(
            method=responses.PUT,
            url=f"https://compute.api.globus.org/v3/endpoints/{endpoint_id}",
            headers={"Content-Type": "application/json"},
            json={"error": err_msg},
            status=status_code,
        )

    return create_response


def _create_pam_handle_mock():
    try:
        # attempt to play nice with systems that do not have PAM installed, and
        # rely on those that do to test with spec=PamHandle
        from globus_compute_endpoint.pam import PamHandle

        _has_pam = True
    except ImportError:
        _has_pam = False

    def _create_mock():
        # work with other fixtures (namely fs), that don't like multiple attempts to
        # import; do the work once and cache it via closure
        while True:
            if _has_pam:
                yield mock.MagicMock(spec=PamHandle)
            else:
                yield mock.MagicMock()

    return _create_mock()


create_pam_handle_mock = _create_pam_handle_mock()


@pytest.fixture
def mock_pamh():
    m = next(create_pam_handle_mock)
    m.return_value = m
    m.__enter__.return_value = m
    yield m


@pytest.fixture
def mock_pam(mock_pamh):
    with mock.patch(f"{_MOCK_BASE}_import_pam") as m:
        m.return_value = m
        m.PamHandle = mock_pamh
        m.PamError = MockPamError
        yield m


@pytest.fixture
def mock_ctl():
    with mock.patch(f"{_MOCK_BASE}_import_pyprctl") as m:
        m.return_value = m
        yield m


@pytest.mark.parametrize("env", [None, "blar", "local", "production"])
def test_sets_process_title(
    randomstring, conf_dir, mock_conf, mock_client, mock_setproctitle, env
):
    mock_spt, orig_proc_title = mock_setproctitle

    ep_uuid, mock_gcc = mock_client
    mock_conf.environment = env

    EndpointManager(conf_dir, ep_uuid, mock_conf)
    assert mock_spt.setproctitle.called, "Sanity check"

    a, *_ = mock_spt.setproctitle.call_args
    assert a[0].startswith(
        "Globus Compute Endpoint"
    ), "Expect easily identifiable process name"
    assert "*(" in a[0], "Expected asterisk as subtle clue of 'multi-user'"
    assert f"{ep_uuid}, {conf_dir.name}" in a[0], "Can find process by conf"

    if env:
        assert f" - {env}" in a[0], "Expected environment name in title"
    else:
        assert " - " not in a[0], "Default is not 'do not show env' for prod"
    assert a[0].endswith(f"[{orig_proc_title}]"), "Save original cmdline for debugging"


@responses.activate
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
def test_gracefully_exits_if_registration_blocked(
    mocker,
    mock_log,
    register_endpoint_failure_response,
    conf_dir,
    mock_conf,
    endpoint_uuid,
    randomstring,
    get_standard_compute_client,
    exit_code,
    status_code,
):
    mock_gcc = get_standard_compute_client()
    mocker.patch("globus_compute_sdk.Client", return_value=mock_gcc)

    some_err = randomstring()
    register_endpoint_failure_response(endpoint_uuid, status_code, some_err)

    f = io.StringIO()
    with redirect_stdout(f):
        with pytest.raises((GlobusAPIError, SystemExit)) as pyexc:
            EndpointManager(conf_dir, endpoint_uuid, mock_conf)
        stdout_msg = f.getvalue()

    assert mock_log.warning.called
    a, *_ = mock_log.warning.call_args
    assert some_err in str(a), "Expected upstream response still shared"

    assert some_err in stdout_msg, f"Expecting error message in stdout ({stdout_msg})"
    assert pyexc.value.code == exit_code, "Expecting meaningful exit code"

    if exit_code == "Error":
        # The other route tests SystemExit; nominally this route is an unhandled
        # traceback -- good.  We should _not_ blanket hide all exceptions.
        assert pyexc.value.http_status == status_code


def test_handles_provided_endpoint_id_no_json(
    mock_client: t.Tuple[uuid.UUID, mock.Mock],
    conf_dir: pathlib.Path,
    mock_conf: ManagerEndpointConfig,
):
    ep_uuid, mock_gcc = mock_client

    EndpointManager(conf_dir, ep_uuid, mock_conf)

    _a, k = mock_gcc.register_endpoint.call_args
    assert k["endpoint_id"] == ep_uuid


def test_handles_provided_endpoint_id_with_json(
    mock_client: t.Tuple[uuid.UUID, mock.Mock],
    conf_dir: pathlib.Path,
    mock_conf: ManagerEndpointConfig,
):
    ep_uuid, mock_gcc = mock_client
    provided_ep_uuid_str = str(uuid.uuid4())

    ep_json = conf_dir / "endpoint.json"
    ep_json.write_text(json.dumps({"endpoint_id": str(ep_uuid)}))

    EndpointManager(conf_dir, provided_ep_uuid_str, mock_conf)

    _a, k = mock_gcc.register_endpoint.call_args
    assert k["endpoint_id"] == ep_uuid


@pytest.mark.parametrize("public", (True, False))
def test_sends_data_during_registration(
    conf_dir, mock_conf: ManagerEndpointConfig, mock_client, public: bool
):
    ep_uuid, mock_gcc = mock_client
    mock_conf.public = public
    mock_conf.source_content = "foo: bar"
    EndpointManager(conf_dir, ep_uuid, mock_conf)

    assert mock_gcc.register_endpoint.called
    _a, k = mock_gcc.register_endpoint.call_args
    expected_keys = {
        "name",
        "endpoint_id",
        "metadata",
        "multi_user",
        "high_assurance",
        "display_name",
        "allowed_functions",
        "auth_policy",
        "subscription_id",
        "public",
    }
    assert expected_keys == k.keys(), "Missing or unexpected keys; update this test?"

    expected_keys = {
        "endpoint_version",
        "hostname",
        "local_user",
        "config",
        "endpoint_config",
        "user_config_template",
        "user_config_schema",
    }
    assert expected_keys == k["metadata"].keys(), "Expected minimal metadata"

    for key in (
        "type",
        "multi_user",
        "environment",
    ):
        assert key in k["metadata"]["config"]

    assert k["public"] is mock_conf.public
    assert k["multi_user"] is True
    assert k["metadata"]["config"]["multi_user"] is True
    assert k["metadata"]["endpoint_config"] == mock_conf.source_content


def test_handles_network_error_scriptably(
    mocker,
    mock_log,
    conf_dir,
    mock_conf,
    endpoint_uuid,
    randomstring,
):
    some_err = randomstring()
    mocker.patch(
        "globus_compute_sdk.Client",
        side_effect=NetworkError(some_err, Exception()),
    )

    with pytest.raises(SystemExit) as pyexc:
        EndpointManager(conf_dir, endpoint_uuid, mock_conf)

    assert pyexc.value.code == os.EX_TEMPFAIL, "Expecting meaningful exit code"
    assert mock_log.exception.called, "Expected usable traceback"
    assert mock_log.critical.called
    a = mock_log.critical.call_args[0][0]
    assert "Network failure" in a
    assert some_err in a


def test_mismatched_id_gracefully_exits(
    mock_log, randomstring, conf_dir, mock_conf, mock_client
):
    wrong_uuid, mock_gcc = mock_client
    ep_uuid = str(uuid.uuid4())
    assert wrong_uuid != ep_uuid, "Verify test setup"

    with pytest.raises(SystemExit) as pyexc:
        EndpointManager(conf_dir, ep_uuid, mock_conf)

    assert pyexc.value.code == os.EX_SOFTWARE, "Expected meaningful exit code"
    assert mock_log.error.called
    a = mock_log.error.call_args[0][0]
    assert "mismatched endpoint" in a
    assert f"Expected: {ep_uuid}" in a
    assert f"received: {wrong_uuid}" in a


@pytest.mark.parametrize(
    "received_data",
    (
        (False, {"command_queue_info": {"connection_url": ""}}),
        (False, {"command_queue_info": {"queue": ""}}),
        (False, {"heartbeat_queue_info": {"connection_url": ""}}),
        (False, {"heartbeat_queue_info": {"queue": ""}}),
        (
            False,
            {
                "typo-ed_cqi": {"connection_url": "", "queue": ""},
                "heartbeat_queue_info": {"connection_url": "", "queue": ""},
            },
        ),
        (
            True,
            {
                "command_queue_info": {"connection_url": "", "queue": ""},
                "heartbeat_queue_info": {
                    "connection_url": "",
                    "queue": "",
                    "queue_publish_kwargs": {},
                },
            },
        ),
    ),
)
def test_handles_invalid_reg_info(
    mock_log, randomstring, conf_dir, mock_conf, mock_client, received_data
):
    ep_uuid, mock_gcc = mock_client
    received_data[1]["endpoint_id"] = ep_uuid
    should_succeed, mock_gcc.register_endpoint.return_value = received_data

    if not should_succeed:
        with pytest.raises(SystemExit) as pyexc:
            EndpointManager(conf_dir, ep_uuid, mock_conf)
        assert pyexc.value.code == os.EX_DATAERR, "Expected meaningful exit code"
        assert mock_log.error.called
        a = mock_log.error.call_args[0][0]
        assert "Invalid or unexpected" in a
    else:
        # "null" test
        EndpointManager(conf_dir, ep_uuid, mock_conf)


def test_records_user_ep_as_running(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    mock_os.fork.return_value = 1

    em._event_loop()

    uep_rec = em._children.pop(1)
    assert uep_rec.ep_name == "some_ep_name"


def test_caches_start_cmd_args_if_ep_already_running(
    successful_exec_from_mocked_root, mocker
):
    *_, em = successful_exec_from_mocked_root
    child_pid = random.randrange(1, 32768 + 1)
    mock_uep = mocker.MagicMock()
    mock_uep.ep_name = "some_ep_name"
    em._children[child_pid] = mock_uep

    em._event_loop()

    assert child_pid in em._children
    cached_args = em._cached_cmd_start_args.pop(child_pid)
    assert cached_args is not None
    mpi, args, kwargs = cached_args
    assert mpi.local_user_record == _mock_localuser_rec
    assert args == []
    assert kwargs == {"name": "some_ep_name", "user_opts": {"heartbeat": 10}}


def test_writes_endpoint_uuid(epmanager_as_root):
    conf_dir, _mock_conf, mock_client, *_ = epmanager_as_root
    _ep_uuid, mock_gcc = mock_client

    returned_uuid = mock_gcc.register_endpoint.return_value["endpoint_id"]

    ep_json_path = conf_dir / "endpoint.json"
    assert ep_json_path.exists()

    ep_data = json.loads(ep_json_path.read_text())
    assert ep_data["endpoint_id"] == returned_uuid


def test_log_contains_sentinel_lines(
    mocker, mock_log, epmanager_as_root, noop, reset_signals
):
    *_, em = epmanager_as_root

    mocker.patch(f"{_MOCK_BASE}os")
    em._event_loop = noop
    em.start()

    uuid_pat = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    beg_re_sentinel = re.compile(r"\n\n=+ Endpoint Manager begins: ")
    beg_re_uuid = re.compile(rf"\n\n=+ Endpoint Manager begins: {uuid_pat}\n")
    end_re_sentinel = re.compile(r"\n-+ Endpoint Manager ends: ")
    end_re_uuid = re.compile(rf"\n-+ Endpoint Manager ends: {uuid_pat}\n\n")
    log_str = "\n".join(a[0] for a, _ in mock_log.info.call_args_list)
    assert log_str.startswith("\n\n"), "Expect visual separation for log trawlers"
    assert beg_re_sentinel.search(log_str) is not None, "Expected visual begin sentinel"
    assert beg_re_uuid.search(log_str) is not None, "Expected begin sentinel has EP id"
    assert "\nShutdown complete.\n" in log_str
    assert end_re_sentinel.search(log_str) is not None, "Expected visual end sentinel"
    assert end_re_uuid.search(log_str) is not None, "Expected end sentinel has EP id"


def test_title_changes_for_shutdown(
    mocker, epmanager_as_root, noop, mock_setproctitle, reset_signals
):
    *_, em = epmanager_as_root
    mock_spt, orig_proc_title = mock_setproctitle

    em._event_loop = noop
    mocker.patch(f"{_MOCK_BASE}os")

    mock_spt.reset_mock()
    assert not mock_spt.setproctitle.called, "Verify test setup"
    em.start()

    assert mock_spt.setproctitle.called
    a = mock_spt.setproctitle.call_args[0][0]
    assert a.startswith("[shutdown in progress]"), "Let admin know action in progress"
    assert a.endswith(orig_proc_title)


def test_children_signaled_at_shutdown(
    mocker, epmanager_as_root, randomstring, noop, reset_signals
):
    *_, em = epmanager_as_root

    em._event_loop = mock.Mock()
    em.wait_for_children = noop
    mock_os = mocker.patch(f"{_MOCK_BASE}os")
    mock_time = mocker.patch(f"{_MOCK_BASE}time")
    mock_time.monotonic.side_effect = [0, 10, 20, 30]  # don't _actually_ wait.
    mock_os.getuid.side_effect = ["us"]  # fail if called more than once; intentional
    mock_os.getgid.side_effect = ["us"]  # fail if called more than once; intentional
    mock_os.getpgid = lambda pid: pid

    expected = []
    for _ in range(random.randrange(0, 10)):
        uid, gid, pid = tuple(random.randint(1, 2**30) for _ in range(3))
        uname = randomstring()
        expected.append((uid, gid, uname, "some process command line"))
        mock_rec = mocker.MagicMock()
        mock_rec.uid, mock_rec.gid = uid, gid
        em._children[pid] = mock_rec

    gid_expected_calls = (
        a
        for b in zip(
            ((gid, gid, -1) for _uid, gid, *_ in expected),
            [("us", "us", -1)] * len(expected),  # return to root gid after signal
        )
        for a in b
    )
    uid_expected_calls = (
        a
        for b in zip(
            ((uid, uid, -1) for uid, _gid, *_ in expected),
            [("us", "us", -1)] * len(expected),  # return to root uid after signal
        )
        for a in b
    )

    # test that SIGTERM, *then* SIGKILL sent
    killpg_expected_calls = [(pid, signal.SIGTERM) for pid in em._children]
    killpg_expected_calls.extend((pid, signal.SIGKILL) for pid in em._children)

    em.start()
    assert em._event_loop.called, "Verify test setup"

    resgid = mock_os.setresgid.call_args_list
    resuid = mock_os.setresuid.call_args_list
    killpg = mock_os.killpg.call_args_list[1:]

    for setgid_call, exp_args in zip(resgid, gid_expected_calls):
        assert setgid_call[0] == exp_args, "Signals only sent by _same_ user, NOT root"
    for setuid_call, exp_args in zip(resuid, uid_expected_calls):
        assert setuid_call[0] == exp_args, "Signals only sent by _same_ user, NOT root"
    for killpg_call, exp_args in zip(killpg, killpg_expected_calls):
        assert killpg_call[0] == exp_args, "Expected SIGTERM, *then* SIGKILL"


def test_restarts_running_endpoint_with_cached_args(epmanager_as_root, mock_log):
    *_, mock_os, _mock_pwd, em = epmanager_as_root
    child_pid = random.randrange(1, 32768 + 1)
    mapped_posix = MappedPosixIdentity(_mock_localuser_rec, [], None)
    args_tup = (
        mapped_posix,
        [],
        {"name": "some_ep_name", "user_opts": {"heartbeat": 10}},
    )

    mock_os.waitpid.side_effect = [(child_pid, -1), (0, -1)]
    mock_os.waitstatus_to_exitcode.return_value = 0

    em._cached_cmd_start_args[child_pid] = args_tup
    em.cmd_start_endpoint = mock.Mock()

    em.wait_for_children()

    a, _k = mock_log.info.call_args
    assert "using cached arguments to start" in a[0]
    assert em.cmd_start_endpoint.call_args.args == args_tup


def test_no_cached_args_means_no_restart(epmanager_as_root, mocker, mock_log):
    *_, em = epmanager_as_root
    child_pid = random.randrange(1, 32768 + 1)

    mock_os = mocker.patch(f"{_MOCK_BASE}os")
    mock_os.waitpid.side_effect = [(child_pid, -1), (0, -1)]
    mock_os.waitstatus_to_exitcode.return_value = 0
    em.cmd_start_endpoint = mock.Mock()

    em.wait_for_children()
    a, _k = mock_log.info.call_args
    assert "stopped normally" in a[0], "Verify happy path"

    assert not em.cmd_start_endpoint.called


def test_emits_endpoint_id_if_isatty(mocker, mock_log, epmanager_as_root):
    *_, em = epmanager_as_root

    mocker.patch.object(em, "_install_signal_handlers", side_effect=Exception)

    mock_sys = mocker.patch(f"{_MOCK_BASE}sys")
    mock_sys.stdout.isatty.return_value = True
    mock_sys.stderr.isatty.return_value = True
    with pytest.raises(Exception):
        em.start()

    assert mock_log.info.called, "Always emitted to log"
    a = mock_log.info.call_args[0][0]
    assert em._endpoint_uuid_str in a
    assert mock_sys.stdout.write.called
    assert not mock_sys.stderr.write.called, "Expect ID not emitted twice"
    written = "".join(a[0] for a, _ in mock_sys.stdout.write.call_args_list)
    assert em._endpoint_uuid_str in written

    mock_log.reset_mock()
    mock_sys.reset_mock()
    mock_sys.stdout.isatty.return_value = False
    mock_sys.stderr.isatty.return_value = True
    with pytest.raises(Exception):
        em.start()

    assert mock_log.info.called, "Always emitted to log"
    a = mock_log.info.call_args[0][0]
    assert em._endpoint_uuid_str in a
    assert not mock_sys.stdout.write.called, "Expect ID not emitted twice"
    assert mock_sys.stderr.write.called
    written = "".join(a[0] for a, _ in mock_sys.stderr.write.call_args_list)
    assert em._endpoint_uuid_str in written

    mock_log.reset_mock()
    mock_sys.reset_mock()
    mock_sys.stdout.isatty.return_value = False
    mock_sys.stderr.isatty.return_value = False
    with pytest.raises(Exception):
        em.start()

    assert mock_log.info.called, "Always emitted to log"
    a = mock_log.info.call_args[0][0]
    assert em._endpoint_uuid_str in a
    assert not mock_sys.stdout.write.called
    assert not mock_sys.stderr.write.called


def test_as_root_and_no_identity_mapper_configuration_fails(
    mocker, mock_log, mock_client, conf_dir, mock_conf
):
    mock_print = mocker.patch(f"{_MOCK_BASE}print")
    mocker.patch(f"{_MOCK_BASE}is_privileged", return_value=True)

    ep_uuid, _ = mock_client
    mock_conf.identity_mapping_config_path = None
    with pytest.raises(SystemExit) as pyt_exc:
        EndpointManager(conf_dir, ep_uuid, mock_conf)

    assert pyt_exc.value.code == os.EX_OSFILE
    assert mock_log.error.called
    assert mock_print.called
    for a in (mock_log.error.call_args[0][0], mock_print.call_args[0][0]):
        assert "No identity mapping file specified" in a
        assert "identity_mapping_config_path" in a, "Expected required config item"


def test_no_identity_mapper_if_unprivileged(
    mocker, conf_dir, mock_conf_root, mock_client
):
    mock_privilege = mocker.patch(f"{_MOCK_BASE}is_privileged")
    mock_privilege.return_value = True

    em = EndpointManager(conf_dir, None, mock_conf_root)
    assert em.identity_mapper is not None
    em.identity_mapper.stop_watching()

    mock_privilege.return_value = False
    em = EndpointManager(conf_dir, None, mock_conf_root)
    assert em.identity_mapper is None


def test_unprivileged_warns_if_identity_conf_specified(
    mocker, mock_log, conf_dir, mock_conf, mock_conf_root, mock_client
):
    mocker.patch(f"{_MOCK_BASE}is_privileged", return_value=False)

    em = EndpointManager(conf_dir, None, mock_conf)
    assert em.identity_mapper is None
    assert not mock_log.warning.called

    em = EndpointManager(conf_dir, None, mock_conf_root)
    assert em.identity_mapper is None

    a, _ = mock_log.warning.call_args
    assert "specified, but process is not privileged" in a[0]
    assert "identity mapping configuration will be ignored" in a[0]


def test_quits_if_not_privileged_and_no_identity_set(
    mocker, mock_log, mock_client, mock_auth_client, epmanager_as_root
):
    *_, em = epmanager_as_root
    mocker.patch(f"{_MOCK_BASE}is_privileged", return_value=False)
    mock_auth_client.userinfo.return_value = {"identity_set": []}
    assert em._time_to_stop is False, "Verify test setup"
    em._event_loop()

    assert em._time_to_stop
    a, _ = mock_log.error.call_args
    assert "Failed to determine identity set" in a[0]
    assert "try `whoami` command" in a[0], "Expected suggested action"


def test_clean_exit_on_identity_collection_error(
    mocker, mock_log, mock_client, mock_auth_client, epmanager_as_root
):
    *_, em = epmanager_as_root
    mocker.patch(f"{_MOCK_BASE}is_privileged", return_value=False)
    mock_auth_client.userinfo.return_value = {"not_identity_set": None}
    assert em._time_to_stop is False, "Verify test setup"
    em._event_loop()

    # handle potential Python version differences
    ke = KeyError("identity_set")
    expected_exc_text = f"({type(ke).__name__}) {ke}"

    assert em._time_to_stop
    a, _ = mock_log.error.call_args
    assert expected_exc_text in a[0]
    assert "Failed to determine identity set" in a[0]
    assert "try `whoami` command" in a[0], "Expected suggested action"

    a, k = mock_log.debug.call_args
    assert "failed to determine identities" in a[0]
    assert "exc_info" in k


@pytest.mark.no_mock_pim
def test_as_root_gracefully_handles_unreadable_identity_mapper_conf(
    mocker, mock_log, conf_dir, mock_conf_root, identity_map_path
):
    mock_print = mocker.patch(f"{_MOCK_BASE}print")
    mocker.patch(f"{_MOCK_BASE}is_privileged", return_value=True)

    ep_uuid = str(uuid.uuid1())
    reg_info = {
        "endpoint_id": ep_uuid,
        "command_queue_info": {"connection_url": 1, "queue": 1},
    }
    identity_map_path.chmod(mode=0o000)
    with pytest.raises(SystemExit) as pyt_exc:
        EndpointManager(conf_dir, ep_uuid, mock_conf_root, reg_info)

    assert pyt_exc.value.code == os.EX_NOPERM
    assert mock_log.error.called
    assert mock_print.called
    for a in (mock_log.error.call_args[0][0], mock_print.call_args[0][0]):
        assert "PermissionError" in a

    identity_map_path.chmod(mode=0o644)
    identity_map_path.write_text("[{asfg")
    with pytest.raises(SystemExit) as pyt_exc:
        EndpointManager(conf_dir, ep_uuid, mock_conf_root, reg_info)

    assert pyt_exc.value.code == os.EX_CONFIG
    assert mock_log.error.called
    assert mock_print.called
    for a in (mock_log.error.call_args[0][0], mock_print.call_args[0][0]):
        assert "Unable to read identity mapping" in a


def test_iterates_even_if_no_commands(mocker, epmanager_as_root):
    *_, em = epmanager_as_root

    em._command_stop_event.set()
    em._event_loop()  # subtest is that it iterates and doesn't block

    em._time_to_stop = False
    em._command_queue = mock.Mock()
    em._command_queue.get.side_effect = queue.Empty()
    em._event_loop()

    assert em._command_queue.get.called


@pytest.mark.parametrize("hb", (-100, -5, 0, 0.1, 4, 7, 11, None))
def test_heartbeat_period_minimum(conf_dir, mock_conf, hb, ep_uuid, mock_reg_info):
    if hb is not None:
        mock_conf._heartbeat_period = hb
        assert mock_conf.heartbeat_period == hb, "Avoid config setter"
    em = EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)
    exp_hb = 30.0 if hb is None else max(MINIMUM_HEARTBEAT, hb)
    assert exp_hb == em._heartbeat_period, "Expected a reasonable minimum heartbeat"


def test_send_heartbeat_verifies_thread(mock_conf, conf_dir, ep_uuid, mock_reg_info):
    em = EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)
    f = em.send_heartbeat()
    exc = f.exception()
    assert "publisher is not running" in str(exc)


def test_send_heartbeat_honors_shutdown(mock_conf, conf_dir, ep_uuid, mock_reg_info):
    em = EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)
    em._heartbeat_period = random.randint(1, 10000)
    em._heartbeat_publisher = mock.Mock(spec=ResultPublisher)

    em.send_heartbeat()
    a, _ = em._heartbeat_publisher.publish.call_args
    epsr: EPStatusReport = unpack(a[0])
    assert epsr.global_state["heartbeat_period"] == em._heartbeat_period

    em.send_heartbeat(shutting_down=True)
    a, _ = em._heartbeat_publisher.publish.call_args
    epsr: EPStatusReport = unpack(a[0])
    assert epsr.global_state["heartbeat_period"] == 0


def test_send_heartbeat_shares_exception(
    mock_log, mock_conf, conf_dir, ep_uuid, mock_reg_info, randomstring
):
    exc_text = randomstring()
    em = EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)
    em._heartbeat_publisher = mock.Mock(spec=ResultPublisher)
    em._heartbeat_publisher.publish.return_value = Future()
    f = em.send_heartbeat()
    mock_log.error.reset_mock()
    f.set_exception(MemoryError(exc_text))

    assert mock_log.error.called
    a, _ = mock_log.error.call_args
    assert exc_text in str(a[0])


def test_sends_heartbeat_at_shutdown(epmanager_as_root, noop, reset_signals):
    *_, em = epmanager_as_root
    hb_fut = mock.Mock(spec=Future)
    em.send_heartbeat = mock.Mock(spec=EndpointManager.send_heartbeat)
    em.send_heartbeat.return_value = hb_fut
    em._event_loop = noop
    em.start()

    assert hb_fut.result.called
    a, _ = hb_fut.result.call_args
    assert isinstance(a[0], int), "Expected *some* timeout value for sending a HB"

    _, k = em.send_heartbeat.call_args

    assert k["shutting_down"] is True


def test_heartbeat_publisher_stopped_at_shutdown(
    epmanager_as_root, noop, reset_signals
):
    *_, em = epmanager_as_root
    em._event_loop = noop
    em.start()

    assert em._heartbeat_publisher.stop.called
    assert em._heartbeat_publisher.join.called


@pytest.mark.parametrize("num_iterations", (random.randint(3, 20),))
def test_heartbeat_sent_periodically(
    mocker, epmanager_as_root, reset_signals, num_iterations
):
    *_, em = epmanager_as_root
    last_time = time.monotonic()  # anything greater than em._heartbeat_period will do
    iteration_count = 0

    mock_monotonic = mocker.patch(f"{_MOCK_BASE}time.monotonic")

    def mock_q_get(*a, **k):
        nonlocal iteration_count, num_iterations
        iteration_count += 1
        if iteration_count >= num_iterations:
            em._time_to_stop = True
        raise queue.Empty()

    def increase_time_by_hb(*a, **k):
        nonlocal last_time
        last_time += em._heartbeat_period + 1
        return last_time

    em.send_heartbeat = mock.Mock(spec=EndpointManager.send_heartbeat)
    em._command_queue = mock.Mock(spec=queue.SimpleQueue)
    em._command_queue.get.side_effect = mock_q_get
    mock_monotonic.side_effect = increase_time_by_hb
    em._event_loop()
    assert em.send_heartbeat.call_count == num_iterations


def test_emits_command_requested_debug(mock_log, epmanager_as_root, mock_props):
    *_, em = epmanager_as_root
    em._command_queue = mock.Mock()
    em._command_stop_event.set()

    mock_props.content_type = "asdfasdf"  # quit loop early b/c test is satisfied
    queue_item = [1, mock_props, json.dumps({"asdf": 123}).encode()]
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()
    assert not mock_log.warning.called

    mock_props.headers = {"debug": False}
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._time_to_stop = False
    em._event_loop()
    assert not mock_log.warning.called

    mock_props.headers = {"debug": True}
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._time_to_stop = False
    em._event_loop()
    assert mock_log.warning.called
    a = mock_log.warning.call_args[0][0]

    assert "Command debug requested" in a
    assert f"Delivery Tag: {queue_item[0]}" in a
    assert f"Properties: {queue_item[1]}" in a
    assert f"Body bytes: {queue_item[2]}" in a
    assert em._command.ack.called, "Command always ACKed"


def test_emitted_debug_command_credentials_removed(
    mock_log, epmanager_as_root, randomstring, mock_props
):
    *_, em = epmanager_as_root
    em._command_queue = mock.Mock()
    em._command_stop_event.set()

    mock_props.content_type = "asdfasdf"  # quit early in loop b/c test is satisfied
    mock_props.headers = {"debug": True}

    pword = randomstring()
    pld = {"creds": f"scheme://user:{pword}@some.fqdn:1234/some/path"}
    queue_item = [1, mock_props, json.dumps(pld).encode()]
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()
    assert mock_log.warning.called

    expected = _redact_url_creds(pld["creds"], redact_user=False)
    a = mock_log.warning.call_args[0][0]
    assert "Body bytes:" in a, "Verify test setup"
    assert pld["creds"] not in a
    assert pword not in a
    assert expected in a
    assert em._command.ack.called, "Command always ACKed"


def test_command_verifies_content_type(mock_log, epmanager_as_root, mock_props):
    *_, em = epmanager_as_root
    em._command_queue = mock.Mock()
    em._command_stop_event.set()

    mock_props.content_type = "asdfasdfasdfasd"  # the test

    queue_item = [1, mock_props, json.dumps({"asdf": 123}).encode()]
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()
    assert mock_log.error.called
    a = mock_log.error.call_args[0][0]
    assert "Unable to deserialize Globus Compute services command" in a
    assert "Invalid message type; expecting JSON" in a
    assert em._command.ack.called, "Command always ACKed"


def test_ignores_stale_commands(mock_log, epmanager_as_root, mock_props, randomstring):
    *_, mock_os, _, em = epmanager_as_root
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)

    ep_name = randomstring()
    child_pid = random.randint(2, 1000000)
    mock_os.fork.return_value = child_pid  # remain the parent process

    mock_props.timestamp = round(time.time()) + 10 * 60  # ten-minute clock skew

    queue_item = [1, mock_props, json.dumps({"kwargs": {"name": ep_name}}).encode()]
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()

    assert mock_log.warning.called
    a = mock_log.warning.call_args[0][0]
    assert "Ignoring command from server" in a
    assert "Command too old or skew between" in a
    assert "Command timestamp: " in a
    assert "Endpoint timestamp: " in a
    assert em._command.ack.called, "Command always ACKed"

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert k["msg"] == a


@pytest.mark.parametrize("should_fork", (True, False, None))
def test_send_failure_notice_conditionally_forks(
    mocker, epmanager_as_root, should_fork
):
    mocker.patch(f"{_MOCK_BASE}log")
    *_, mock_os, _, em = epmanager_as_root

    kw = {}
    if should_fork is not None:
        kw = {"fork": should_fork}
    with pytest.raises(SystemExit) as pyt_exc:
        em.send_failure_notice({}, **kw)

    assert pyt_exc.value.code is None, "Should always 'happy exit'"
    assert mock_os.fork.called is (should_fork is None or should_fork)


def test_send_failure_notice_gracefully_ignores_malformed_kwargs(
    mock_log, epmanager_as_root
):
    *_, em = epmanager_as_root

    with pytest.raises(SystemExit) as pyt_exc:
        em.send_failure_notice({}, fork=False)

    assert pyt_exc.value.code is None, "Should always 'happy exit'"
    assert mock_log.exception.called


def test_send_failure_notice_populates_children_structure(
    epmanager_as_root, randomstring
):
    *_, mock_os, _, em = epmanager_as_root

    ep_name = randomstring()
    child_pid = random.randint(2, 1000000)
    mock_os.fork.return_value = child_pid  # remain the parent process

    kw = {"name": ep_name}
    user_info = [randomstring(), randomstring()]
    em.send_failure_notice(kw, "", "\n   ".join(user_info))

    assert child_pid in em._children
    fork_args = em._children[child_pid].arguments
    assert "Temporary process" in fork_args
    assert ep_name in fork_args
    assert all(ui in fork_args for ui in user_info)


def test_send_failure_notice_sends_message(mocker, epmanager_as_root, randomstring):
    mock_send = mocker.patch(f"{_MOCK_BASE}send_endpoint_startup_failure_to_amqp")
    *_, mock_os, _, em = epmanager_as_root
    mock_os.fork.return_value = 0  # test the child process path

    err_msg = randomstring()
    kw = {"amqp_creds": {"some": "structure"}}
    with pytest.raises(SystemExit) as pyt_exc:
        em.send_failure_notice(kw, msg=err_msg)

    assert pyt_exc.value.code is None
    assert mock_send.called

    a, k = mock_send.call_args
    assert a[0] is kw["amqp_creds"]
    assert "msg" in k
    assert k["msg"] is err_msg


def test_send_failure_notice_fails_to_send(mock_log, epmanager_as_root, randomstring):
    *_, mock_os, _, em = epmanager_as_root
    mock_os.fork.return_value = 0  # test the child process path

    kw = None  # will produce TypeError
    with pytest.raises(SystemExit) as pyt_exc:
        em.send_failure_notice(kw)

    assert pyt_exc.value.code is None

    assert mock_log.exception.called

    a, k = mock_log.exception.call_args
    assert "Unable to send user endpoint start up failure" in a[0]


def test_handles_invalid_server_msg_gracefully(mock_log, epmanager_as_root, mock_props):
    *_, em = epmanager_as_root
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)

    queue_item = (1, mock_props, json.dumps({"asdf": 123}).encode())

    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()
    a = mock_log.error.call_args[0][0]
    assert "Invalid server command" in a
    assert "KeyError" in a, "Expected exception name in log line"
    assert em._command.ack.called, "Command always ACKed"

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert k["msg"] == a


@pytest.mark.parametrize(
    "is_invalid,idset",
    (
        (True, None),
        (True, 123),
        (True, "some_ident"),
        (True, [{"a": "123"}]),
        (False, ""),
        (False, ()),
    ),
)
def test_unprivileged_handles_identity_set_robustly(
    mock_log,
    mock_props,
    epmanager_as_user,
    is_invalid,
    idset,
    randomstring,
    mock_auth_client,
):
    cmd_payload = {
        "globus_effective_identity": f"abc{randomstring()}",
        "globus_identity_set": idset,
        "globus_username": f"a{randomstring()}@b.com",
    }
    queue_item = (1, mock_props, json.dumps(cmd_payload).encode())

    *_, em = epmanager_as_user
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)
    em._command_queue.get.side_effect = (queue_item, queue.Empty())
    em._event_loop()

    if is_invalid:
        a, _k = mock_log.debug.call_args
        assert "Invalid identity set" in a[0]
    a, _k = mock_log.error.call_args
    assert "Ignoring start request for untrusted identity" in a[0]

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert k["msg"] == a[0]
    assert "user_ident" in k
    assert cmd_payload["globus_effective_identity"] in k["user_ident"]
    assert cmd_payload["globus_username"] in k["user_ident"]


def test_unprivileged_happy_path(
    mocker, mock_props, epmanager_as_user, mock_client, mock_auth_client
):
    *_, em = epmanager_as_user
    ident_rv = mock_auth_client.userinfo.return_value

    mocker.patch(f"{_MOCK_BASE}log")
    cmd_payload = {
        "globus_effective_identity": "abc",
        "globus_identity_set": ident_rv["identity_set"],
        "globus_username": "a@b.com",
        "command": "cmd_start_endpoint",
    }
    queue_item = (1, mock_props, json.dumps(cmd_payload).encode())

    em._command_queue.get.side_effect = (queue_item, queue.Empty())
    em.cmd_start_endpoint = mock.Mock(spec=em.cmd_start_endpoint)
    em._event_loop()
    assert em.cmd_start_endpoint.called
    a, k = em.cmd_start_endpoint.call_args
    mapped, _a, _kw = a
    assert mapped.matched_identity is None, "Did not map; no matched"
    assert mapped.globus_identity_candidates == [], "Did not map"


def test_privileged_happy_path(epmanager_as_root, mock_props, randomstring, ident):
    *_, em = epmanager_as_root
    pld = {
        "globus_username": "a" + randomstring(),
        "globus_effective_identity": "abc" + randomstring(),
        "globus_identity_set": [],
        "command": "cmd_start_endpoint",
        "kwargs": {"name": "some_ep_name", "user_opts": {"heartbeat": 10}},
    }
    queue_item = (1, mock_props, json.dumps(pld).encode())

    exp_candidate_identities = [[{ident: ["typicalglobusname@somehost.org"]}]]

    em.identity_mapper.map_identities.return_value = exp_candidate_identities
    em.cmd_start_endpoint = mock.Mock()
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]

    em._event_loop()
    assert em.cmd_start_endpoint.called
    a, _k = em.cmd_start_endpoint.call_args

    mpi, cmd_a, cmd_k = a
    assert mpi.local_user_record == _mock_localuser_rec
    assert mpi.matched_identity == ident
    assert mpi.globus_identity_candidates == exp_candidate_identities
    assert cmd_k == pld["kwargs"]


def test_handles_unknown_identity_gracefully(
    mock_log, epmanager_as_root, mock_props, randomstring
):
    *_, em = epmanager_as_root

    pld = {
        "globus_username": "a" + randomstring(),
        "globus_effective_identity": "abc" + randomstring(),
        "globus_identity_set": [],
    }
    queue_item = (1, mock_props, json.dumps(pld).encode())

    em.identity_mapper.map_identities.return_value = [[]]
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]

    em._event_loop()
    a = mock_log.error.call_args[0][0]
    assert "Identity failed to map to a local user name" in a
    assert "(LookupError)" in a, "Expected exception name in log line"
    assert "Globus effective identity: " in a
    assert str(pld["globus_effective_identity"]) in a

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert k["msg"] == a
    assert "user_ident" in k
    assert pld["globus_effective_identity"] in k["user_ident"]
    assert pld["globus_username"] in k["user_ident"]


def test_gracefully_handles_identity_mapping_error(
    mock_log, epmanager_as_root, randomstring, mock_props
):
    *_, em = epmanager_as_root

    pld = {
        "globus_username": randomstring(),
        "globus_effective_identity": randomstring(),
        "globus_identity_set": [],
    }
    exc_text = "Test engineered: " + randomstring()
    queue_item = (1, mock_props, json.dumps(pld).encode())

    em.identity_mapper.map_identities.side_effect = MemoryError(exc_text)
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]

    em._event_loop()
    a = mock_log.error.call_args[0][0]
    assert "Unhandled error attempting to map to a local user name" in a
    assert "(MemoryError)" in a, "Expected exception name in log line"
    assert exc_text in a
    assert "Globus effective identity: " in a
    assert pld["globus_effective_identity"] in a

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert len(k["msg"]) > 2, "Expected a call site-specific message"
    assert "user_ident" in k
    assert pld["globus_effective_identity"] in k["user_ident"]
    assert pld["globus_username"] in k["user_ident"]


@pytest.mark.parametrize(
    "cmd_name", ("", "_private", "9c", "valid_but_do_not_exist", " ", "a" * 101)
)
def test_handles_unknown_or_invalid_command_gracefully(
    mocker, mock_log, epmanager_as_root, cmd_name, mock_props, randomstring
):
    *_, em = epmanager_as_root

    mocker.patch(f"{_MOCK_BASE}pwd")
    em.identity_mapper = mock.Mock()
    em.identity_mapper.map_identities.return_value = [[{"someuuid": ["a"]}]]

    pld = {
        "globus_username": randomstring(),
        "globus_effective_identity": randomstring(),
        "globus_identity_set": "a",
        "command": cmd_name,
        "user_opts": {"heartbeat": 10},
    }
    queue_item = (1, mock_props, json.dumps(pld).encode())

    mocker.patch(f"{_MOCK_BASE}pwd.getpwnam")

    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()
    a = mock_log.error.call_args[0][0]
    assert "Unknown or invalid command" in a
    assert "Globus effective identity: " in a
    assert str(pld["globus_effective_identity"]) in a
    assert str(cmd_name) in a

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert "unexpected error" in k["msg"]
    assert "misconfiguration or a programming error" in k["msg"]
    assert "or the Globus Compute team" in k["msg"]
    assert "user_ident" in k
    assert pld["globus_effective_identity"] in k["user_ident"]
    assert pld["globus_username"] in k["user_ident"]


def test_handles_local_user_not_found_gracefully(
    mock_log, epmanager_as_root, randomstring, mock_props
):
    *_, mock_pwd, em = epmanager_as_root

    invalid_user_name = "username_that_is_not_on_localhost6_" + randomstring()
    em.identity_mapper = mock.Mock()
    em.identity_mapper.map_identities.return_value = [[{"uuid": [invalid_user_name]}]]
    mock_pwd.getpwnam.side_effect = KeyError(invalid_user_name)

    pld = {
        "globus_username": randomstring(),
        "globus_effective_identity": randomstring(),
        "globus_identity_set": "a",
    }
    queue_item = (1, mock_props, json.dumps(pld).encode())

    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()
    a = mock_log.error.call_args[0][0]
    assert "Identity mapped to a local user name, but local user does not exist" in a
    assert f"Local user name: {invalid_user_name}" in a
    assert "Globus effective identity: " in a
    assert str(pld["globus_effective_identity"]) in a

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert len(k["msg"]) > 2, "Expected a call site-specific message"
    assert "user_ident" in k
    assert pld["globus_effective_identity"] in k["user_ident"]
    assert pld["globus_username"] in k["user_ident"]


def test_handles_failed_command(
    mocker, mock_log, epmanager_as_root, mock_props, randomstring
):
    mocker.patch(f"{_MOCK_BASE}pwd.getpwnam")
    mocker.patch(
        f"{_MOCK_BASE}EndpointManager.cmd_start_endpoint", side_effect=Exception()
    )
    *_, em = epmanager_as_root

    pld = {
        "globus_username": randomstring(),
        "globus_effective_identity": randomstring(),
        "globus_identity_set": [],
        "command": "cmd_start_endpoint",
        "user_opts": {"heartbeat": 10},
    }
    queue_item = (1, mock_props, json.dumps(pld).encode())

    em.identity_mapper = mock.Mock()
    em.identity_mapper.map_identities.return_value = [[{"uuid": ["a"]}]]
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)
    em._command_queue = mock.Mock()
    em._command_stop_event.set()
    em._command_queue.get.side_effect = [queue_item, queue.Empty()]
    em._event_loop()
    a = mock_log.exception.call_args[0][0]
    assert "Unable to execute command" in a
    assert pld["command"] in a, "Expected debugging help in log"
    assert "   args: " in a, "Expected debugging help in log"
    assert " kwargs: " in a, "Expected debugging help in log"

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert "msg" not in k, "Expect a general message for unknown exception"
    assert "user_ident" in k
    assert pld["globus_effective_identity"] in k["user_ident"]
    assert pld["globus_username"] in k["user_ident"]


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT, signal.SIGQUIT])
def test_handles_shutdown_signal(successful_exec_from_mocked_root, sig, reset_signals):
    mock_os, *_, em = successful_exec_from_mocked_root

    with mock.patch.object(em, "_install_signal_handlers") as mock_install:
        mock_install.side_effect = Exception()
        with pytest.raises(Exception):
            em.start()
        assert mock_install.called, "Ensure hookup that installs signal handlers ..."

    em._install_signal_handlers()  # ... now install them for real ...
    assert em._time_to_stop is False
    os.kill(os.getpid(), sig)
    em._event_loop()

    assert em._time_to_stop is True
    assert not em._command_queue.get.called, " ... that we've now confirmed works"


def test_environment_default_path(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    expected_pybindir = pathlib.Path(sys.executable).parent
    expected_order = ("/usr/local/bin", "/usr/bin", "/bin", str(expected_pybindir))

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    a, k = mock_os.execvpe.call_args
    env = k["env"]
    assert "PATH" in env, "Path always set, with default if nothing else available"
    for expected_dir, found_dir in zip(expected_order, env["PATH"].split(":")):
        assert expected_dir == found_dir, "Expected sane default path order"


def test_loads_user_environment(successful_exec_from_mocked_root, randomstring):
    mock_os, conf_dir, *_, em = successful_exec_from_mocked_root

    sentinel_key = randomstring()
    expected_env = {sentinel_key: randomstring()}
    (conf_dir / "user_environment.yaml").write_text(yaml.dump(expected_env))
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    a, k = mock_os.execvpe.call_args
    env = k["env"]
    assert sentinel_key in env
    assert env[sentinel_key] == expected_env[sentinel_key]


def test_handles_invalid_user_environment_file_gracefully(
    successful_exec_from_mocked_root, mocker
):
    _mock_os, conf_dir, *_, em = successful_exec_from_mocked_root
    mock_warn = mocker.patch(f"{_MOCK_BASE}log.warning")

    env_path = conf_dir / "user_environment.yaml"
    env_path.write_text("\nalkdhj: g\nkladhj - asdf -asd f")
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()
    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    a, k = mock_warn.call_args_list[0]
    assert "Failed to parse user environment variables" in a[0]
    assert env_path in a, "Expected pointer to problem file in warning"
    assert "ScannerError" in a, "Expected exception name in warning"


def test_environment_default_path_set_if_not_specified(
    successful_exec_from_mocked_root,
):
    mock_os, conf_dir, *_, em = successful_exec_from_mocked_root

    expected_env = {"some_env_var": "some value"}
    (conf_dir / "user_environment.yaml").write_text(yaml.dump(expected_env))
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    a, k = mock_os.execvpe.call_args
    env = k["env"]
    assert "PATH" in env, "Expected PATH is always set"


def test_warns_if_environment_file_not_found(successful_exec_from_mocked_root, caplog):
    _, conf_dir, *_, em = successful_exec_from_mocked_root

    conf_path = conf_dir / "user_environment.yaml"
    conf_path.unlink(missing_ok=True)
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    assert f"No user environment variable file found at {conf_path}" in caplog.text


def test_warns_if_environment_file_empty(successful_exec_from_mocked_root, caplog):
    _, conf_dir, *_, em = successful_exec_from_mocked_root

    conf_path = conf_dir / "user_environment.yaml"
    conf_path.write_text("")
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    assert f"User environment variable file at {conf_path} is empty" in caplog.text


def test_warns_if_executable_not_found(
    mock_log, successful_exec_from_mocked_root, randomstring
):
    mock_os, conf_dir, *_, em = successful_exec_from_mocked_root
    exc_text = f"Some error: {randomstring()}"
    mock_os.execvpe.side_effect = Exception(exc_text)
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)

    expected_env = {"PATH": "/some/typoed:/path:/here"}
    (conf_dir / "user_environment.yaml").write_text(yaml.dump(expected_env))
    with pytest.raises(SystemExit) as pyt_exc:
        em._event_loop()

    assert mock_os.execvpe.called, "Expect that exec attempted, even after warning"
    ec = pyt_exc.value.code

    assert mock_log.warning.called
    a, _k = mock_log.warning.call_args
    assert "Unable to find executable" in a[0], "Expected precise problem in warning"
    assert "(not found):" in a[0]
    assert "globus-compute-endpoint" in a[0], "Share the precise thing not-found"
    assert expected_env["PATH"] in a[0]

    assert mock_log.error.called
    a, _k = mock_log.error.call_args
    assert (
        "Unable to start user endpoint" in a[0]
    ), "Expect attempt to log, even if fds closed"
    assert f" [exit code: {ec};" in a[0], "Expect exit code for debugging"
    assert exc_text in a[0]

    assert em.send_failure_notice.called

    _, k = em.send_failure_notice.call_args
    assert k["msg"] is a[0], "Expected a real error message clue-to-user"
    assert not k.get("fork", True)


def test_start_endpoint_children_die_with_parent(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    a, k = mock_os.execvpe.call_args
    assert a[0] == "globus-compute-endpoint", "Sanity check"
    assert k["args"][0] == a[0], "Expect transparency for admin"
    assert any("--die-with-parent" == i for i in k["args"]), "trust flag does the work"


def test_start_endpoint_children_have_own_session(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    assert mock_os.setsid.called


def test_start_endpoint_privileges_dropped(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    expected_user = _mock_localuser_rec.pw_name
    expected_gid = _mock_localuser_rec.pw_gid
    expected_uid = _mock_localuser_rec.pw_uid
    assert mock_os.initgroups.called
    (uname, gid), _ = mock_os.initgroups.call_args
    assert uname == expected_user
    assert gid == expected_gid

    assert mock_os.setresgid.called, "Do NOT save gid; truly change user"
    a, _ = mock_os.setresgid.call_args
    assert a == (expected_gid, expected_gid, expected_gid)

    assert mock_os.setresuid.called, "Do NOT save uid; truly change user"
    a, _ = mock_os.setresuid.call_args
    assert a == (expected_uid, expected_uid, expected_uid)


@pytest.mark.parametrize(
    "priv_func,ec",
    (
        # manual accounting for exit code at failure points
        ("setresgid", 71),
        ("initgroups", 72),
        ("setresuid", 73),
    ),
)
def test_start_endpoint_drop_privileges_fails_dies(
    mocker, priv_func, ec, successful_exec_from_mocked_root, randomstring
):
    exc_text = randomstring()
    exc = MemoryError(exc_text)
    mock_os, *_, em = successful_exec_from_mocked_root
    mock_log = mocker.patch(f"{_MOCK_BASE}log")
    mock_log.getEffectiveLevel.return_value = random.randint(0, 60)  # some int

    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)
    mocked_func = getattr(mock_os, priv_func)
    mocked_func.side_effect = exc
    with pytest.raises(SystemExit) as pyt_e:
        em._event_loop()

    found_ec = pyt_e.value.code
    a, _k = mock_log.error.call_args
    assert f"({type(exc).__name__})" in a[0]
    assert exc_text in a[0]
    assert found_ec == ec, "Expect each point of failure to increase exit code"
    assert mocked_func.called
    assert em.send_failure_notice.called, "Expect privilege failure to send notice"


def test_start_endpoint_paranoid_reassumption_check(
    mocker, successful_exec_from_mocked_root
):
    mock_os, *_, em = successful_exec_from_mocked_root
    mock_os.setuid.side_effect = None
    mock_log = mocker.patch(f"{_MOCK_BASE}log")
    mock_log.getEffectiveLevel.return_value = random.randint(0, 60)  # some int
    em.send_failure_notice = mock.Mock(spec=em.send_failure_notice)

    with pytest.raises(SystemExit):
        em._event_loop()

    assert em.send_failure_notice.called, "Expect privilege failure to send notice"
    _a, k = em.send_failure_notice.call_args
    m = k["msg"]
    exp_msg = "failed to start endpoint]"
    assert m.endswith(exp_msg), "Expect terse user msg, that doesn't include details"

    a, _k = mock_log.critical.call_args
    assert "regained original privileges" in a[0], "Expect explanation in logs"


def test_start_endpoint_logs_to_std(mocker, successful_exec_from_mocked_root):
    *_, em = successful_exec_from_mocked_root
    mock_logging = mocker.patch("globus_compute_endpoint.logging_config.logging")
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    log_config = mock_logging.config.dictConfig.call_args[0][0]
    handlers = log_config["handlers"]
    assert "console" in handlers, "Test setup: verify expected structure"
    assert "logfile" not in handlers, "Expect only use stdout or stderr"


def test_run_as_same_user_disabled_if_admin(
    mocker, conf_dir, mock_conf, mock_client, mock_pim
):
    ep_uuid, mock_gcc = mock_client

    mock_pwd = mocker.patch(f"{_MOCK_BASE}pwd")
    mock_prctl = mocker.patch(f"{_MOCK_BASE}_import_pyprctl")
    mock_prctl.return_value = mock_prctl
    mock_prctl.CapState.get_current.return_value.effective = set()

    mock_pwd.getpwuid.return_value = namedtuple("getent", "pw_name,pw_uid")("asdf", 0)
    em = EndpointManager(conf_dir, ep_uuid, mock_conf)
    assert em._allow_same_user is False, "Verify check against UID 0"

    mock_pwd.getpwuid.return_value = namedtuple("getent", "pw_name,pw_uid")("root", 999)
    em = EndpointManager(conf_dir, ep_uuid, mock_conf)
    assert em._allow_same_user is False, "Verify check against 'root' username"


@pytest.mark.parametrize("cap", (pyprctl.Cap.SYS_ADMIN, pyprctl.Cap.SETUID))
def test_run_as_same_user_disabled_if_privileged(
    mocker, conf_dir, mock_conf_root, mock_client, cap
):
    # spot-check a couple of capabilities: if set, then same user is *disallowed*
    ep_uuid, mock_gcc = mock_client

    _test_mock_base = "globus_compute_endpoint.endpoint.utils."
    mocker.patch(f"{_test_mock_base}_pwd")
    mock_prctl = mocker.patch(f"{_test_mock_base}_pyprctl")

    mock_prctl.CapState.get_current.return_value.effective = {cap}
    em = EndpointManager(conf_dir, ep_uuid, mock_conf_root)
    assert em._allow_same_user is False


def test_run_as_same_user_enabled_if_not_admin(
    mocker, conf_dir, mock_conf, mock_client
):
    # spot-check a couple of capabilities: if set, then same user is *disallowed*
    ep_uuid, mock_gcc = mock_client

    _test_mock_base = "globus_compute_endpoint.endpoint.utils."
    mocker.patch(f"{_test_mock_base}_pwd")
    mocker.patch(f"{_test_mock_base}_pyprctl")

    em = EndpointManager(conf_dir, ep_uuid, mock_conf)
    assert em._allow_same_user is True, "If not privileged, can only runas same user"


@pytest.mark.parametrize("isatty", (True, False))
def test_run_as_same_user_forced_warns(
    mocker, isatty, conf_dir, mock_conf, mock_client, randomstring
):
    # spot-check a couple of capabilities: if set, then same user is *disallowed*
    ep_uuid, mock_gcc = mock_client

    mocker.patch(f"{_MOCK_BASE}pwd")
    mock_os = mocker.patch(f"{_MOCK_BASE}os")
    mock_os.stderr.isatty.return_value = isatty
    mock_warn = mocker.patch(f"{_MOCK_BASE}log.warning")
    mocker.patch(f"{_MOCK_BASE}print")

    _test_mock_base = "globus_compute_endpoint.endpoint.utils."
    mocker.patch(f"{_test_mock_base}_pwd")
    mock_prctl = mocker.patch(f"{_test_mock_base}_pyprctl")

    mock_prctl.CapState.get_current.return_value.effective = {pyprctl.Cap.SYS_ADMIN}
    em = EndpointManager(conf_dir, ep_uuid, mock_conf)
    assert em._allow_same_user is False, "Verify test setup"
    assert not any(
        "force_mu_allow_same_user" in a[0] for a, _ in mock_warn.call_args_list
    ), "Verify test setup"

    mock_uid, mock_gid = randomstring(), randomstring()
    mock_os.getuid.return_value = mock_uid
    mock_os.getgid.return_value = mock_gid
    mock_conf.force_mu_allow_same_user = True
    mock_warn.reset_mock()
    em = EndpointManager(conf_dir, ep_uuid, mock_conf)
    assert em._allow_same_user is True
    assert mock_warn.called

    a, _k = mock_warn.call_args
    a = next(
        a[0] for a, _ in mock_warn.call_args_list if "force_mu_allow_same_user" in a[0]
    )
    assert "`force_mu_allow_same_user` set to `true`" in a
    assert "very dangerous override" in a
    assert "Endpoint (UID, GID):" in a, "Expect process UID, GID in warning"
    assert f"({mock_uid}, {mock_gid})" in a, "Expect process UID, GID in warning"
    if isatty:
        a = next(a[0] for a, _ in mock_warn.call_args_list if "dangerous" in a[0])
        assert a is not None, "Superfluous assert: ensure warning printed for human eye"
        assert "`force_mu_allow_same_user` set to `true`" in a
        assert "very dangerous override" in a
        assert "Endpoint (UID, GID):" in a, "Expect process UID, GID in warning"
        assert f"({mock_uid}, {mock_gid})" in a, "Expect process UID, GID in warning"


def test_run_as_same_user_fails_if_admin(successful_exec_from_mocked_root):
    *_, em = successful_exec_from_mocked_root

    em._allow_same_user = False  # just to be explicit
    mpi = MappedPosixIdentity(em._mu_user, [], str(uuid.uuid4()))
    kwargs = {"name": "some_endpoint_name"}
    with pytest.raises(InvalidUserError) as pyexc:
        em.cmd_start_endpoint(mpi, None, kwargs)

    e_str = str(pyexc.value)
    assert "UID is same as" in e_str
    assert "using a non-root user" in e_str, "Expected suggested fix"
    assert "removing privileges" in e_str, "Expected suggested fix"
    assert "\n  MU Process UID: 0 (root)" in e_str
    assert "\n  Requested UID:  0" in e_str
    assert f"\n  Via identity:   {mpi.matched_identity}" in e_str


def test_run_as_same_user_does_not_change_uid(successful_exec_from_mocked_root):
    mock_os, *_, mock_pwd, em = successful_exec_from_mocked_root
    mock_pwd.getpwnam.return_value = em._mu_user
    mock_pwd.getpwnam.side_effect = None

    em._allow_same_user = True
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == 85, "Q&D: verify we exec'ed, but no privilege drop"

    assert not mock_os.initgroups.called
    assert not mock_os.setresuid.called
    assert not mock_os.setresgid.called


def test_default_to_secure_umask(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    assert mock_os.umask.called
    umask = mock_os.umask.call_args[0][0]
    assert umask == 0o77


def test_start_from_user_dir(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    udir = mock_os.chdir.call_args[0][0]
    expected_udir = _mock_localuser_rec.pw_dir
    assert udir == expected_udir


def test_ep_info_contains_candidates(successful_exec_from_mocked_root, ident):
    mock_os, *_, em = successful_exec_from_mocked_root

    m = mock.Mock()
    mock_os.fdopen.return_value.__enter__.return_value = m
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    exp_identities = em.identity_mapper.map_identities.return_value
    a, _ = m.write.call_args
    stdin_data = json.loads(a[0])
    ep_info = stdin_data.get("ep_info")
    assert ep_info is not None
    assert set(_ep_info_known_keys) == set(ep_info)
    assert ep_info["posix_ppid"] == mock_os.getppid()
    assert ep_info["globus_candidate_identities"] == exp_identities
    assert ep_info["globus_matched_identity"] == ident


def test_ep_info_not_root_gets_no_matched_identity(
    epmanager_as_user, mock_props, randomstring, mock_auth_client
):
    *_, mock_os, _, em = epmanager_as_user
    ident_rv = mock_auth_client.userinfo.return_value

    cmd_payload = {
        "globus_effective_identity": "abc",
        "globus_identity_set": ident_rv["identity_set"],
        "globus_username": "a@b.com",
        "command": "cmd_start_endpoint",
        "kwargs": {"name": "some_ep_name", "user_opts": {"heartbeat": 10}},
    }
    queue_item = (1, mock_props, json.dumps(cmd_payload).encode())
    em._command_queue.get.side_effect = (queue_item, queue.Empty())

    m = mock.Mock()
    mock_os.fdopen.return_value.__enter__.return_value = m
    with pytest.raises(SystemExit):
        em._event_loop()

    assert mock_os.execvpe.called, "Achieved exec()"

    a, _ = m.write.call_args
    stdin_data = json.loads(a[0])
    ep_info = stdin_data.get("ep_info")
    assert ep_info is not None
    assert set(ep_info.keys()) == {"posix_ppid"}, "Expect exactly 1 key; update tests?"
    assert ep_info["posix_ppid"] == mock_os.getppid()


def test_all_files_closed(successful_exec_from_mocked_root):
    mock_os, *_, em = successful_exec_from_mocked_root
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    _soft_no, hard_no = resource.getrlimit(resource.RLIMIT_NOFILE)
    assert mock_os.closerange.called
    (low, hi), _ = mock_os.closerange.call_args
    assert low == 3, "Starts from first FD number after std* files"
    assert low < hi
    assert hi >= hard_no, "Expect ALL open files closed"

    assert mock_os.dup2.call_count == 3, "Expect to close 3 std* files"
    closed = [std_to_close for (_fd, std_to_close), _ in mock_os.dup2.call_args_list]
    assert closed == [0, 1, 2]


@pytest.mark.parametrize("is_valid", (True, False))
def test_pipe_size_limit(mocker, mock_log, successful_exec_from_mocked_root, is_valid):
    *_, em = successful_exec_from_mocked_root

    stdin_data_size = 226  # Empirically/designed size of `stdin_data` string
    pipe_buffer_size = 255 + stdin_data_size + is_valid  # manufacture error/success

    conf_str = "k: v"  # some key, some value; valid YAML string
    mocker.patch.object(fcntl, "fcntl", return_value=pipe_buffer_size)
    mocker.patch(f"{_MOCK_BASE}render_config_user_template", return_value=conf_str)

    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    if is_valid:
        assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    else:
        assert pyexc.value.code < _GOOD_EC
        assert f"{stdin_data_size} bytes" in mock_log.error.call_args[0][0]


def test_able_to_render_user_config_sc28360(successful_exec_from_mocked_root, conf_dir):
    def _remove_user_config_template(*args, **kwargs):
        shutil.rmtree(conf_dir)

    mock_os, *_, em = successful_exec_from_mocked_root

    # simulate no-permission-access to root-owned directory by removing entire dir
    mock_os.setresuid.side_effect = _remove_user_config_template
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"


def test_mep_not_restricted_uep_allowed_functions_not_overridden(
    successful_exec_from_mocked_root, mock_conf_root
):
    mock_os, *_, em = successful_exec_from_mocked_root

    m = mock.Mock()
    mock_os.fdopen.return_value.__enter__.return_value = m
    mock_conf_root.allowed_functions = None  # just be explicit, despite default
    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    (received_stdin,), _k = m.write.call_args
    parsed_stdin = json.loads(received_stdin)

    # another test verifies when allowed_functions *is* set
    assert "allowed_functions" not in parsed_stdin


@pytest.mark.parametrize("fn_count", (0, 1, 2, 3, random.randint(4, 100)))
def test_set_uep_allowed_functions(
    successful_exec_from_mocked_root, mock_conf_root, fn_count
):
    mock_os, *_, em = successful_exec_from_mocked_root

    m = mock.Mock()
    mock_os.fdopen.return_value.__enter__.return_value = m

    fns = [str(uuid.uuid4()) for _ in range(fn_count)]
    mock_conf_root.allowed_functions = fns
    with mock.patch.object(fcntl, "fcntl", return_value=2**20):
        # 2**20 == plenty for test
        with pytest.raises(SystemExit) as pyexc:
            em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    (received_stdin,), _k = m.write.call_args
    parsed_stdin = json.loads(received_stdin)
    assert "allowed_functions" in parsed_stdin, "Even empty list should be stated"
    assert parsed_stdin["allowed_functions"] == fns


def test_redirect_stdstreams_to_user_log(
    successful_exec_from_mocked_root, conf_dir, command_payload
):
    mock_os, *_, em = successful_exec_from_mocked_root

    mock_os.O_WRONLY = 0x1
    mock_os.O_APPEND = 0x2
    mock_os.O_SYNC = 0x4
    mock_os.O_CREAT = 0x8
    exp_flags = mock_os.O_CREAT | mock_os.O_WRONLY | mock_os.O_APPEND | mock_os.O_SYNC

    uep_name = command_payload["kwargs"]["name"]
    uep_dir = mock_ensure_compute_dir() / uep_name
    ep_log = uep_dir / "endpoint.log"

    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    a, k = next((a, k) for a, k in mock_os.open.call_args_list if a[0] == ep_log)
    assert a[1] == exp_flags, "Expect replacement stdout/stderr: append, wronly, sync"
    assert k["mode"] == 0o600, "Expect default to writable *and* readable"


@pytest.mark.parametrize("debug", (True, False))
def test_user_debug_emits_ephemeral_config_to_user_log(
    mocker, mock_log, successful_exec_from_mocked_root, conf_dir, command_payload, debug
):
    mock_os, *_, em = successful_exec_from_mocked_root

    template_path = Endpoint.user_config_template_path(conf_dir)

    if debug:
        with open(template_path, "a") as f:
            f.write("\ndebug: true")

    def duped_first_check(*a, **k):
        assert mock_os.dup2.call_count == 3, "3 std streams; to log file, not stdout"

    mock_print = mocker.patch(f"{_MOCK_BASE}print")
    mock_print.side_effect = duped_first_check

    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"

    assert mock_print.called is debug, "Expect only written if `debug: true` set"
    if debug:
        template = template_path.read_text()
        exp_lines = template.count("\n") + 1  # +1 ==> \n *splits* lines
        a, _k = mock_print.call_args
        c = a[0]
        assert "DEBUG" in c
        assert " Endpoint Begin Compute endpoint" in c, "Expect sentinel begin"
        assert "\nEnd Compute endpoint configuration" in c, "Expect sentinel end"
        assert f"({exp_lines:,} lines)" in c, "Expect line count"


@pytest.mark.parametrize("port", [random.randint(0, 65535)])
def test_port_is_respected(mocker, mock_client, mock_conf, conf_dir, port):
    ep_uuid, _ = mock_client
    mock_conf.amqp_port = port

    mock_update_url_port = mocker.patch(f"{_MOCK_BASE}update_url_port")

    EndpointManager(conf_dir, ep_uuid, mock_conf)

    assert mock_update_url_port.call_args[0][1] == port


@pytest.mark.parametrize(
    "fn_name,pam_enable",
    (
        ("_import_pam", True),
        ("_import_pyprctl", False),
    ),
)
def test_conditional_imports_verified_at_init_for_ux(
    conf_dir, mock_conf, ep_uuid, mock_reg_info, mock_ctl, fn_name, pam_enable
):
    mock_conf.pam.enable = pam_enable
    with mock.patch(f"{_MOCK_BASE}{fn_name}") as m:
        m.side_effect = MemoryError("test induced")
        with pytest.raises(MemoryError):
            EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)


def test_pam_disabled(conf_dir, mock_conf, ep_uuid, mock_reg_info, mock_ctl, mock_pam):
    em = EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)

    mock_conf.pam.enable = False
    with em.do_host_auth("some user name"):
        pass
    assert not mock_pam.called, "PAM was disable; should *not* attempt PAM"
    assert mock_ctl.CapState.called, "No PAM?  No privileges."
    assert mock_ctl.set_no_new_privs.called, "No PAM?  No privileges."


def test_pam_enabled(conf_dir, mock_conf, ep_uuid, mock_reg_info, mock_ctl, mock_pam):
    def install_next_pamf():
        # ensure PAM functions called in appropriate order
        fns = [  # reversed because we pop() to get each fn
            "credentials_delete",
            "pam_close_session",
            "pam_open_session",
            "credentials_establish",
        ]

        def _install_next_test_func():
            if not fns:
                return
            fn = fns.pop()
            getattr(pamh, fn).side_effect = _install_next_test_func

        return _install_next_test_func

    mock_conf.pam.enable = True
    pamh = mock_pam.PamHandle
    pamh.pam_acct_mgmt.side_effect = install_next_pamf()
    pamh.credentials_establish.side_effect = AssertionError("Out of order")
    pamh.pam_open_session.side_effect = AssertionError("Out of order")
    pamh.pam_close_session.side_effect = AssertionError("Out of order")
    pamh.credentials_delete.side_effect = AssertionError("Out of order")

    em = EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)
    with em.do_host_auth("some user name"):
        assert pamh.pam_open_session.called, "Complete authentication"
        assert not pamh.credentials_delete.called, "PAM session *not* over yet"
    assert pamh.credentials_delete.called, "PAM session completes"

    assert not mock_ctl.CapState.called, "Using PAM; admin manages privs"
    assert not mock_ctl.set_no_new_privs.called, "Using PAM; admin manages privs"


@pytest.mark.parametrize(
    "fn_name",
    (
        "pam_acct_mgmt",
        "credentials_establish",
        "pam_open_session",
        "pam_close_session",
        "credentials_delete",
    ),
)
@pytest.mark.parametrize("exc", (MockPamError("test err"), MemoryError("test err")))
def test_pam_error(
    mock_log, conf_dir, mock_conf, ep_uuid, mock_reg_info, fn_name, mock_pam, exc
):
    em = EndpointManager(conf_dir, ep_uuid, mock_conf, mock_reg_info)

    mock_conf.pam.enable = True
    pamh = mock_pam.PamHandle
    username = "some username"
    getattr(pamh, fn_name).side_effect = exc
    with pytest.raises(PermissionError) as pyt_e:
        with em.do_host_auth(username):
            pass

    e_str = str(pyt_e.value)
    assert "PAM" not in e_str, "User-visible exception should be opaque"
    assert "see your system administrator" in e_str, "User-visible should have action"

    if not isinstance(exc, MockPamError):
        assert mock_log.exception.called, "Admin log should contain entire exception"
        a, _k = mock_log.exception.call_args

        assert username in a[0], "Admin log should contain related username"


def test_do_auth_change_uid_then_close(
    mock_conf_root, successful_exec_from_mocked_root, mock_pam
):
    mock_os, *_, em = successful_exec_from_mocked_root

    def this_func(fn_opener, fn_name: str):
        def _mark_called(*_a, **_k):
            fn_opener(fn_name)

        return _mark_called

    def set_called():
        fn_calls = {"setresuid", "setresgid", "initgroups"}

        def _called(fn_name):
            if fn_name == "pam_open_session":
                # these are now allowed
                mock_os.setresuid.side_effect = this_func(fn_opener, "setresuid")
                mock_os.setresgid.side_effect = this_func(fn_opener, "setresgid")
                mock_os.initgroups.side_effect = this_func(fn_opener, "initgroups")
                return

            fn_calls.discard(fn_name)
            if not fn_calls:
                # once we've become the new user, pam session may close
                pamh.pam_close_session.side_effect = None

        return _called

    mock_conf_root.pam.enable = True
    fn_opener = set_called()

    pamh = mock_pam.PamHandle
    pamh.pam_open_session.side_effect = this_func(fn_opener, "pam_open_session")
    pamh.pam_close_session.side_effect = AssertionError("Out of order")
    mock_os.setresuid.side_effect = AssertionError("Out of order")
    mock_os.setresgid.side_effect = AssertionError("Out of order")
    mock_os.initgroups.side_effect = AssertionError("Out of order")

    with pytest.raises(SystemExit) as pyexc:
        em._event_loop()

    assert pyexc.value.code == _GOOD_EC, "Q&D: verify we exec'ed, based on '+= 1'"
    assert pamh.pam_close_session.called
