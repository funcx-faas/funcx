from __future__ import annotations

import logging
import warnings

import globus_sdk
from globus_compute_sdk.sdk.login_manager.manager import LoginManager
from globus_compute_sdk.sdk.login_manager.protocol import LoginManagerProtocol
from globus_compute_sdk.sdk.web_client import WebClient
from globus_sdk.scopes import AuthScopes

from ..auth.scopes import ComputeScopes

log = logging.getLogger(__name__)


class AuthorizerLoginManager(LoginManagerProtocol):
    """
    Implements a LoginManager that can be instantiated with authorizers.
    This manager can be used to create an Executor with authorizers created
    from previously acquired tokens, rather than requiring a Native App login
    flow or Client credentials.
    """

    def __init__(self, authorizers: dict[str, globus_sdk.RefreshTokenAuthorizer]):
        warnings.warn(
            "The `AuthorizerLoginManager` is deprecated. Please use `GlobusApp` objects"
            "from the Globus SDK instead:"
            " https://globus-compute.readthedocs.io/en/stable/sdk.html#globusapps",
            category=DeprecationWarning,
            stacklevel=2,
        )
        self.authorizers = authorizers

    def get_auth_client(self) -> globus_sdk.AuthClient:
        return globus_sdk.AuthClient(
            authorizer=self.authorizers[AuthScopes.resource_server]
        )

    def get_web_client(
        self, *, base_url: str | None = None, app_name: str | None = None
    ) -> WebClient:
        return WebClient(
            base_url=base_url,
            app_name=app_name,
            authorizer=self.authorizers[ComputeScopes.resource_server],
        )

    def ensure_logged_in(self):
        """Ensure authorizers for each of the required scopes are present."""

        for server in LoginManager.SCOPES:
            if server not in self.authorizers:
                log.error(f"Required authorizer for {server} is not present.")
                raise LookupError(
                    f"{type(self).__name__} could not find authorizer for {server}"
                )

    def logout(self):
        log.warning(f"Logout cannot be invoked from an {type(self).__name__}.")
