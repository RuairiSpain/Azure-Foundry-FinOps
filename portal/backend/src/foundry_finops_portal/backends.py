"""The data source behind the portal: the reporting facade of ``foundry_finops`` plus the few reads the fleet needs."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import timedelta
from typing import Any, Protocol

from foundry_finops import CostQuery, FinOpsClient
from foundry_finops.demo import data as demo_data
from foundry_finops.service import DemoService, LiveService, Settings

from .auth import Principal
from .azure_access import ClientFactory
from .config import PortalSettings

logger = logging.getLogger("foundry_finops_portal")


class Backend(Protocol):
    """What the portal's services call. ``LiveService`` and ``DemoService`` already provide most of it."""

    settings: Settings

    def info(self) -> dict[str, Any]: ...
    def overview(self, days: int = 30) -> dict[str, Any]: ...
    def cost_series(self, group_by: str = "project", days: int = 30) -> dict[str, Any]: ...
    def token_series(self, group_by: str = "deployment", hours: int = 168) -> dict[str, Any]: ...
    def list_cost_controls(self, estimate_usage: bool = True) -> list[dict[str, Any]]: ...
    def upsert_cost_control(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def delete_cost_control(self, name: str, project: str | None = None) -> None: ...
    def list_budgets(self) -> list[dict[str, Any]]: ...
    def upsert_budget(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    def delete_budget(self, name: str) -> None: ...
    def list_tags(self) -> list[dict[str, Any]]: ...
    def set_tags(self, scope: str, tags: dict[str, str]) -> dict[str, str]: ...
    def list_deployments(self) -> list[dict[str, Any]]: ...
    def list_quota(self) -> list[dict[str, Any]]: ...
    def list_projects(self) -> list[dict[str, Any]]: ...
    def cost_by_project_agent(self, days: int) -> list[dict[str, Any]]: ...
    def policy_violations(self) -> list[dict[str, Any]]: ...


class DemoBackend(DemoService):
    """Illustrative data. Writes change the in-memory demo store."""

    def list_projects(self) -> list[dict[str, Any]]:
        return [{"name": n, "display_name": n, "team": m["team"]} for n, m in demo_data.PROJECTS.items()]

    def cost_by_project_agent(self, days: int) -> list[dict[str, Any]]:
        agg: dict[tuple[str, str], float] = defaultdict(float)
        for r in self._since(self.store.cost_daily, timedelta(days=days)):
            agg[(r["Project"], r["Agent"])] += r["Cost"]
        return [{"project": p, "agent": a, "cost": round(c, 2)} for (p, a), c in sorted(agg.items())]

    def policy_violations(self) -> list[dict[str, Any]]:
        return [{"resource_id": f"{demo_data.ACCOUNT_ID}/projects/dev-sandbox", "violations": 2,
                 "policies": ["Allowed model deployments", "Require tag: team"]},
                {"resource_id": f"{demo_data.ACCOUNT_ID}/projects/marketing-gen", "violations": 1,
                 "policies": ["Require tag: team"]}]


class LiveBackend(LiveService):
    """Azure data, read with the client it is given (the signed-in person's, or the portal's own)."""

    def list_projects(self) -> list[dict[str, Any]]:
        out = []
        for p in self.fo.projects.list(self._rg, self._acct):
            name = (p.name or "").split("/")[-1]
            props = getattr(p, "properties", None)
            out.append({"name": name, "display_name": getattr(props, "display_name", None) or name, "team": ""})
        return out

    def cost_by_project_agent(self, days: int) -> list[dict[str, Any]]:
        """Cost per project and agent from the ``project`` and ``agent.name`` billing tags (two tag groupings)."""
        q = CostQuery.last_days(days).total().group_by_tag("project").group_by_tag("agent.name")
        table = self.fo.cost.query(q, scope=self._account_id)
        names = [c.name for c in table.columns]
        out = []
        for row in table.rows:
            rec = dict(zip(names, row, strict=False))
            tags = {}
            for key_col in (n for n in names if n.startswith("TagKey")):
                value = rec.get("TagValue" + key_col[len("TagKey"):])
                tags[str(rec[key_col])] = value or ""
            out.append({"project": tags.get("project", "") or "(untagged)", "agent": tags.get("agent.name", ""),
                        "cost": round(float(rec.get("Cost") or rec.get("PreTaxCost") or 0), 4)})
        return out

    def policy_violations(self) -> list[dict[str, Any]]:
        try:
            return self.fo.policy.violations_by_resource(self._account_id)
        except Exception as exc:  # the policy cards fail on their own, like the Foundry portal's
            logger.info("Policy violations unavailable: %s", exc)
            raise


class BackendProvider:
    """Hands out the backend for a request: one shared demo backend, or a live one per person or for the portal."""

    def __init__(self, settings: PortalSettings, clients: ClientFactory | None = None) -> None:
        self.settings = settings
        self._clients = clients
        self._demo = DemoBackend(self._finops_settings()) if settings.demo else None

    def _finops_settings(self) -> Settings:
        s = self.settings
        return Settings(subscription_id=s.subscription_id, resource_group=s.resource_group,
                        foundry_account=s.foundry_account, apim_service=s.apim_service, location=s.location,
                        team_tag=s.team_tag, demo=s.demo, read_only=s.read_only)

    def for_user(self, principal: Principal) -> Any:
        if self._demo is not None:
            return self._demo
        assert self._clients is not None
        return LiveBackend(self._finops_settings(), self._clients.for_user(principal))

    def for_service(self) -> Any:
        """The portal's own identity, used for writes that the portal has already checked the person may make."""
        if self._demo is not None:
            return self._demo
        assert self._clients is not None
        return LiveBackend(self._finops_settings(), self._clients.service())

    @property
    def client(self) -> FinOpsClient | None:
        return self._clients.service() if self._clients is not None else None
