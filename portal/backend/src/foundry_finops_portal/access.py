"""What a signed-in person may see and change, from their portal role and scope grants.

This is the portal's own layer. In live mode it comes on top of Azure RBAC, which still decides what Azure returns,
because reads run with the person's own token. A grant can only narrow what Azure already allows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Grant types that cover every project of the Foundry resource the portal manages.
ACCOUNT_WIDE = frozenset({"all", "subscription", "resource_group", "account"})


@dataclass(frozen=True)
class ScopeRef:
    """Where something lives, from the subscription down to a project, optionally with its cost centre."""

    subscription: str = ""
    resource_group: str = ""
    account: str = ""
    project: str | None = None
    cost_centre: str | None = None


@dataclass
class Access:
    """One person's rights, built once per request."""

    oid: str
    role: str
    grants: list[dict[str, Any]] = field(default_factory=list)
    #: Cost centre code -> project names its allocation rules name, for ``cost_centre`` grants.
    centre_projects: dict[str, set[str]] = field(default_factory=dict)
    #: The portal's own Azure scope; a grant for another subscription or account never matches.
    home: ScopeRef = field(default_factory=ScopeRef)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def _covers(self, g: dict[str, Any], ref: ScopeRef) -> bool:
        t, v = g["scope_type"], g["scope_value"]
        if t == "all":
            return True
        if t == "subscription":
            return bool(v == (ref.subscription or self.home.subscription))
        if t == "resource_group":
            return bool(v == (ref.resource_group or self.home.resource_group))
        if t == "account":
            return bool(v == (ref.account or self.home.account))
        if t == "project":
            return ref.project is not None and v == ref.project
        if t == "cost_centre":
            if ref.cost_centre is not None and v == ref.cost_centre:
                return True
            return ref.project is not None and ref.project in self.centre_projects.get(v, set())
        return False

    def can_read(self, ref: ScopeRef) -> bool:
        return self.is_admin or any(self._covers(g, ref) for g in self.grants)

    def can_write(self, ref: ScopeRef) -> bool:
        """Owners write where a grant says so; administrators write everywhere; viewers never write."""
        if self.is_admin:
            return True
        if self.role != "owner":
            return False
        return any(g.get("can_write") and self._covers(g, ref) for g in self.grants)

    def visible_projects(self, all_projects: list[str]) -> set[str] | None:
        """``None`` when every project is visible, else the visible project names (possibly none)."""
        if self.account_level:
            return None
        names: set[str] = set()
        for g in self.grants:
            if g["scope_type"] == "project":
                names.add(g["scope_value"])
            elif g["scope_type"] == "cost_centre":
                names |= self.centre_projects.get(g["scope_value"], set())
        return {p for p in all_projects if p in names} | {n for n in names if n in all_projects}

    @property
    def account_level(self) -> bool:
        """May see data that can't be split by project (quota, deployments, token metrics)."""
        # A grant for another subscription, resource group or account doesn't cover the Foundry resource we manage.
        return self.is_admin or any(g["scope_type"] in ACCOUNT_WIDE and self._covers(g, ScopeRef()) for g in self.grants)
