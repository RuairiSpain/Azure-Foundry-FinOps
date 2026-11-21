"""Azure Policy compliance (``Microsoft.PolicyInsights``): the data behind the Foundry fleet's policy cards."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from typing import Any

from ._base import _Operations


class PolicyOperations(_Operations):
    """Read policy compliance for a scope. Needs ``Microsoft.PolicyInsights/*/read`` (the Foundry docs list it for the
    fleet's *Assets with policy violations* and *Most policy violations* cards).

    Policy Insights answers these reads with ``POST`` requests that read compliance data without changing anything.
    """

    def states(
        self,
        scope: str | None = None,
        *,
        non_compliant_only: bool = True,
        filter: str | None = None,
        top: int = 1000,
    ) -> Iterator[dict[str, Any]]:
        """Latest policy states under ``scope`` (default: the resource group, else the subscription).

        ``scope`` may also be a resource ID, such as a Foundry resource. ``filter`` is an OData filter, for example
        ``"PolicyDefinitionAction eq 'deny'"``; it is combined with ``IsCompliant eq false`` when
        ``non_compliant_only`` is set.
        """
        base = scope or (self.resource_group_scope() if self._rg else self.subscription_scope)
        base = base if base.startswith("/") else f"/{base}"
        clauses = (["IsCompliant eq false"] if non_compliant_only else []) + ([filter] if filter else [])
        params: dict[str, Any] = {"$top": top}
        if clauses:
            params["$filter"] = " and ".join(f"({c})" for c in clauses)
        url = f"{base}/providers/Microsoft.PolicyInsights/policyStates/latest/queryResults"
        resp = self._arm("POST", url, self._v.policy_insights, params=params)
        while True:
            body = resp.body or {}
            yield from body.get("value") or []
            next_link = body.get("@odata.nextLink")
            if not next_link:
                return
            resp = self._t.request("POST", self._url(next_link), scope=self._cloud.arm_scope)

    def violations_by_resource(self, scope: str | None = None, *, top: int = 1000) -> list[dict[str, Any]]:
        """Non-compliant resources with how many policy states fail on each, most violations first."""
        found: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "policies": set()})
        for s in self.states(scope, top=top):
            rid = s.get("resourceId") or "(unknown)"
            found[rid]["count"] += 1
            found[rid]["policies"].add(s.get("policyAssignmentName") or s.get("policyDefinitionName") or "(unknown)")
        rows = [{"resource_id": rid, "violations": v["count"], "policies": sorted(v["policies"])}
                for rid, v in found.items()]
        return sorted(rows, key=lambda r: (-r["violations"], r["resource_id"]))
