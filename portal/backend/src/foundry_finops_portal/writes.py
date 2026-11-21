"""Changes the portal makes for a person. The portal's own identity performs the change, so it first checks twice that
the person may make it: the portal's grants (an owner with write access to that scope) and Azure RBAC (their own
token must be allowed the action at that resource). Every attempt is written to the audit log."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from .access import Access, ScopeRef
from .auth import Principal
from .azure_access import PermissionChecker
from .config import PortalSettings
from .db import Store

COST_CONTROL_WRITE = "Microsoft.CognitiveServices/accounts/costControls/write"
COST_CONTROL_DELETE = "Microsoft.CognitiveServices/accounts/costControls/delete"
PROJECT_COST_CONTROL_WRITE = "Microsoft.CognitiveServices/accounts/projects/costControls/write"
PROJECT_COST_CONTROL_DELETE = "Microsoft.CognitiveServices/accounts/projects/costControls/delete"
BUDGET_WRITE = "Microsoft.Consumption/budgets/write"
BUDGET_DELETE = "Microsoft.Consumption/budgets/delete"
TAGS_WRITE = "Microsoft.Resources/tags/write"


class WriteGuard:
    def __init__(self, settings: PortalSettings, store: Store, permissions: PermissionChecker | None) -> None:
        self.settings, self.store, self.permissions = settings, store, permissions

    # resource IDs of what the portal manages
    @property
    def rg_id(self) -> str:
        s = self.settings
        return f"/subscriptions/{s.subscription_id}/resourceGroups/{s.resource_group}"

    @property
    def account_id(self) -> str:
        return f"{self.rg_id}/providers/Microsoft.CognitiveServices/accounts/{self.settings.foundry_account}"

    def cost_control_target(self, project: str | None, delete: bool = False) -> tuple[str, str]:
        if project:
            return (f"{self.account_id}/projects/{project}",
                    PROJECT_COST_CONTROL_DELETE if delete else PROJECT_COST_CONTROL_WRITE)
        return self.account_id, COST_CONTROL_DELETE if delete else COST_CONTROL_WRITE

    def authorize(self, principal: Principal, access: Access, *, ref: ScopeRef, resource_id: str, action: str,
                  label: str, detail: dict[str, Any] | None = None) -> None:
        """Raise 403 unless the person may do this. Audits a refusal as well as a success (see :meth:`done`)."""
        reason = None
        if self.settings.read_only:
            reason = "The portal is read-only"
        elif not access.can_write(ref):
            reason = "Your portal access doesn't include changing this. Ask an administrator for write access to this scope."
        elif not self.settings.demo:
            assert self.permissions is not None
            try:
                allowed = self.permissions.allows(principal, resource_id, action)
            except Exception as exc:
                raise HTTPException(502, f"Could not check your Azure permissions: {exc}") from exc
            if not allowed:
                reason = f"Azure RBAC doesn't allow you {action} at this scope."
        if reason:
            self.store.audit(principal.oid, principal.upn, label, resource_id, {**(detail or {}), "reason": reason}, "denied")
            raise HTTPException(403, reason)

    def done(self, principal: Principal, label: str, target: str, detail: dict[str, Any] | None = None,
             error: str | None = None) -> None:
        self.store.audit(principal.oid, principal.upn, label, target, {**(detail or {}), **({"error": error} if error else {})},
                         "error" if error else "ok")
