"""Bulk download of every read query the SDK offers for one Foundry resource or project.

Used by ``foundry-finops download``. Each query writes ``FinOps/<yyyy-mm-dd>/<query-name>.json`` where the
date is the day the download ran and the file name is the query type. A ``_manifest.json`` lists what ran,
what was skipped and what failed, so a partial download is never mistaken for a complete one.

Cost Management data is requested with daily granularity and split into windows of at most
:data:`COST_WINDOW_DAYS` days (the API rejects longer ranges), so any start and end date works.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import queries
from .analysis import anomalies_from_table, ptu_headroom
from .client import FinOpsClient
from .errors import AuthenticationError, FinOpsError
from .models import CostQuery, Table, _jsonable, metric_series_to_table
from .operations.monitor import FoundryMetrics, project_matches
from .service import LiveService

#: Cost Management rejects queries that span more than a year.
COST_WINDOW_DAYS = 360
#: Azure Monitor keeps platform metrics for 93 days.
METRICS_RETENTION_DAYS = 93
FOLDER_NAME = "FinOps"


class QuerySkipped(Exception):
    """A query that can't run for this request (missing setting, date range) - not a failure."""


@dataclass
class ExportContext:
    client: FinOpsClient
    service: LiveService
    account: str
    start: date
    end: date
    project: str | None = None
    location: str = "eastus2"
    logs_target: str = ""
    apim_service: str = ""
    team_tag: str = "team"
    #: A discount you know, in percent, applied to the retail price sheet file (never read from Azure).
    discount_percent: float = 0.0
    today: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    #: Notes added by queries (for example "metrics clamped to 93 days"); saved in the manifest.
    notes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def account_id(self) -> str:
        return self.client.foundry.account_id(self.account)

    @property
    def project_id(self) -> str | None:
        return self.client.foundry.account_id(self.account) + f"/projects/{self.project}" if self.project else None

    @property
    def start_dt(self) -> datetime:
        return datetime(self.start.year, self.start.month, self.start.day, tzinfo=timezone.utc)

    @property
    def end_dt(self) -> datetime:
        """End of the last requested day."""
        return datetime(self.end.year, self.end.month, self.end.day, 23, 59, 59, tzinfo=timezone.utc)

    def note(self, query: str, text: str) -> None:
        self.notes.setdefault(query, []).append(text)


@dataclass(frozen=True)
class Query:
    name: str  #: also the file name (without ``.json``)
    description: str
    run: Callable[[ExportContext], Any]
    #: Settings the query needs, from :class:`ExportContext` (``logs_target``, ``apim_service``, ...).
    needs: tuple[str, ...] = ()


# ------------------------------------------------------------------ helpers
def _records(table: Table) -> list[dict[str, Any]]:
    return table.to_records()


def _windows(start: date, end: date, days: int = COST_WINDOW_DAYS) -> list[tuple[date, date]]:
    out = []
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        out.append((cur, stop))
        cur = stop + timedelta(days=1)
    return out


def _cost_table(ctx: ExportContext, build: Callable[[CostQuery], CostQuery], *, forecast: bool = False) -> Table:
    """Run a Cost Management query over the whole range, one API window at a time."""
    rows: list[list[Any]] = []
    columns = None
    first, last = (ctx.today, ctx.end) if forecast else (ctx.start, ctx.end)
    for lo, hi in _windows(first, last):
        q = build(CostQuery.between(
            datetime(lo.year, lo.month, lo.day, tzinfo=timezone.utc),
            datetime(hi.year, hi.month, hi.day, 23, 59, 59, tzinfo=timezone.utc),
        ).daily())
        if ctx.project:
            q.where_tag("project", [ctx.project])
        table = (ctx.client.cost.forecast if forecast else ctx.client.cost.query)(q, scope=ctx.account_id)
        columns = columns or table.columns
        rows.extend(table.rows)
    return Table(columns or [], rows, "Cost")


def _group(kind: str, name: str) -> Callable[[ExportContext], Any]:
    def run(ctx: ExportContext) -> Any:
        tag = {"project": "project", "agent": "agent.name", "deployment": "deployment", "team": ctx.team_tag}
        return _records(_cost_table(ctx, lambda q: q.group_by_tag(tag[name]) if kind == "tag" else q.group_by(name)))

    return run


def _cost_total(ctx: ExportContext) -> Any:
    return _records(_cost_table(ctx, lambda q: q))


def _cost_forecast(ctx: ExportContext) -> Any:
    if ctx.end <= ctx.today:
        raise QuerySkipped("the end date is not in the future, so there is nothing to forecast")
    return _records(_cost_table(ctx, lambda q: q, forecast=True))


def _metrics(split: str, name: str) -> Callable[[ExportContext], Any]:
    def run(ctx: ExportContext) -> Any:
        floor = ctx.today - timedelta(days=METRICS_RETENTION_DAYS)
        start = max(ctx.start, floor)
        if start > ctx.end:
            raise QuerySkipped(f"Azure Monitor only keeps {METRICS_RETENTION_DAYS} days of metrics; the range is older")
        if start != ctx.start:
            ctx.note(name, f"Start clamped to {start} because Azure Monitor keeps {METRICS_RETENTION_DAYS} days.")
        if ctx.project:
            ctx.note(name, "Metrics exist per Foundry resource and deployment, not per project: account-wide values.")
        days = (ctx.end - start).days + 1
        series = ctx.client.metrics.query(
            ctx.account_id,
            ["ProcessedPromptTokens", "GeneratedTokens", "TokenTransaction", "AzureOpenAIRequests"],
            start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            end=min(ctx.end_dt, datetime.now(timezone.utc)),
            interval="PT1H" if days <= 2 else "P1D",
            split_by=(split,),
        )
        return _records(metric_series_to_table(series))

    return run


def _estimated_cost(by: tuple[str, ...], name: str) -> Callable[[ExportContext], Any]:
    def run(ctx: ExportContext) -> Any:
        floor = ctx.today - timedelta(days=METRICS_RETENTION_DAYS)
        start = max(ctx.start, floor)
        if start > ctx.end:
            raise QuerySkipped(f"Azure Monitor only keeps {METRICS_RETENTION_DAYS} days of metrics; the range is older")
        if start != ctx.start:
            ctx.note(name, f"Start clamped to {start} because Azure Monitor keeps {METRICS_RETENTION_DAYS} days.")
        ctx.note(name, "FoundryModelEstimatedCost is a Preview metric: an estimate in USD for Foundry models, not billed "
                       "cost. Cost Management remains the system of record.")
        days = (ctx.end - start).days + 1
        kwargs: dict[str, Any] = {
            "start": datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
            "end": min(ctx.end_dt, datetime.now(timezone.utc)),
            "by": by, "interval": "PT1H" if days <= 2 else "P1D",
        }
        rows = ctx.client.metrics.estimated_cost(ctx.account_id, **kwargs)
        if ctx.project and FoundryMetrics.PROJECT_DIMENSION in by:
            seen = {str(r.get(FoundryMetrics.PROJECT_DIMENSION.lower()) or "") for r in rows}
            keep = {v for v in seen if project_matches(v, ctx.project)}
            if not keep:
                ctx.note(name, f"No ProjectId value matched project {ctx.project!r}; all projects are included. "
                               f"Values seen: {sorted(seen)}")
            else:
                rows = [r for r in rows if str(r.get(FoundryMetrics.PROJECT_DIMENSION.lower()) or "") in keep]
        return rows

    return run


def _cost_anomalies(ctx: ExportContext) -> Any:
    tag = "agent.name" if ctx.project else "project"  # one project: compare its agents; otherwise compare projects
    table = _cost_table(ctx, lambda q: q.group_by_tag(tag))
    min_history = 7
    days = (ctx.end - ctx.start).days + 1
    ctx.note("cost_anomalies", f"Grouped by tag {tag!r}. Method: modified z-score above 3.5 against the median of the "
                               f"previous 28 days; a day needs {min_history} earlier days. Days without a charge count as "
                               "zero. Statistical outliers in your own data, not Azure's anomaly alerts.")
    if days <= min_history:
        ctx.note("cost_anomalies", f"The range is {days} day(s); at least {min_history + 1} are needed to judge a day.")
        return []
    return [asdict(a) for a in anomalies_from_table(table, group_key="TagValue")]


def _budget_status(ctx: ExportContext) -> Any:
    return [{**b.to_json(), "percent_used": b.percent_used(), "percent_forecast": b.percent_forecast(),
             "notification_status": b.notification_status()} for b in ctx.client.cost.list_budgets()]


def _effective_prices(ctx: ExportContext) -> Any:
    from .operations.pricing import effective_discounts

    start = datetime(ctx.start.year, ctx.start.month, ctx.start.day, tzinfo=timezone.utc)
    if (ctx.end - ctx.start).days > COST_WINDOW_DAYS:
        raise QuerySkipped(f"effective prices use one Cost Management query, which spans at most {COST_WINDOW_DAYS} days")
    effective = ctx.client.cost.effective_prices(scope=ctx.account_id, start=start, end=ctx.end_dt)
    billed = effective.to_price_sheet()
    out: dict[str, Any] = {"meters": effective.to_records(), "skipped_meters": effective.skipped,
                           "price_sheet_per_1m_tokens": billed.to_records()}
    try:
        out["discount_vs_retail"] = effective_discounts(billed, ctx.client.pricing.openai_price_sheet(ctx.location))
    except (FinOpsError, ValueError) as exc:
        ctx.note("effective_prices", f"Discount against retail not computed: {exc}")
    ctx.note("effective_prices", "Derived from billed cost divided by billed quantity: it includes your discounts. A meter "
                                 "with no billed usage in the range has no price.")
    return out


def _ptu_headroom(ctx: ExportContext) -> Any:
    end = min(ctx.end, ctx.today)
    hours = max(1, min((end - ctx.start).days + 1, METRICS_RETENTION_DAYS) * 24)
    ctx.note("ptu_headroom", f"Utilisation covers the last {hours} hours up to now (the metric has no history beyond "
                             f"{METRICS_RETENTION_DAYS} days).")
    return ptu_headroom(ctx.client, ctx.account, hours=hours, location=ctx.location)


def _need_rg(ctx: ExportContext) -> None:
    if not ctx.client.resource_group:
        raise QuerySkipped("needs --resource-group")


def _cost_controls(ctx: ExportContext) -> Any:
    rows = ctx.service.list_cost_controls()
    if ctx.project:  # the project's own controls plus resource-level controls that apply to every project
        rows = [r for r in rows if r.get("project") in (None, ctx.project)]
    return rows


def _budgets(ctx: ExportContext) -> Any:
    return [b.to_json() for b in ctx.client.cost.list_budgets()]


def _foundry_account(ctx: ExportContext) -> Any:
    return ctx.client.foundry.get_account(ctx.account).to_json()


def _foundry_projects(ctx: ExportContext) -> Any:
    if ctx.project:
        return [ctx.client.foundry.get_project(ctx.account, ctx.project).to_json()]
    return [p.to_json() for p in ctx.client.foundry.list_projects(ctx.account)]


def _tags(ctx: ExportContext) -> Any:
    out: dict[str, Any] = {"inventory": ctx.service.list_tags(), "account": ctx.client.tags.get(ctx.account_id)}
    if ctx.project_id:
        out["project"] = ctx.client.tags.get(ctx.project_id)
    return out


def _alerts(ctx: ExportContext) -> Any:
    _need_rg(ctx)
    return ctx.service.list_alerts()


def _quota_models(ctx: ExportContext) -> Any:
    return list(ctx.client.quota.models(ctx.location))


def _price_sheet(ctx: ExportContext) -> Any:
    sheet = ctx.client.pricing.openai_price_sheet(ctx.location)
    out: dict[str, Any] = {"currency": sheet.currency, "source": "Azure Retail Prices API",
                           "prices_per_1m_tokens": {k: asdict(v) for k, v in sheet.prices.items()}}
    if ctx.discount_percent:
        out["discount_percent_supplied_by_you"] = ctx.discount_percent
        out["discounted_prices_per_1m_tokens"] = {
            k: asdict(v) for k, v in sheet.scaled(ctx.discount_percent).prices.items()}
    return out


def _apim(ctx: ExportContext) -> Any:
    return ctx.service.apim_overview()


def _kql(title: str, text: str) -> Callable[[ExportContext], Any]:
    def run(ctx: ExportContext) -> Any:
        table = ctx.client.logs.query(
            ctx.logs_target, text, timespan=f"{ctx.start.isoformat()}T00:00:00Z/{(ctx.end + timedelta(days=1)).isoformat()}T00:00:00Z"
        )
        if ctx.project:
            ctx.note(f"kql_{_slug(title)}", "Saved KQL isn't filtered by project; edit the query to narrow it.")
        return _records(table)

    return run


def _short(message: str, limit: int = 400) -> str:
    first = message.strip().splitlines()[0] if message.strip() else message
    return first if len(first) <= limit else first[: limit - 1] + "…"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


# ----------------------------------------------------------------- registry
QUERIES: list[Query] = [
    # --- Cost Management (billed cost, daily rows)
    Query("cost_daily_total", "Billed cost per day", _cost_total),
    Query("cost_by_project", "Billed cost per day by Foundry project (billing tag `project`)", _group("tag", "project")),
    Query("cost_by_agent", "Billed cost per day by agent (billing tag `agent.name`)", _group("tag", "agent")),
    Query("cost_by_deployment", "Billed cost per day by model deployment (billing tag `deployment`)",
          _group("tag", "deployment")),
    Query("cost_by_team", "Billed cost per day by your team tag", _group("tag", "team")),
    Query("cost_by_model", "Billed cost per day by model (meter sub-category)", _group("dim", "MeterSubCategory")),
    Query("cost_by_meter", "Billed cost per day by meter", _group("dim", "Meter")),
    Query("cost_by_pricing_model", "Billed cost per day by pricing model (on-demand, reservation, savings plan)",
          _group("dim", "PricingModel")),
    Query("cost_by_charge_type", "Billed cost per day by charge type (usage, purchase, refund)",
          _group("dim", "ChargeType")),
    Query("cost_anomalies", "Days whose billed cost jumps above the group's own history", _cost_anomalies),
    Query("effective_prices", "Effective prices per meter and per 1M tokens from your billed cost and quantity",
          _effective_prices),
    Query("cost_forecast", "Cost forecast from today to the end date", _cost_forecast),
    Query("cost_dimensions", "Cost Management dimensions available at this scope",
          lambda c: c.client.cost.dimensions(scope=c.account_id)),
    Query("cost_exports", "Scheduled Cost Management exports", lambda c: c.client.cost.list_exports()),
    Query("budgets", "Cost Management budgets with current and forecast spend", _budgets),
    Query("budget_status", "Which budget notification thresholds the reported spend has reached", _budget_status),
    # --- Foundry
    Query("cost_controls", "Foundry cost controls with estimated usage", _cost_controls),
    Query("foundry_account", "The Foundry resource", _foundry_account),
    Query("foundry_projects", "Foundry projects", _foundry_projects),
    Query("foundry_deployments", "Model deployments and their capacity (TPM)",
          lambda c: [d.to_json() for d in c.client.foundry.list_deployments(c.account)]),
    Query("foundry_connections", "Foundry resource connections", lambda c: list(c.client.foundry.list_connections(c.account))),
    Query("foundry_event_grid_topics", "Event Grid topics usable as cost-control alert destinations",
          lambda c: list(c.client.foundry.list_event_grid_topics())),
    # --- Azure Monitor
    Query("token_usage_by_deployment", "Tokens and requests by deployment (Azure Monitor)",
          _metrics("ModelDeploymentName", "token_usage_by_deployment")),
    Query("token_usage_by_model", "Tokens and requests by model (Azure Monitor)", _metrics("ModelName", "token_usage_by_model")),
    Query("estimated_cost_by_project", "Near-real-time ESTIMATED cost per project (Preview metric)",
          _estimated_cost((FoundryMetrics.PROJECT_DIMENSION,), "estimated_cost_by_project")),
    Query("estimated_cost_by_deployment", "Near-real-time ESTIMATED cost per deployment (Preview metric)",
          _estimated_cost((FoundryMetrics.DEPLOYMENT_DIMENSION,), "estimated_cost_by_deployment")),
    Query("ptu_headroom", "Provisioned (PTU) deployments: capacity, utilisation, regional quota", _ptu_headroom),
    Query("metric_definitions", "Metrics the Foundry resource exposes",
          lambda c: c.client.metrics.definitions(c.account_id)),
    # --- Quota, tags, alerts, gateway, prices
    Query("quota_usage", "Regional model quota (TPM) used and available",
          lambda c: [q.to_json() for q in c.client.quota.usages(c.location)]),
    Query("quota_models", "Models and SKUs available in the region", _quota_models),
    Query("tags", "Tag inventory plus the tags on the Foundry resource and project", _tags),
    Query("alerts", "Metric alerts, log alerts and action groups", _alerts),
    Query("apim_gateway", "API Management products, subscriptions and llm-token-limit policies", _apim,
          needs=("apim_service",)),
    Query("retail_price_sheet", "Azure retail prices per 1M tokens for the region (public price list)", _price_sheet),
]
# Every saved KQL query becomes its own file, run over the requested date range.
QUERIES += [
    Query(f"kql_{_slug(title)}", f"KQL: {title}", _kql(title, text.strip()), needs=("logs_target",))
    for title, text in queries.ALL.items()
]
NAMES = [q.name for q in QUERIES]


# ------------------------------------------------------------------- roles
#: Built-in roles whose "*/read" permission covers every query below (when held at a wide enough scope).
BROAD_READ = frozenset({"owner", "contributor", "reader"})
_COST = BROAD_READ | {"cost management reader", "cost management contributor"}
#: The four Foundry roles under their current names and the names they had before the rename. Per the Foundry docs
#: all of them can view cost controls and read usage metrics (``Microsoft.Insights/metrics/read``).
FOUNDRY_ROLES = frozenset({
    "foundry user", "foundry project manager", "foundry account owner", "foundry owner",
    "azure ai user", "azure ai project manager", "azure ai account owner", "azure ai owner",
})
_MONITOR_ONLY = BROAD_READ | {"monitoring reader", "monitoring contributor"}
_MONITOR = _MONITOR_ONLY | FOUNDRY_ROLES
_LOGS = _MONITOR_ONLY | {"log analytics reader", "log analytics contributor"}  # Foundry roles can't query workspaces
_FOUNDRY = BROAD_READ | FOUNDRY_ROLES | {"cognitive services contributor"}  # that role holds Microsoft.CognitiveServices/*
_APIM = BROAD_READ | {"api management service reader role", "api management service contributor",
                      "api management service operator role"}

#: Roles that allow each query (lower-case). ``None`` means no Azure role is needed (a public API).
#: Role names are matched case-insensitively and the roles are assumed to be held at a scope that covers the
#: resource (the subscription for the quota, Event Grid and tag-inventory calls).
QUERY_ROLES: dict[str, frozenset[str] | None] = {
    "cost_daily_total": _COST, "cost_by_project": _COST, "cost_by_agent": _COST, "cost_by_deployment": _COST,
    "cost_by_team": _COST, "cost_by_model": _COST, "cost_by_meter": _COST, "cost_forecast": _COST,
    "cost_dimensions": _COST, "cost_exports": _COST, "budgets": _COST, "budget_status": _COST,
    "cost_by_pricing_model": _COST, "cost_by_charge_type": _COST, "cost_anomalies": _COST, "effective_prices": _COST,
    "estimated_cost_by_project": _MONITOR, "estimated_cost_by_deployment": _MONITOR, "ptu_headroom": BROAD_READ,
    "cost_controls": _FOUNDRY, "foundry_account": _FOUNDRY, "foundry_projects": _FOUNDRY,
    "foundry_deployments": _FOUNDRY, "foundry_connections": _FOUNDRY,
    "foundry_event_grid_topics": BROAD_READ,
    "token_usage_by_deployment": _MONITOR, "token_usage_by_model": _MONITOR, "metric_definitions": _MONITOR,
    "quota_usage": BROAD_READ, "quota_models": BROAD_READ, "tags": BROAD_READ,
    "alerts": _MONITOR, "apim_gateway": _APIM,
    "retail_price_sheet": None,
}


def roles_for(query: Query) -> frozenset[str] | None:
    """Roles that allow ``query``; saved KQL queries use the log-reading roles."""
    if query.name in QUERY_ROLES:
        return QUERY_ROLES[query.name]
    if query.name.startswith("kql_"):
        return _LOGS
    return BROAD_READ  # a new query without an entry is assumed to need broad read access


def parse_roles(text: str) -> list[str]:
    """Split ``"Reader, Cost Management Reader"`` into role names; empty items are dropped."""
    roles = [r.strip() for r in text.split(",") if r.strip()]
    if not roles:
        raise ValueError("--roles needs at least one role name, comma-separated, e.g. 'Reader,Monitoring Reader'")
    return roles


def allowed_by_roles(query: Query, roles: list[str] | None) -> bool:
    """``True`` if the roles allow ``query``. ``roles=None`` (no switch) allows everything."""
    if roles is None:
        return True
    needed = roles_for(query)
    return needed is None or bool({r.strip().lower() for r in roles} & needed)


def known_role_names() -> set[str]:
    out: set[str] = set(BROAD_READ)
    for q in QUERIES:
        out |= roles_for(q) or set()
    return out


def _needs_text(query: Query) -> str:
    needed = sorted(roles_for(query) or ())
    narrow = [n for n in needed if n not in BROAD_READ]
    names = [n.title() for n in [*sorted(BROAD_READ), *narrow]]
    return "needs one of: " + ", ".join(names)


# ------------------------------------------------------------------- runner
@dataclass
class QueryResult:
    name: str
    status: str  #: ok | skipped | failed
    file: str | None = None
    rows: int | None = None
    detail: str | None = None
    seconds: float = 0.0


@dataclass
class ExportResult:
    folder: Path
    results: list[QueryResult]

    @property
    def failed(self) -> list[QueryResult]:
        return [r for r in self.results if r.status == "failed"]


class NamedRoles:
    """Access from role *names* the caller typed (``--roles "Reader,Monitoring Reader"``)."""

    def __init__(self, roles: list[str]) -> None:
        self.roles = roles

    def check(self, query_name: str) -> str | None:
        query = next(q for q in QUERIES if q.name == query_name)
        if allowed_by_roles(query, self.roles):
            return None
        return f"not allowed by the roles given ({_needs_text(query)})"

    def describe(self) -> list[str]:
        return self.roles


def run_export(
    ctx: ExportContext, output_dir: Path, only: list[str] | None = None, roles: list[str] | None = None,
    access: Any = None,
) -> ExportResult:
    """Run the selected queries and write one JSON file each under ``<output_dir>/FinOps/<today>/``.

    ``roles`` (role names) or ``access`` (a :class:`~foundry_finops.roles.RoleAccess` read from Azure) limit the
    download to the queries those roles allow; the rest are recorded as skipped. With neither, everything is tried.
    """
    if access is None and roles is not None:
        access = NamedRoles(roles)
    selected = QUERIES
    if only:
        unknown = [n for n in only if n not in NAMES]
        if unknown:
            raise ValueError(f"Unknown query name(s): {', '.join(unknown)}. Use --list to see them.")
        selected = [q for q in QUERIES if q.name in only]
    if ctx.end < ctx.start:
        raise ValueError("The end date is before the start date")

    folder = output_dir / FOLDER_NAME / ctx.today.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"account": ctx.account, "project": ctx.project, "start": ctx.start.isoformat(),
              "end": ctx.end.isoformat(), "subscription_id": ctx.client.subscription_id,
              "resource_group": ctx.client.resource_group, "location": ctx.location,
              "roles": None if access is None else ("auto" if hasattr(access, "grants") else roles)}
    if access is not None and hasattr(access, "grants"):
        params["discovered_roles"] = access.describe()
    results: list[QueryResult] = []
    for q in selected:
        began = time.monotonic()
        res = QueryResult(q.name, "ok")
        try:
            if access is not None and (reason := access.check(q.name)):
                raise QuerySkipped(reason)
            for need in q.needs:
                if not getattr(ctx, need):
                    flag = need.replace("_", "-")
                    raise QuerySkipped(f"needs --{flag} (or FINOPS_{need.upper()})")
            data = q.run(ctx)
            payload = {"query": q.name, "description": q.description, "generated_at": generated,
                       "parameters": params, "notes": ctx.notes.get(q.name, []), "data": _jsonable(data)}
            path = folder / f"{q.name}.json"
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
            res.file = path.name
            res.rows = len(data) if isinstance(data, (list, tuple)) else None
        except AuthenticationError:
            # No point trying the remaining queries with credentials that don't work: fail once, clearly.
            if not any(folder.iterdir()):  # leave no empty FinOps/<date> folders behind
                folder.rmdir()
                if not any(folder.parent.iterdir()):
                    folder.parent.rmdir()
            raise
        except QuerySkipped as exc:
            res.status, res.detail = "skipped", str(exc)
        except (FinOpsError, ValueError, KeyError) as exc:
            res.status = "failed"
            res.detail = _short(getattr(exc, "message", None) or str(exc).strip("'"))
        except Exception as exc:
            res.status, res.detail = "failed", _short(f"{type(exc).__name__}: {exc}")
        res.seconds = round(time.monotonic() - began, 2)
        results.append(res)

    manifest = {"generated_at": generated, "parameters": params,
                "queries": [asdict(r) for r in results]}
    (folder / "_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return ExportResult(folder, results)
