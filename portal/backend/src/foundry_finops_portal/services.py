"""The portal's views: what the Foundry portal's Fleet, Operations and Cost pages show, plus cost centres.

Every function takes the person's :class:`~foundry_finops_portal.access.Access` and a backend. The backend is the
person's own Azure client in live mode, so Azure RBAC has already limited what it returns; the portal's grants then
narrow it further. Nothing here invents a number: a figure Azure can't give is ``None`` with a ``note``.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from foundry_finops.models import project_budget as pick_project_budget
from foundry_finops.service import cost_control_from_payload

from .access import Access
from .db import Store

NEEDS_ACCOUNT = ("This view is for the whole Foundry resource, and your portal access is limited to projects. "
                 "Ask a portal administrator for access to the resource.")


def need_account(access: Access) -> None:
    if not access.account_level:
        raise HTTPException(403, NEEDS_ACCOUNT)


# ------------------------------------------------------------------ helpers
def _records(table: dict[str, Any]) -> list[dict[str, Any]]:
    names = [c["name"] for c in table["columns"]]
    return [dict(zip(names, r, strict=False)) for r in table["rows"]]


def _pivot(records: list[dict[str, Any]], group: str, value: str = "Cost", date_key: str = "UsageDate") -> dict[str, Any]:
    labels = sorted({str(r[date_key])[:10] for r in records})
    index = {d: i for i, d in enumerate(labels)}
    series: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(labels))
    for r in records:
        series[str(r.get(group) or "(untagged)")][index[str(r[date_key])[:10]]] += float(r.get(value) or 0)
    items: list[dict[str, Any]] = [{"name": n, "values": [round(v, 2) for v in vals], "total": round(sum(vals), 2)}
             for n, vals in series.items()]
    items.sort(key=lambda s: -s["total"])
    return {"labels": labels, "series": items, "total": round(sum(i["total"] for i in items), 2)}


def _split_periods(records: list[dict[str, Any]], days: int, today: date | None = None
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Rows of the last ``days`` days, and of the ``days`` before them (for the change from the previous period)."""
    today = today or datetime.now(timezone.utc).date()
    cut = (today - timedelta(days=days - 1)).isoformat()
    current = [r for r in records if str(r["UsageDate"])[:10] >= cut]
    previous = [r for r in records if str(r["UsageDate"])[:10] < cut]
    return current, previous


def _trend(current: float, previous: float) -> float | None:
    """Percent change, or ``None`` when there was no cost before (the portal shows that as *New*)."""
    return round((current - previous) / previous * 100, 1) if previous > 0 else None


def _sum_by(records: list[dict[str, Any]], key: str) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for r in records:
        out[str(r.get(key) or "(untagged)")] += float(r.get("Cost") or 0)
    return out


class Views:
    """Builds the pages' data. One instance per request keeps the person's access and backend together."""

    def __init__(self, store: Store, access: Access, backend: Any) -> None:
        self.store, self.access, self.backend = store, access, backend

    # ---------------------------------------------------------------- scoping
    def _projects(self) -> list[str]:
        return [p["name"] for p in self.backend.list_projects()]

    def visible_projects(self) -> set[str] | None:
        return self.access.visible_projects(self._projects())

    def _keep_project(self, name: str, visible: set[str] | None) -> bool:
        return visible is None or name in visible

    def _project_cost_records(self, days: int, visible: set[str] | None) -> list[dict[str, Any]]:
        recs = _records(self.backend.cost_series("project", days))
        return [r for r in recs if self._keep_project(str(r.get("project")), visible)]

    def _pairs(self, days: int, visible: set[str] | None) -> list[dict[str, Any]]:
        return [p for p in self.backend.cost_by_project_agent(days) if self._keep_project(p["project"], visible)]

    def _cost_controls(self, visible: set[str] | None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = list(self.backend.list_cost_controls())
        if visible is None:
            return rows
        out = []
        for r in rows:
            owner = r.get("project")
            filt = (r.get("filters") or {}).get("project") or []
            if owner:
                if owner in visible:
                    out.append(r)
            elif filt and set(filt) <= visible:  # a resource-level control that only covers visible projects
                out.append(r)
        return out

    # ------------------------------------------------------------------- Fleet
    def fleet(self, days: int = 30) -> dict[str, Any]:
        visible = self.visible_projects()
        projects = [p for p in self.backend.list_projects() if self._keep_project(p["name"], visible)]
        recs = self._project_cost_records(days * 2, visible)
        current, previous = _split_periods(recs, days)
        cur, prev = _sum_by(current, "project"), _sum_by(previous, "project")
        pairs = self._pairs(days, visible)
        agents: dict[str, set[str]] = defaultdict(set)
        for p in pairs:
            if p["agent"]:
                agents[p["project"]].add(p["agent"])
        controls = self._cost_controls(visible)
        attention = [c for c in controls if c.get("state") in ("Near threshold", "Limit reached")]
        rows: list[dict[str, Any]] = []
        for p in projects:
            n = p["name"]
            rows.append({
                "name": n, "display_name": p.get("display_name") or n, "team": p.get("team", ""),
                "cost": round(cur.get(n, 0.0), 2), "previous_cost": round(prev.get(n, 0.0), 2),
                "trend_pct": _trend(cur.get(n, 0.0), prev.get(n, 0.0)), "new": prev.get(n, 0.0) == 0 and cur.get(n, 0.0) > 0,
                "agents": len(agents.get(n, ())),
                "cost_controls": sum(1 for c in controls if c.get("project") == n),
                "attention": sum(1 for c in attention if c.get("project") == n),
            })
        rows.sort(key=lambda r: -r["cost"])
        increases = sorted((r for r in rows if r["cost"] - r["previous_cost"] > 0),
                           key=lambda r: -(r["cost"] - r["previous_cost"]))[:5]
        violations: list[dict[str, Any]] | None
        note = None
        try:
            violations = self.backend.policy_violations()
            if visible is not None:
                violations = [v for v in violations if any(f"/projects/{p}" in v["resource_id"] for p in visible)]
        except Exception:
            violations, note = None, "Policy violations unavailable: you may lack Microsoft.PolicyInsights/*/read."
        deployments = self.backend.list_deployments() if self.access.account_level else None
        return {
            "days": days,
            "kpis": {
                "projects": len(rows), "agents": len({a for s in agents.values() for a in s}),
                "deployments": len(deployments) if deployments is not None else None,
                "cost": round(sum(r["cost"] for r in rows), 2),
                "previous_cost": round(sum(r["previous_cost"] for r in rows), 2),
                "trend_pct": _trend(sum(r["cost"] for r in rows), sum(r["previous_cost"] for r in rows)),
                "cost_controls_attention": len(attention),
                "policy_violations": sum(v["violations"] for v in violations) if violations is not None else None,
            },
            "projects": rows,
            "top_cost_increases": [{"name": r["name"], "increase": round(r["cost"] - r["previous_cost"], 2),
                                    "trend_pct": r["trend_pct"]} for r in increases],
            "attention": [{"name": c["name"], "project": c.get("project"), "state": c["state"],
                           "percent_used": c.get("percent_used")} for c in attention],
            "policy": violations, "policy_note": note,
            "currency": "USD",
            "notes": ["Cost comes from Azure Cost Management 8 to 24 hours after usage.",
                      "Agent counts come from the agent.name billing tag (November 2026 onwards)."],
        }

    # -------------------------------------------------------------- Operations
    def operations(self, hours: int = 168) -> dict[str, Any]:
        need_account(self.access)
        by_dep = _records(self.backend.token_series("deployment", hours))
        labels = sorted({r["TimeGenerated"] for r in by_dep})
        totals: dict[str, dict[str, float]] = defaultdict(lambda: {"tokens": 0, "requests": 0})
        series: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(labels))
        idx = {t: i for i, t in enumerate(labels)}
        for r in by_dep:
            d = str(r["deployment"])
            totals[d]["tokens"] += r["TotalTokens"]
            totals[d]["requests"] += r["Requests"]
            series[d][idx[r["TimeGenerated"]]] += r["TotalTokens"]
        deployments = {d["name"]: d for d in self.backend.list_deployments()}
        rows = [{"deployment": d, "tokens": int(v["tokens"]), "requests": int(v["requests"]),
                 "tokens_per_request": round(v["tokens"] / v["requests"]) if v["requests"] else None,
                 "tokens_per_minute_limit": deployments.get(d, {}).get("tokens_per_minute"),
                 "model": deployments.get(d, {}).get("model_name")} for d, v in totals.items()]
        rows.sort(key=lambda r: -r["tokens"])
        return {
            "hours": hours, "labels": labels,
            "series": [{"name": n, "values": [int(x) for x in vals], "total": int(sum(vals))}
                       for n, vals in sorted(series.items(), key=lambda kv: -sum(kv[1]))],
            "kpis": {"tokens": int(sum(r["tokens"] for r in rows)), "requests": int(sum(r["requests"] for r in rows)),
                     "deployments": len(deployments)},
            "deployments": rows, "quota": self.backend.list_quota(),
            "notes": ["Usage appears within minutes. Azure Monitor splits tokens by deployment and model, not by "
                      "project or agent; cost by project and agent is on the Cost page.",
                      "Error and latency views need metrics this release does not read yet."],
        }

    # -------------------------------------------------------------------- Cost
    def cost_overview(self, days: int = 30) -> dict[str, Any]:
        visible = self.visible_projects()
        recs = self._project_cost_records(days * 2, visible)
        current, previous = _split_periods(recs, days)
        cur, prev = sum(float(r["Cost"]) for r in current), sum(float(r["Cost"]) for r in previous)
        pairs = self._pairs(days, visible)
        agent_cost = sum(p["cost"] for p in pairs if p["agent"])
        direct = sum(p["cost"] for p in pairs if not p["agent"])
        forecast: float | None = None
        note: str | None = "The month-end forecast is for the whole Foundry resource; your access is limited to projects."
        if self.access.account_level:
            kpis = self.backend.overview(days).get("kpis", {})
            forecast = kpis.get("forecast_month_end")
            note = None if forecast is not None else "Cost Management returned no forecast yet (not enough billing history)."
        controls = self._cost_controls(visible)
        top = sorted(_sum_by(current, "project").items(), key=lambda kv: -kv[1])[:5]
        return {
            "days": days, "currency": "USD",
            "estimated_cost": {"value": round(cur, 2), "previous": round(prev, 2), "trend_pct": _trend(cur, prev)},
            "forecast": {"value": forecast, "note": note},
            "spend_breakdown": {"agent_cost": round(agent_cost, 2), "direct_model_cost": round(direct, 2)},
            "cost_controls": {"total": len(controls),
                              "attention": sum(1 for c in controls if c.get("state") in ("Near threshold", "Limit reached"))},
            "highest_spend": [{"name": n, "cost": round(c, 2)} for n, c in top],
            "series": _pivot(current, "project"),
            "notes": ["Estimated cost is cost from Azure Cost Management at the prices you pay, before credits and taxes. "
                      "It appears 8 to 24 hours after usage and can change until the invoice is final.",
                      "Agent cost is the model cost of agent requests; direct model cost is the rest. Agent names are "
                      "in billing data from November 2026."],
        }

    def cost_series(self, group_by: str, days: int) -> dict[str, Any]:
        visible = self.visible_projects()
        if group_by == "cost_centre":
            return self._centre_series(days, visible)
        if group_by == "project":
            return _pivot(self._project_cost_records(days, visible), "project")
        need_account(self.access)
        return _pivot(_records(self.backend.cost_series(group_by, days)), group_by)

    def cost_table(self, group_by: str, days: int) -> dict[str, Any]:
        visible = self.visible_projects()
        if group_by == "cost_centre":
            series = self._centre_series(days, visible)
        elif group_by == "agent" and visible is not None:
            agg: dict[str, float] = defaultdict(float)
            for p in self._pairs(days, visible):
                agg[p["agent"] or "(direct model cost)"] += p["cost"]
            series = {"series": [{"name": n, "total": round(v, 2)} for n, v in agg.items()],
                      "total": round(sum(agg.values()), 2)}
        else:
            if group_by == "project":
                recs = self._project_cost_records(days * 2, visible)
            else:
                need_account(self.access)
                recs = _records(self.backend.cost_series(group_by, days * 2))
            current, previous = _split_periods(recs, days)
            cur, prev = _sum_by(current, group_by), _sum_by(previous, group_by)
            total = sum(cur.values())
            rows: list[dict[str, Any]] = [
                {"name": n, "cost": round(c, 2), "share_pct": round(c / total * 100, 1) if total else None,
                 "previous_cost": round(prev.get(n, 0.0), 2), "trend_pct": _trend(c, prev.get(n, 0.0)),
                 "new": prev.get(n, 0.0) == 0 and c > 0}
                for n, c in cur.items()]
            rows.sort(key=lambda r: -r["cost"])
            return {"group_by": group_by, "days": days, "rows": rows, "total": round(total, 2), "currency": "USD"}
        total = series["total"]
        rows = [{"name": s["name"], "cost": s["total"], "share_pct": round(s["total"] / total * 100, 1) if total else None,
                 "previous_cost": None, "trend_pct": None, "new": False} for s in series["series"]]
        return {"group_by": group_by, "days": days, "rows": rows, "total": total, "currency": "USD"}

    # ------------------------------------------------------------ cost controls
    def cost_controls(self, project: str | None = None) -> dict[str, Any]:
        visible = self.visible_projects()
        rows = self._cost_controls(visible)
        if project:
            rows = [r for r in rows if r.get("project") in (None, project)]
        for r in rows:
            r["can_write"] = self.access.can_write(self._ref(r.get("project")))
        card = None
        if project:
            specs = [cost_control_from_payload(r) for r in rows]
            pb = pick_project_budget(specs, project)
            card = {"name": pb.budget.name if pb.budget else None, "amount": pb.budget.amount if pb.budget else None,
                    "count": pb.count, "per_agent_or_session": pb.per_agent_or_session}
        return {"cost_controls": rows, "project_budget": card,
                "note": "Usage is an estimate: Foundry has no usage API for cost controls, so it is rebuilt from billed cost "
                        "or metrics (see usage_note on each row)."}

    def _ref(self, project: str | None) -> Any:
        from .access import ScopeRef

        return ScopeRef(project=project, **self._home())

    def _home(self) -> dict[str, str]:
        h = self.access.home
        return {"subscription": h.subscription, "resource_group": h.resource_group, "account": h.account}

    # ------------------------------------------------------------ project monitor
    def project_monitor(self, project: str, days: int = 30) -> dict[str, Any]:
        visible = self.visible_projects()
        if not self._keep_project(project, visible) or project not in self._projects():
            raise HTTPException(404, f"No project {project!r} that you can see")
        recs = [r for r in self._project_cost_records(days * 2, visible) if r.get("project") == project]
        current, previous = _split_periods(recs, days)
        cur, prev = sum(float(r["Cost"]) for r in current), sum(float(r["Cost"]) for r in previous)
        pairs = [p for p in self._pairs(days, visible) if p["project"] == project]
        agents = sorted(({"agent": p["agent"] or "(direct model cost)", "cost": p["cost"]} for p in pairs),
                        key=lambda a: -a["cost"])
        return {"project": project, "days": days, "currency": "USD",
                "latest_billed_cost": {"value": round(cur, 2), "previous": round(prev, 2), "trend_pct": _trend(cur, prev)},
                "agents": agents, "series": _pivot(current, "project"),
                "controls": self.cost_controls(project),
                "notes": ["Token and run counts per project come from Azure Monitor, which does not split by project; "
                          "see the Operations page for the whole Foundry resource."]}

    # ---------------------------------------------------------------- cost centres
    def _rules_for(self, basis: str) -> tuple[str, str]:
        kind, _, key = basis.partition(":")
        if kind not in ("project", "agent", "deployment", "tag"):
            raise HTTPException(400, "basis must be project, agent, deployment or tag:<name>")
        if kind == "tag" and not key:
            raise HTTPException(400, "basis tag needs a name, for example tag:team")
        return kind, key

    def _values_by_basis(self, basis: str, days: int, visible: set[str] | None) -> dict[str, float]:
        kind, key = self._rules_for(basis)
        if kind == "project":
            return dict(_sum_by(self._project_cost_records(days, visible), "project"))
        if visible is not None:
            if kind == "agent":
                agg: dict[str, float] = defaultdict(float)
                for p in self._pairs(days, visible):
                    if p["agent"]:
                        agg[p["agent"]] += p["cost"]
                return dict(agg)
            raise HTTPException(403, NEEDS_ACCOUNT)
        recs = _records(self.backend.cost_series(key if kind == "tag" else kind, days))
        return dict(_sum_by(recs, key if kind == "tag" else kind))

    def cost_centre_report(self, basis: str = "project", days: int = 30) -> dict[str, Any]:
        kind, key = self._rules_for(basis)
        visible = self.visible_projects()
        values = self._values_by_basis(basis, days, visible)
        centres = self.store.list_cost_centres()
        claimed: dict[str, float] = defaultdict(float)
        out = []
        for cc in centres:
            rows = []
            for rule in cc["rules"]:
                if rule["match_type"] != kind or (kind == "tag" and rule["match_key"] != key):
                    continue
                cost = values.get(rule["match_value"])
                if cost is None:
                    continue
                share = rule["share_percent"]
                claimed[rule["match_value"]] += share
                rows.append({"value": rule["match_value"], "share_pct": share, "cost": round(cost * share / 100, 2)})
            if not rows and visible is not None:
                continue
            total = round(sum(r["cost"] for r in rows), 2)
            budget = cc["monthly_budget"]
            out.append({"id": cc["id"], "code": cc["code"], "name": cc["name"], "cost": total, "values": rows,
                        "monthly_budget": budget,
                        "percent_of_budget": round(total / budget * 100, 1) if budget else None})
        unallocated = [{"value": v, "cost": round(c * max(0.0, 100 - claimed.get(v, 0.0)) / 100, 2),
                        "unallocated_pct": round(max(0.0, 100 - claimed.get(v, 0.0)), 1)}
                       for v, c in values.items() if claimed.get(v, 0.0) < 100]
        out.sort(key=lambda r: -r["cost"])
        return {"basis": basis, "days": days, "currency": "USD", "centres": out,
                "unallocated": sorted(unallocated, key=lambda r: -r["cost"]),
                "total": round(sum(values.values()), 2),
                "note": "A rule gives a cost centre a share of one value of one dimension (a project, agent, deployment "
                        "or tag value). Only rules of the chosen basis count, so no cost is counted twice. A cost "
                        "centre is a reporting label in this portal; it is not written to Azure."}

    def _centre_series(self, days: int, visible: set[str] | None) -> dict[str, Any]:
        recs = self._project_cost_records(days, visible)
        rules: dict[str, list[tuple[str, float]]] = defaultdict(list)
        names = {c["id"]: c["name"] for c in self.store.list_cost_centres()}
        for cc in self.store.list_cost_centres():
            for r in cc["rules"]:
                if r["match_type"] == "project":
                    rules[r["match_value"]].append((names[cc["id"]], r["share_percent"]))
        shaped = []
        for r in recs:
            claimed = 0.0
            for centre, share in rules.get(str(r["project"]), []):
                shaped.append({"UsageDate": r["UsageDate"], "cost_centre": centre, "Cost": float(r["Cost"]) * share / 100})
                claimed += share
            if claimed < 100:
                shaped.append({"UsageDate": r["UsageDate"], "cost_centre": "(unallocated)",
                               "Cost": float(r["Cost"]) * (100 - claimed) / 100})
        return _pivot(shaped, "cost_centre")

    # ------------------------------------------------------------------- budgets
    def budgets(self) -> list[dict[str, Any]]:
        need_account(self.access)
        return list(self.backend.list_budgets())

    def tags(self) -> list[dict[str, Any]]:
        need_account(self.access)
        return list(self.backend.list_tags())

    def alert_events(self) -> list[dict[str, Any]]:
        visible = self.visible_projects()
        rows = self.store.list_alert_events(200)
        return [r for r in rows if visible is None or r["project"] in visible]
