"""Work out which ``download`` queries the signed-in identity may run, from its real Azure RBAC.

``--roles auto`` reads the identity's role assignments and the permissions of each role, then checks them against
the actions every query needs **at the scope the query runs at**. Because it matches permissions rather than role
names, custom roles and roles this package has never heard of work too.

It is a pre-check to avoid pointless calls, not a security boundary: Azure still enforces access, and anything it
refuses shows up as ``failed`` in the manifest. Not modelled: deny assignments, ABAC conditions, and PIM-eligible
(not yet activated) roles, which are not active assignments.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .client import FinOpsClient
from .errors import FinOpsError


@dataclass(frozen=True)
class Requirement:
    """What a query needs: any one of ``alternatives``, each a tuple of actions that must ALL be allowed, at ``scope``.

    ``scope`` is one of ``subscription``, ``resource_group``, ``account``, ``apim`` or ``anywhere`` (the resource
    isn't known, e.g. a Log Analytics workspace given by GUID: any assignment inside the subscription counts).
    """

    alternatives: tuple[tuple[str, ...], ...]
    scope: str


def _need(*actions: str, scope: str) -> Requirement:
    return Requirement((tuple(actions),), scope)


_COG = "Microsoft.CognitiveServices"
#: Actions behind each ``download`` query. Keys are query names; saved KQL queries use ``KQL_REQUIREMENT``.
QUERY_REQUIREMENTS: dict[str, Requirement] = {
    **{n: _need("Microsoft.CostManagement/query/read", scope="account") for n in (
        "cost_daily_total", "cost_by_project", "cost_by_agent", "cost_by_deployment", "cost_by_team",
        "cost_by_model", "cost_by_meter")},
    "cost_forecast": _need("Microsoft.CostManagement/forecast/read", scope="account"),
    "cost_dimensions": _need("Microsoft.CostManagement/dimensions/read", scope="account"),
    "cost_exports": _need("Microsoft.CostManagement/exports/read", scope="account"),
    "budgets": _need("Microsoft.Consumption/budgets/read", scope="resource_group"),
    "cost_controls": _need(f"{_COG}/accounts/costControls/read", scope="account"),
    "foundry_account": _need(f"{_COG}/accounts/read", scope="account"),
    "foundry_projects": _need(f"{_COG}/accounts/projects/read", scope="account"),
    "foundry_deployments": _need(f"{_COG}/accounts/deployments/read", scope="account"),
    "foundry_connections": _need(f"{_COG}/accounts/connections/read", scope="account"),
    "foundry_event_grid_topics": _need("Microsoft.EventGrid/topics/read", scope="subscription"),
    "token_usage_by_deployment": _need("Microsoft.Insights/metrics/read", scope="account"),
    "token_usage_by_model": _need("Microsoft.Insights/metrics/read", scope="account"),
    "metric_definitions": _need("Microsoft.Insights/metricDefinitions/read", scope="account"),
    "quota_usage": _need(f"{_COG}/locations/usages/read", scope="subscription"),
    "quota_models": _need(f"{_COG}/locations/models/read", scope="subscription"),
    "tags": _need("Microsoft.Resources/tags/read", "Microsoft.Resources/subscriptions/resources/read",
                  scope="subscription"),
    "alerts": _need("Microsoft.Insights/metricAlerts/read", "Microsoft.Insights/scheduledQueryRules/read",
                    "Microsoft.Insights/actionGroups/read", scope="resource_group"),
    "apim_gateway": _need("Microsoft.ApiManagement/service/read", "Microsoft.ApiManagement/service/products/read",
                          "Microsoft.ApiManagement/service/subscriptions/read",
                          "Microsoft.ApiManagement/service/apis/read", scope="apim"),
}
#: Public price list: no role needed.
NO_ROLE_NEEDED = frozenset({"retail_price_sheet"})
KQL_REQUIREMENT = Requirement(
    (("Microsoft.OperationalInsights/workspaces/query/read",), ("Microsoft.Insights/components/query/read",)),
    "anywhere",
)


def requirement_for(query_name: str) -> Requirement | None:
    """``None`` for queries that need no role. Unlisted queries are treated as needing broad read access."""
    if query_name in NO_ROLE_NEEDED:
        return None
    if query_name in QUERY_REQUIREMENTS:
        return QUERY_REQUIREMENTS[query_name]
    if query_name.startswith("kql_"):
        return KQL_REQUIREMENT
    return _need("*/read", scope="subscription")


# ------------------------------------------------------------- permission matching
def _pattern(action: str) -> re.Pattern[str]:
    return re.compile(".*".join(re.escape(part) for part in action.split("*")), re.IGNORECASE)


@dataclass(frozen=True)
class Permission:
    actions: tuple[str, ...]
    not_actions: tuple[str, ...] = ()

    def allows(self, action: str) -> bool:
        """Azure's rule: some ``actions`` pattern matches and no ``notActions`` pattern does (``*`` is a wildcard)."""
        return (any(_pattern(a).fullmatch(action) for a in self.actions)
                and not any(_pattern(n).fullmatch(action) for n in self.not_actions))


@dataclass(frozen=True)
class RoleGrant:
    """One role assignment: a role (by name and permissions) held at a scope."""

    role_name: str
    scope: str
    permissions: tuple[Permission, ...]

    def allows(self, action: str) -> bool:
        return any(p.allows(action) for p in self.permissions)

    def covers(self, target: str) -> bool:
        """``True`` if the assignment's scope contains ``target`` (or is the root or a management group)."""
        mine, theirs = self.scope.rstrip("/").lower(), target.rstrip("/").lower()
        if mine in ("", "/") or "/providers/microsoft.management/managementgroups/" in mine:
            return True  # root / management group: the subscription sits beneath it
        return theirs == mine or theirs.startswith(mine + "/")

    def inside(self, ancestor: str) -> bool:
        """``True`` if the assignment's scope is within ``ancestor`` (used when the target scope is unknown)."""
        mine, top = self.scope.rstrip("/").lower(), ancestor.rstrip("/").lower()
        return self.covers(ancestor) or mine == top or mine.startswith(top + "/")


@dataclass
class RoleAccess:
    """The identity's active role assignments, and the check ``download`` applies to each query."""

    grants: list[RoleGrant]
    #: Scopes (resource IDs) the queries run at; ``scope`` kinds in :class:`Requirement` map to these.
    scopes: dict[str, str]

    def check(self, query_name: str) -> str | None:
        """``None`` if allowed, otherwise a short reason."""
        req = requirement_for(query_name)
        if req is None:
            return None
        target = self.scopes.get(req.scope, self.scopes["subscription"])
        for alt in req.alternatives:
            if all(self._allowed(action, req.scope, target) for action in alt):
                return None
        needs = " or ".join(" + ".join(alt) for alt in req.alternatives)
        where = "somewhere in the subscription" if req.scope == "anywhere" else f"at {target}"
        return f"your role assignments don't allow {needs} {where}"

    def _allowed(self, action: str, scope_kind: str, target: str) -> bool:
        for g in self.grants:
            if not g.allows(action):
                continue
            if g.inside(self.scopes["subscription"]) if scope_kind == "anywhere" else g.covers(target):
                return True
        return False

    def describe(self) -> list[dict[str, str]]:
        return [{"role": g.role_name, "scope": g.scope} for g in self.grants]


# ------------------------------------------------------------------- discovery
def discover(client: FinOpsClient, scopes: dict[str, str], on_progress: Callable[[str], None] | None = None) -> RoleAccess:
    """Read the signed-in identity's role assignments and role definitions from Azure.

    Needs ``Microsoft.Authorization/roleAssignments/read`` and ``roleDefinitions/read`` (the **Reader** role has
    both). Raises :class:`FinOpsError` with advice when they can't be read.
    """
    auth = client.authorization
    try:
        principal = auth.principal_id()
        assignments = auth.role_assignments(principal)
    except FinOpsError as exc:
        raise FinOpsError(
            f"Could not read your role assignments ({str(exc).splitlines()[0]}). "
            "Pass your roles explicitly instead, e.g. --roles \"Reader,Cost Management Reader\"."
        ) from exc
    grants: list[RoleGrant] = []
    definitions: dict[str, tuple[str, tuple[Permission, ...]]] = {}
    for a in assignments:
        props = a.get("properties") or {}
        role_id, scope = props.get("roleDefinitionId"), props.get("scope")
        if not role_id or scope is None:
            continue
        if role_id not in definitions:
            if on_progress:
                on_progress(role_id)
            d = auth.role_definition(role_id)
            dp = d.get("properties") or {}
            definitions[role_id] = (
                dp.get("roleName") or role_id.rsplit("/", 1)[-1],
                tuple(Permission(tuple(p.get("actions") or ()), tuple(p.get("notActions") or ()))
                      for p in dp.get("permissions") or ()),
            )
        name, perms = definitions[role_id]
        grants.append(RoleGrant(name, scope, perms))
    return RoleAccess(grants, scopes)


def summarize(grants: Iterable[RoleGrant]) -> list[str]:
    return [f"{g.role_name} at {g.scope}" for g in grants]
