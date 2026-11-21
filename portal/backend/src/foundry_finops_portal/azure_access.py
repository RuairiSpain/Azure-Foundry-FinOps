"""Azure access: the signed-in person's own identity for reads (on-behalf-of), the portal's identity for writes,
and the check that the person may do what they ask before the portal does it for them."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

import msal
import requests
from azure.core.credentials import AccessToken
from azure.core.exceptions import ClientAuthenticationError
from foundry_finops import FinOpsClient
from foundry_finops.roles import Permission

from .auth import Principal
from .config import PortalSettings

ARM = "https://management.azure.com"
PERMISSIONS_API = "2022-04-01"


class OboCredential:
    """A ``TokenCredential`` that exchanges the person's access token for a token for the requested Azure resource
    (the OAuth on-behalf-of flow). Azure then sees the person, so Azure RBAC limits what comes back."""

    def __init__(self, app: Any, assertion: str) -> None:
        self._app = app
        self._assertion = assertion

    def get_token(self, *scopes: str, **kwargs: Any) -> AccessToken:
        result = self._app.acquire_token_on_behalf_of(user_assertion=self._assertion, scopes=list(scopes))
        if "access_token" not in result:
            raise ClientAuthenticationError(
                f"On-behalf-of exchange failed: {result.get('error')}: {result.get('error_description', '')[:200]}")
        return AccessToken(result["access_token"], int(time.time()) + int(result.get("expires_in", 3000)))


class ClientFactory(Protocol):
    def for_user(self, principal: Principal) -> FinOpsClient: ...
    def service(self) -> FinOpsClient: ...
    def permissions(self) -> PermissionChecker: ...


class PermissionChecker(Protocol):
    def allows(self, principal: Principal, resource_id: str, action: str) -> bool:
        """``True`` if Azure says this person may perform ``action`` at ``resource_id``."""


def _evaluate(payload: dict[str, Any], action: str) -> bool:
    perms = [Permission(tuple(p.get("actions") or ()), tuple(p.get("notActions") or ()))
             for p in payload.get("value", [])]
    return any(p.allows(action) for p in perms)


class ArmPermissionChecker:
    """Asks Azure Resource Manager what the person may do at a scope (``Microsoft.Authorization/permissions``)
    using the person's own token, so the answer is theirs and not the portal's."""

    def __init__(self, obo_token: Callable[[Principal], str], session: requests.Session | None = None) -> None:
        self._token = obo_token
        self._session = session or requests.Session()

    def allows(self, principal: Principal, resource_id: str, action: str) -> bool:
        if not resource_id.startswith("/subscriptions/") or any(c in resource_id for c in "?#\\") or ".." in resource_id:
            raise ValueError("resource_id must be an Azure resource ID")
        resp = self._session.get(
            f"{ARM}{resource_id}/providers/Microsoft.Authorization/permissions",
            params={"api-version": PERMISSIONS_API}, headers={"Authorization": f"Bearer {self._token(principal)}"},
            timeout=20)
        if resp.status_code in (401, 403):
            return False
        resp.raise_for_status()
        return _evaluate(resp.json(), action)


class AzureClientFactory:
    """Builds the Microsoft-client-based ``FinOpsClient`` for a person (on-behalf-of) and for the portal itself."""

    def __init__(self, settings: PortalSettings) -> None:
        self.settings = settings
        self._app = msal.ConfidentialClientApplication(
            settings.client_id, client_credential=settings.client_secret,
            authority=f"https://login.microsoftonline.com/{settings.tenant_id}")
        self._service: FinOpsClient | None = None
        self._lock = threading.Lock()

    def for_user(self, principal: Principal) -> FinOpsClient:
        s = self.settings
        return FinOpsClient(OboCredential(self._app, principal.token), s.subscription_id, resource_group=s.resource_group)

    def service(self) -> FinOpsClient:
        with self._lock:
            if self._service is None:
                s = self.settings
                self._service = FinOpsClient.from_default_credential(s.subscription_id, resource_group=s.resource_group)
            return self._service

    def permissions(self) -> PermissionChecker:
        def token(principal: Principal) -> str:
            return str(OboCredential(self._app, principal.token).get_token(f"{ARM}/.default").token)

        return ArmPermissionChecker(token)
