from __future__ import annotations

from itertools import chain, combinations
from unittest import mock

import pytest
from globus_compute_sdk.sdk.auth.scopes import ComputeScopes
from globus_compute_sdk.sdk.login_manager import AuthorizerLoginManager, LoginManager
from globus_compute_sdk.sdk.web_client import WebClient
from globus_sdk import AuthClient, RefreshTokenAuthorizer
from globus_sdk.scopes import AuthScopes

CID_KEY = "GLOBUS_COMPUTE_CLIENT_ID"
CSC_KEY = "GLOBUS_COMPUTE_CLIENT_SECRET"
_MOCK_BASE = "globus_compute_sdk.sdk.login_manager."


@pytest.fixture
def mock_authorizers():
    mock_authorizer = mock.Mock(spec=RefreshTokenAuthorizer)
    return {
        AuthScopes.resource_server: mock_authorizer,
        ComputeScopes.resource_server: mock_authorizer,
    }


@pytest.fixture
def logman(mocker, tmp_path):
    compute_dir = tmp_path / ".globus_compute"
    compute_dir.mkdir()
    mocker.patch(f"{_MOCK_BASE}tokenstore.ensure_compute_dir", return_value=compute_dir)
    return AuthorizerLoginManager({})


def test_authorizer_login_manager_deprecated():
    with pytest.warns(DeprecationWarning) as record:
        AuthorizerLoginManager({})
    msg = "The `AuthorizerLoginManager` is deprecated"
    assert any(msg in str(r.message) for r in record)


def test_auth_client_requires_authorizer_openid_scope(mocker):
    alm = AuthorizerLoginManager({})
    with pytest.raises(KeyError) as pyt_exc:
        alm.get_auth_client()
    assert AuthScopes.resource_server in str(pyt_exc)


def test_get_auth_client(mock_authorizers: dict[str, RefreshTokenAuthorizer]):
    alm = AuthorizerLoginManager(mock_authorizers)
    auth_client = alm.get_auth_client()
    assert isinstance(auth_client, AuthClient)
    assert auth_client.authorizer == mock_authorizers[AuthScopes.resource_server]


def test_get_web_client(mock_authorizers: dict[str, RefreshTokenAuthorizer]):
    alm = AuthorizerLoginManager(mock_authorizers)
    web_client = alm.get_web_client()
    assert isinstance(web_client, WebClient)
    assert web_client.authorizer == mock_authorizers[ComputeScopes.resource_server]


def test_does_not_open_local_cred_storage(
    mocker, randomstring, mock_authorizers: dict[str, RefreshTokenAuthorizer]
):
    mock_lm = mocker.patch(f"{_MOCK_BASE}authorizer_login_manager.LoginManager")
    mock_lm.SCOPES = {key: [randomstring()] for key in mock_authorizers.keys()}
    with pytest.raises(LookupError):
        AuthorizerLoginManager({}).ensure_logged_in()

    alm = AuthorizerLoginManager(mock_authorizers)
    assert alm.ensure_logged_in() is None, "Test setup: verified SCOPES is checked"
    assert not mock_lm.called, "Do not instantiate; do not create creds storage"


@pytest.mark.parametrize(
    "missing_keys",
    list(
        chain(
            combinations(LoginManager.SCOPES, 1),
            combinations(LoginManager.SCOPES, 2),
            [()],
        )
    ),
)
def test_ensure_logged_in(mocker, logman, missing_keys):
    _authorizers = dict(LoginManager.SCOPES)
    for k in missing_keys:
        _authorizers.pop(k, None)

    logman.authorizers = _authorizers

    if missing_keys:
        with pytest.raises(LookupError) as err:
            logman.ensure_logged_in()

        assert f"could not find authorizer for {missing_keys[0]}" in err.value.args[0]


def test_warns_upon_logout_attempts(mocker):
    mock_log = mocker.patch(f"{_MOCK_BASE}authorizer_login_manager.log")
    alm = AuthorizerLoginManager({})
    assert not mock_log.warning.called
    alm.logout()
    assert mock_log.warning.called
