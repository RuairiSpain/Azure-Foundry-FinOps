"""Azure RBAC reads: the signed-in identity's role assignments and the role definitions behind them."""

from __future__ import annotations

import base64
import json
from typing import Any

from ..errors import FinOpsError
from ._base import _Operations


class AuthorizationOperations(_Operations):
    """Read-only RBAC lookups (``Microsoft.Authorization``). Reader is enough to call them."""

    def principal_id(self) -> str:
        """Object ID of the signed-in identity, read from the ``oid`` claim of its own access token.

        The token is only decoded, never validated: it is our own credential and the claim is used to ask Azure
        which roles that identity holds.
        """
        token = self._t._tokens.bearer(self._cloud.arm_scope).removeprefix("Bearer ")
        try:
            payload = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (IndexError, ValueError) as exc:
            raise FinOpsError("The access token isn't a JWT, so the signed-in identity can't be determined") from exc
        oid = claims.get("oid")
        if not oid:
            raise FinOpsError("The access token has no 'oid' claim, so the signed-in identity can't be determined")
        return str(oid)

    def role_assignments(self, principal_id: str, scope: str | None = None) -> list[dict[str, Any]]:
        """Assignments that apply to the identity or to any group it belongs to, at the scope and its parents and
        children. ``scope`` defaults to the subscription."""
        flt = f"assignedTo('{principal_id}')"
        path = f"{scope or self.subscription_scope}/providers/Microsoft.Authorization/roleAssignments"
        return list(self._list(path, self._v.authorization, {"$filter": flt}))

    def role_definition(self, role_definition_id: str) -> dict[str, Any]:
        body = self._arm("GET", role_definition_id, self._v.authorization).body
        return body if isinstance(body, dict) else {}
