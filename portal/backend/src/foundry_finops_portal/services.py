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
from foundry_finops.allocation_rules import ALLOCATION_RULES_API_VERSION, cost_allocation_rule, rule_name
from foundry_finops.analysis import IDLE, UNATTRIBUTED, allocate_by_usage, reconcile_usage
from foundry_finops.models import project_budget as pick_project_budget
from foundry_finops.service import cost_control_from_payload

from .access import Access
from .db import Store
from .directory import Directory

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


def _covers_project(control: dict[str, Any], project: str) -> bool:
    """A project's own cost control, or a resource-level one whose project filter names it."""
    return control.get("project") == project or project in ((control.get("filters") or {}).get("project") or [])


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
                "cost_controls": sum(1 for c in controls if _covers_project(c, n)),
                "attention": sum(1 for c in attention if _covers_project(c, n)),
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

    def usage_split(self, days: int, unattributed: str | None, directory: Directory | None,
                    pool: int | None = None) -> dict[str, Any]:
        """Split cost between cost centres by each person's usage and department.

        With no pools defined the pool is the whole resource's cost. With pools, each pool's cost (its agents or
        deployments) is split by the usage inside that pool, and the cost outside every pool is reported, not split.
        ``unattributed`` overrides each pool's own setting. Administrators only: the input is per-person usage, and the
        result has no per-person rows. Usage with no caller identity, and callers whose department has no cost centre,
        are *unattributed*: attribution is optional.
        """
        if not self.access.is_admin:
            raise HTTPException(403, "Portal administrators only")
        if unattributed not in (None, "proportional", "hold"):
            raise HTTPException(400, "unattributed must be 'proportional' or 'hold'")
        try:
            records = list(self.backend.usage_by_caller(days))
        except NotImplementedError as exc:
            raise HTTPException(501, str(exc)) from exc
        aliases = {a["alias"].casefold(): a["agent"] for a in self.store.list_agent_aliases()}
        records = [{**r, "agent": aliases.get(str(r.get("agent") or "").casefold(), str(r.get("agent") or ""))}
                   for r in records]
        # Estimated cost when the records carry it; tokens otherwise (the gateway log has no price). Tokens ignore the
        # price difference between models, so the report says which measure it used.
        measure = "cost" if sum(float(r.get("cost") or 0) for r in records) > 0 else "tokens"
        total = round(sum(self._values_by_basis("project", days, None).values()), 2)
        if not records or total <= 0:
            raise HTTPException(404, "There is no usage in this period to split")

        departments = self._departments({str(r.get("caller_oid") or "") for r in records} - {""}, directory)
        centres = {cc["id"]: cc for cc in self.store.list_cost_centres()}
        by_department = {m["department"].casefold(): centres[m["cost_centre_id"]]["code"]
                         for m in self.store.list_department_map() if m["cost_centre_id"] in centres}
        names = {cc["code"]: cc["name"] for cc in centres.values()}
        names[IDLE] = "Provisioned capacity nobody used"

        def resolve(oid: str) -> str | None:
            return by_department.get(departments.get(oid, "").casefold())

        def split(pool_cost: float, subset: list[dict[str, Any]], mode: str, idle: str = "spread") -> dict[str, Any]:
            unmapped: dict[str, float] = defaultdict(float)
            for r in subset:
                oid = str(r.get("caller_oid") or "")
                if oid and resolve(oid) is None:
                    unmapped[departments.get(oid) or "(no department)"] += float(r.get(measure) or 0)
            try:
                result = allocate_by_usage(pool_cost, subset, resolve, measure=measure, unattributed=mode,
                                           idle=idle if measure == "cost" else "spread")
            except ValueError:   # nothing recorded for this pool: all of it is unattributed, and the report says so
                result = {"rows": [{"key": UNATTRIBUTED, "allocated": pool_cost, "share_percent": 100.0}] if pool_cost else [],
                          "coverage_percent": 0.0, "unattributed_percent": 100.0, "mode": mode, "idle": "spread",
                          "idle_cost": None}
            rows = [{"code": r["key"], "name": names.get(r["key"], "Usage with no cost centre"),
                     "allocated": r["allocated"], "share_percent": r["share_percent"]} for r in result["rows"]]
            return {"total": pool_cost, "mode": result["mode"], "coverage_percent": result["coverage_percent"],
                    "unattributed_percent": result["unattributed_percent"], "rows": rows,
                    "idle": result["idle"], "idle_cost": result["idle_cost"],
                    "unmapped_departments": [{"department": d, "usage": round(c, 2)}
                                             for d, c in sorted(unmapped.items(), key=lambda kv: -kv[1])]}

        weighted = "estimated cost" if measure == "cost" else "tokens (which ignore price differences between models)"
        common = {"days": days, "currency": "USD", "measure": measure, "unattributed_code": UNATTRIBUTED}
        how = (f"An allocation, not billed cost, weighted by recorded {weighted}. Each cost centre gets the share of "
               "recorded usage made by people whose department is mapped to it. Calls with no caller identity, and "
               "people whose department has no cost centre, are unattributed")

        pools = self.store.list_pools()
        unmatched = self._unmatched_agents(records, measure, days) if any(p["match_type"] == "agent" for p in pools) else []
        if pool is not None:
            pools = [p for p in pools if p["id"] == pool]
            if not pools:
                raise HTTPException(404, "No such pool")
        if not pools:
            mode = unattributed or "proportional"
            spread = "spread over the cost centres in proportion to their usage." if mode == "proportional" \
                else "shown on their own row."
            return {**common, **split(total, records, mode), "pools": [], "unmatched_agents": [],
                    "note": f"{how}: {spread} The pool is the whole Foundry resource's cost."}

        out_pools = []
        totals: dict[str, float] = defaultdict(float)
        pooled = 0.0
        for p in pools:
            kind = p["match_type"]
            values = self._values_by_basis(kind, days, None)
            members = {v.casefold() for v in p["values"]}
            cost = round(sum(c for v, c in values.items() if v.casefold() in members), 2)
            subset = [r for r in records if str(r.get(kind) or "").casefold() in members]
            entry = split(cost, subset, unattributed or p["unattributed"], p["idle"])
            entry.update({"id": p["id"], "name": p["name"], "match_type": kind, "values": p["values"],
                          "matched_usage": bool(subset), "idle_setting": p["idle"]})
            if p["idle"] == "hold" and measure != "cost":
                entry["idle_note"] = ("Idle capacity needs estimated cost, and these records only have tokens, so it is "
                                      "spread over the cost centres.")
            out_pools.append(entry)
            pooled += cost
            for row in entry["rows"]:
                totals[row["code"]] += row["allocated"]
        return {
            **common, "pools": out_pools, "unmatched_agents": unmatched,
            "totals": sorted(({"code": c, "name": names.get(c, "Usage with no cost centre"), "allocated": round(v, 2)}
                              for c, v in totals.items()), key=lambda r: -r["allocated"]),
            "pooled_cost": round(pooled, 2), "unpooled_cost": round(max(0.0, total - pooled), 2),
            "note": f"{how}, as each pool's setting says. Pool members are matched to usage records by exactly the same "
                    "name as the billing data, so an agent recorded under another name (a client ID, for example) finds no "
                    "usage; the pool then says so. Cost outside every pool is not split here; the cost centre report's "
                    "percentage rules still apply to it."}

    def _unmatched_agents(self, records: list[dict[str, Any]], measure: str, days: int) -> list[dict[str, Any]]:
        """Agent names in the usage records that no billing agent has. They are why a pool can find no usage.

        Typically a client ID. Map each one to its billing name with an agent alias.
        """
        try:
            billing = {a.casefold() for a in self._values_by_basis("agent", days, None)}
        except HTTPException:
            return []
        usage: dict[str, float] = defaultdict(float)
        for r in records:
            agent = str(r.get("agent") or "")
            if agent and agent.casefold() not in billing:
                usage[agent] += float(r.get(measure) or 0)
        return [{"agent": a, "usage": round(u, 2)} for a, u in sorted(usage.items(), key=lambda kv: -kv[1])[:20]]

    def usage_reconciliation(self, days: int) -> dict[str, Any]:
        """Compare the tokens in the usage records with the tokens Azure Monitor counted, per deployment.

        A gap means calls that nothing recorded, so the split can't see who made them. Administrators only.
        """
        if not self.access.is_admin:
            raise HTTPException(403, "Portal administrators only")
        try:
            records = list(self.backend.usage_by_caller(days))
        except NotImplementedError as exc:
            raise HTTPException(501, str(exc)) from exc
        table = self.backend.token_series("deployment", days * 24)
        monitor: dict[str, float] = defaultdict(float)
        for r in _records(table):
            monitor[str(r.get("deployment") or "(all)")] += float(r.get("TotalTokens") or 0)
        result = reconcile_usage(records, monitor, key="deployment", measure="tokens")
        return {**result, "days": days,
                "note": "Azure Monitor counts tokens per deployment. Usage records are matched to a deployment by name, "
                        "ignoring case. Records made with the model name instead of the deployment name show as not in "
                        "Azure Monitor, and the deployment they belong to shows as under-recorded."}

    def allocation_rules(self, days: int, target_tag: str, source_tag: str, directory: Directory | None) -> dict[str, Any]:
        """Azure cost allocation rule definitions for this period's split, for review. Nothing is sent to Azure.

        One rule per agent pool: the cost of resources tagged ``source_tag`` = each pool member moves onto resources tagged
        ``target_tag`` = each cost centre's code, by the pool's percentages. Rules are ``NotActive``.
        """
        split = self.usage_split(days, None, directory, None)
        rules, skipped = [], []
        for p in split["pools"]:
            if p["match_type"] != "agent":
                skipped.append({"pool": p["name"], "reason": "Azure allocation rules can't use a deployment as a source: "
                                                             "only a tag, a resource group or a subscription."})
                continue
            shares = {r["code"]: r["allocated"] for r in p["rows"] if r["code"] not in (UNATTRIBUTED, IDLE)}
            kept = sum(shares.values())
            if kept <= 0:
                skipped.append({"pool": p["name"], "reason": "No cost centre received any of this pool's cost."})
                continue
            try:
                definition = cost_allocation_rule(
                    source_tag=source_tag, source_values=p["values"], target_tag=target_tag, shares=shares,
                    description=f"{p['name']}: usage split for the last {days} days, built by Foundry FinOps")
            except ValueError as exc:
                skipped.append({"pool": p["name"], "reason": str(exc)})
                continue
            rules.append({"name": rule_name(p["name"]), "pool": p["name"],
                          "excluded_percent": round(100 - kept / p["total"] * 100, 2) if p["total"] else 0.0,
                          "definition": definition})
        return {
            "days": days, "source_tag": source_tag, "target_tag": target_tag, "rules": rules, "skipped": skipped,
            "api": {"method": "PUT", "api_version": ALLOCATION_RULES_API_VERSION,
                    "path": "/providers/Microsoft.Billing/billingAccounts/{billingAccountId}/providers/"
                            "Microsoft.CostManagement/costAllocationRules/{name}"},
            "notes": [
                "For review only: nothing here has been sent to Azure.",
                "Azure splits by the fixed percentages in the rule. It does not follow usage, so rebuild the rule for "
                "each period.",
                "A rule only moves cost onto resources that carry the target tag. A cost centre that exists only in this "
                "portal has no resource to receive it.",
                "Usage with no cost centre and idle capacity are left out, so the percentages cover only the cost that "
                "reached a cost centre (see excluded_percent).",
                "A rule lives on a billing account, and Azure supports these rules only for some agreement types.",
                "Rules are NotActive: saved, but they change no cost until someone activates them.",
            ],
        }

    def _departments(self, oids: set[str], directory: Directory | None) -> dict[str, str]:
        """Each person's department from the latest dated snapshot, asking the directory only for people not yet recorded.

        A person who had no department is asked again on a later day. Earlier days are never rewritten, so a report for
        a past period isn't changed by someone moving department afterwards.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        known = self.store.latest_departments(sorted(oids))
        stale = [o for o in oids if o not in known or (not known[o][1] and known[o][0] < today)]
        fresh: dict[str, str] = {}
        for oid in stale:
            try:
                user = directory.lookup(oid) if directory is not None else None
            except Exception:  # Graph being unavailable must not stop the report; the person stays unattributed
                user = None
            fresh[oid] = user.department if user is not None else ""
        if fresh:
            self.store.snapshot_departments(today, fresh)
        return {**{o: d for o, (_, d) in known.items()}, **fresh}

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
