"""Typed models for requests and responses.

Every model is a plain dataclass with ``to_dict()`` (ARM request body) and ``from_dict()``
(ARM response). Unknown response fields are preserved in ``raw`` so nothing is lost when
the service adds properties.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from .errors import DependencyMissingError


class _StrEnum(str, Enum):
    def __str__(self) -> str:
        return str(self.value)


# ============================================================================ shared
def iso_utc(value: datetime | date | str) -> str:
    """Format a date/datetime as the ISO-8601 UTC string ARM expects (``...Z``)."""
    if isinstance(value, str):
        v = value.strip()
        if len(v) == 10 and v[4] == "-" and v[7] == "-":
            return f"{v}T00:00:00Z"
        if len(v) == 16 and "T" in v:  # <input type=datetime-local>
            return f"{v}:00Z"
        return v
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{value.isoformat()}T00:00:00Z"


def _parse_utc(value: str) -> datetime | None:
    """Parse an ISO 8601 UTC timestamp (up to seven fractional digits), or ``None`` if it isn't one."""
    import re

    v = re.sub(r"(\.\d{6})\d+", r"\1", value.strip().replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(v)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _props(d: Mapping[str, Any]) -> dict[str, Any]:
    return dict(d.get("properties") or {})


@dataclass
class ArmResource:
    """Fields shared by every ARM resource."""

    id: str | None = None
    name: str | None = None
    type: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_json(self) -> dict[str, Any]:
        """JSON-serialisable dict for UIs/exports (not the ARM request body)."""
        d = asdict(self)
        d.pop("raw", None)
        out: dict[str, Any] = _jsonable(d)
        return out


def _jsonable(o: Any) -> Any:
    if isinstance(o, Enum):
        return o.value
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    return o


# ===================================================================== tabular data
@dataclass
class Column:
    name: str
    type: str = "string"


@dataclass
class Table:
    """Tabular result shared by Cost Management, Log Analytics and metrics.

    Iterating yields one ``dict`` per row. Convert with :meth:`to_records`,
    :meth:`to_pandas`, :meth:`to_csv` or :meth:`to_json`.
    """

    columns: list[Column]
    rows: list[list[Any]]
    name: str = "PrimaryResult"

    def __iter__(self) -> Iterator[dict[str, Any]]:
        names = self.column_names
        for row in self.rows:
            yield dict(zip(names, row))

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def to_records(self) -> list[dict[str, Any]]:
        return list(self)

    def to_pandas(self) -> Any:
        try:
            import pandas as pd
        except ImportError as exc:
            raise DependencyMissingError("pip install 'foundry-finops[pandas]'") from exc
        return pd.DataFrame(self.rows, columns=self.column_names)

    def to_csv(self) -> str:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(self.column_names)
        writer.writerows(
            [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in row] for row in self.rows
        )
        return buf.getvalue()

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": [asdict(c) for c in self.columns],
            "rows": _jsonable(self.rows),
        }

    @classmethod
    def from_records(cls, records: Sequence[Mapping[str, Any]], name: str = "PrimaryResult") -> Table:
        names: list[str] = []
        for r in records:
            for k in r:
                if k not in names:
                    names.append(k)
        cols = [Column(n, _infer_type([r.get(n) for r in records])) for n in names]
        return cls(cols, [[r.get(n) for n in names] for r in records], name)


def _infer_type(values: Sequence[Any]) -> str:
    sample = next((v for v in values if v is not None), None)
    if isinstance(sample, bool):
        return "bool"
    if isinstance(sample, int):
        return "long"
    if isinstance(sample, float):
        return "real"
    if isinstance(sample, (datetime, date)):
        return "datetime"
    return "string"


# ================================================================ Foundry cost controls
class BudgetCategory(_StrEnum):
    COST = "Cost"  #: spending budget in USD
    TOKENS = "Tokens"


class TimeGrain(_StrEnum):
    DAILY = "Daily"
    WEEKLY = "Weekly"
    MONTHLY = "Monthly"
    #: Cost Management budgets only (not accepted by Foundry cost controls):
    QUARTERLY = "Quarterly"
    ANNUALLY = "Annually"
    BILLING_MONTH = "BillingMonth"
    BILLING_QUARTER = "BillingQuarter"
    BILLING_ANNUAL = "BillingAnnual"
    LAST_7_DAYS = "Last7Days"
    LAST_30_DAYS = "Last30Days"


class ThresholdType(_StrEnum):
    PERCENTAGE = "Percentage"
    ABSOLUTE = "Absolute"


class CostControlFilterName(_StrEnum):
    PROJECT = "project"
    DEPLOYMENT = "deployment"
    AGENT = "agent"
    SESSION_ID = "sessionId"
    TOKEN_TYPE = "tokenType"  # values: input / output (Tokens budgets only)


class BudgetSharing(_StrEnum):
    """How matching usage shares the budget (the ``grouping`` field)."""

    SHARED = "shared"  #: one budget for everything (grouping omitted)
    PER_AGENT = "agent"
    PER_SESSION = "sessionId"
    PER_PROJECT = "project"  # Foundry-resource cost controls only


@dataclass
class Threshold:
    """A ``Send alert`` or ``Block usage`` threshold."""

    threshold: float
    threshold_type: ThresholdType = ThresholdType.PERCENTAGE
    name: str | None = None
    #: Alerts only: resource ID of the Foundry connection to an Event Grid topic.
    event_grid_connection_id: str | None = None
    enabled: bool = True

    def _default_name(self, kind: str) -> str:
        unit = "Percent" if self.threshold_type == ThresholdType.PERCENTAGE else "Absolute"
        prefix = "Actual" if kind == "alert" else "Block"
        return self.name or f"{prefix}_GreaterThanOrEqualTo_{self.threshold:g}_{unit}"


@dataclass
class CostControl(ArmResource):
    """A Foundry cost control (``Microsoft.CognitiveServices/accounts[/projects]/costControls``).

    Example – $500/month per agent in the ``fraud-risk`` project, alert at 80 %, block at 100 %::

        CostControl(
            name="fraud-risk-monthly",
            category=BudgetCategory.COST,
            amount=500,
            time_grain=TimeGrain.MONTHLY,
            start_date="2026-12-01T00:00:00Z",
            end_date="2027-07-01T00:00:00Z",
            filters={"project": ["fraud-risk"]},
            sharing=BudgetSharing.PER_AGENT,
            alert=Threshold(80, event_grid_connection_id=conn_id),
            block=Threshold(100),
        )
    """

    category: BudgetCategory = BudgetCategory.COST
    amount: float = 0
    time_grain: TimeGrain = TimeGrain.MONTHLY
    start_date: str | None = None
    end_date: str | None = None
    #: filter name -> values (names, not resource IDs). Combined with AND.
    filters: dict[str, list[str]] = field(default_factory=dict)
    sharing: BudgetSharing = BudgetSharing.SHARED
    alert: Threshold | None = None
    block: Threshold | None = None
    #: Owner scope – populated from the ID: ``account`` or ``project``.
    project: str | None = None
    #: Read-only entity tag from the service; pass it as ``if_match`` to update or delete only if unchanged.
    etag: str | None = None

    @property
    def alerts(self) -> list[Threshold]:
        return [self.alert] if self.alert else []

    @property
    def blocks(self) -> list[Threshold]:
        return [self.block] if self.block else []

    # ------------------------------------------------------------- validation
    def validate(self) -> None:
        import re

        if self.name and not re.fullmatch(r"[A-Za-z0-9_-]{1,63}", self.name):
            raise ValueError("Cost control name must be 1-63 letters, numbers, hyphens or underscores")
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        if self.time_grain not in (TimeGrain.DAILY, TimeGrain.WEEKLY, TimeGrain.MONTHLY):
            raise ValueError("Cost controls reset Daily, Weekly or Monthly; the other periods are for budgets")
        if len(self.alerts) > 1 or len(self.blocks) > 1:
            raise ValueError("A Foundry cost control has at most one alert and one Block action (the service rejects more)")
        if self.category == BudgetCategory.TOKENS and int(self.amount) != self.amount:
            raise ValueError("Token limits must be whole numbers")
        for fname in ("project", "agent", "deployment"):
            for value in self.filters.get(fname, []):
                _check_short_name(fname, value)
        if self.project:
            _check_short_name("project", self.project)
        if "agent" in self.filters and len(self.filters.get("project", [])) != 1:
            raise ValueError("An agent filter needs a project filter with exactly one project")
        if "tokenType" in self.filters and self.category != BudgetCategory.TOKENS:
            raise ValueError("tokenType filters apply only to Tokens budgets")
        if len(self.filters.get("sessionId", [])) > 20:
            raise ValueError("A session filter accepts up to 20 session IDs")
        for fname, values in self.filters.items():
            if any(not str(v).strip() for v in values):
                raise ValueError(f"Values of the {fname} filter must be non-blank strings")
        bad_types = [v for v in self.filters.get("tokenType", []) if v not in ("input", "output")]
        if bad_types:
            raise ValueError(f"tokenType accepts input or output, not {bad_types}")
        if self.project and "project" in self.filters and list(self.filters["project"]) != [self.project]:
            raise ValueError("A project-owned cost control can select only its own project")
        if len(json.dumps(self.filters)) > 32 * 1024:
            raise ValueError("The serialized filters must not exceed 32 KiB")
        if self.project and self.sharing == BudgetSharing.PER_PROJECT:
            raise ValueError("Only a Foundry resource's cost controls can use a per-project budget")
        for t in (*self.alerts, *self.blocks):
            if (self.category == BudgetCategory.TOKENS and t.threshold_type == ThresholdType.ABSOLUTE
                    and int(t.threshold) != t.threshold):
                raise ValueError("Absolute thresholds on a Tokens budget must be whole numbers")
            if t.threshold_type == ThresholdType.PERCENTAGE and not 0 < t.threshold <= 100:
                raise ValueError("Percentage thresholds must be between 0 and 100")
            if t.threshold_type == ThresholdType.ABSOLUTE and not 0 < t.threshold <= self.amount:
                raise ValueError("Absolute thresholds must be positive and can't exceed the amount")
        for a in self.alerts:
            if not a.event_grid_connection_id:
                raise ValueError("An alert threshold needs event_grid_connection_id")
        for label, items in (("alert", self.alerts), ("block", self.blocks)):
            names = [t._default_name(label) for t in items]
            if len(set(names)) != len(names):
                raise ValueError(f"Two {label} thresholds have the same name/value; each must be unique")
        if self.alerts and self.blocks and (
            max(_pct(a, self.amount) for a in self.alerts) > min(_pct(b, self.amount) for b in self.blocks)
        ):
            raise ValueError("Alert thresholds must be at or below the block threshold")
        # The service requires a validity period with both dates (CostControlProperties.timePeriod), and the
        # Foundry docs say every cost control has an end date.
        if not self.start_date or not self.end_date:
            raise ValueError("A cost control needs both start_date and end_date: the service requires a validity "
                             "period. The cost control stops counting at the start of the end date.")
        start, end = _parse_utc(iso_utc(self.start_date)), _parse_utc(iso_utc(self.end_date))
        if start is None or end is None:
            raise ValueError("start_date and end_date must be ISO 8601 dates or UTC timestamps")
        if end <= start:
            raise ValueError("end_date must be later than start_date")

    # ---------------------------------------------------------------- mapping
    def to_resource(self, project: str | None = None) -> Any:
        """The official model for ``client.account_cost_controls`` (or ``client.project_cost_controls`` when the cost
        control is owned by a project): ``azure.mgmt.cognitiveservices.models.AccountCostControl`` / ``ProjectCostControl``.
        Validates first. ``project`` sets the owning project."""
        from azure.mgmt.cognitiveservices import models as cs

        if project:
            self.project = project
        body = self.to_dict()
        return cs.ProjectCostControl(body) if self.project else cs.AccountCostControl(body)

    @classmethod
    def from_resource(cls, resource: Any) -> CostControl:
        """Build a spec from an official ``AccountCostControl`` / ``ProjectCostControl`` (or the REST JSON)."""
        return cls.from_dict(resource.as_dict() if hasattr(resource, "as_dict") else resource)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        props: dict[str, Any] = {
            "category": str(self.category),
            "amount": int(self.amount) if self.category == BudgetCategory.TOKENS else self.amount,
            "timeGrain": str(self.time_grain),
        }
        if self.start_date or self.end_date:
            props["timePeriod"] = {
                k: iso_utc(v)
                for k, v in (("startDate", self.start_date), ("endDate", self.end_date))
                if v is not None
            }
        conditions = [
            {"additionalInfo": {"name": str(k), "operator": "In", "values": list(v)}}
            for k, v in self.filters.items()
            if v
        ]
        if len(conditions) == 1:
            props["filter"] = conditions[0]
        elif conditions:
            props["filter"] = {"and": conditions}
        if self.sharing != BudgetSharing.SHARED:
            props["grouping"] = [{"name": str(self.sharing), "type": "AdditionalInfoKey"}]
        if self.alerts:
            props["notifications"] = {
                a._default_name("alert"): {
                    "enabled": a.enabled,
                    "operator": "GreaterThanOrEqualTo",
                    "threshold": a.threshold,
                    "thresholdType": str(a.threshold_type),
                    "eventGridConnectionId": a.event_grid_connection_id,
                }
                for a in self.alerts
            }
        if self.blocks:
            props["providerProperties"] = {
                "actions": {
                    b._default_name("block"): {
                        "type": "Block",
                        "operator": "GreaterThanOrEqualTo",
                        "threshold": b.threshold,
                        "thresholdType": str(b.threshold_type),
                    }
                    for b in self.blocks
                }
            }
        return {"properties": props}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CostControl:
        p = _props(d)
        filters: dict[str, list[str]] = {}
        f = p.get("filter") or {}
        for cond in f.get("and", [f] if f else []):
            info = cond.get("additionalInfo") or {}
            if info.get("name"):
                filters[info["name"]] = list(info.get("values") or [])
        grouping = (p.get("grouping") or [{}])[0].get("name")
        alerts: list[Threshold] = []
        blocks: list[Threshold] = []
        for key, n in (p.get("notifications") or {}).items():
            alerts.append(Threshold(
                float(n.get("threshold", 0)),
                _enum(ThresholdType, n.get("thresholdType"), ThresholdType.PERCENTAGE),
                key,
                n.get("eventGridConnectionId"),
                bool(n.get("enabled", True)),
            ))
        for key, a in ((p.get("providerProperties") or {}).get("actions") or {}).items():
            blocks.append(Threshold(
                float(a.get("threshold", 0)), _enum(ThresholdType, a.get("thresholdType"), ThresholdType.PERCENTAGE), key
            ))
        tp = p.get("timePeriod") or {}
        rid = d.get("id") or ""
        project = None
        if "/projects/" in rid:
            project = rid.split("/projects/")[1].split("/")[0]
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            type=d.get("type"),
            raw=dict(d),
            category=_enum(BudgetCategory, p.get("category"), BudgetCategory.COST),
            amount=float(p.get("amount", 0) or 0),
            time_grain=_enum(TimeGrain, p.get("timeGrain"), TimeGrain.MONTHLY),
            start_date=tp.get("startDate"),
            end_date=tp.get("endDate"),
            filters=filters,
            sharing=_enum(BudgetSharing, grouping, BudgetSharing.SHARED),
            alert=alerts[0] if alerts else None,
            block=blocks[0] if blocks else None,
            project=project,
            etag=d.get("etag"),
        )


def _check_short_name(kind: str, value: str) -> None:
    """Project, agent and deployment values are plain names, not IDs (rules from the cost-control API)."""
    v = str(value)
    if not 1 <= len(v) <= 64:
        raise ValueError(f"A {kind} name must be 1-64 characters (got {len(v)})")
    if v != v.strip() or any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError(f"A {kind} name can't have leading or trailing spaces or control characters: {v!r}")
    if any(c in v for c in "/\\?#%") or v in (".", ".."):
        raise ValueError(f"A {kind} name is a plain name without / \\ ? # % or '.' segments, not a resource ID: {v!r}")


def _enum(cls: Any, value: Any, default: Any) -> Any:
    """Parse a service value into an enum, falling back so a new service value can't break listing."""
    try:
        return cls(value) if value else default
    except ValueError:
        return default


def _num(value: Any) -> float | None:
    """A number the service may send as a string (token budgets), or ``None`` when absent."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _whole(value: Any) -> int | None:
    n = _num(value)
    return int(n) if n is not None else None


def _pct(t: Threshold, amount: float) -> float:
    return t.threshold if t.threshold_type == ThresholdType.PERCENTAGE else t.threshold / amount * 100


@dataclass
class CostControlAlertEvent:
    """Parsed ``costControl.alert`` CloudEvent delivered through Event Grid."""

    source: str
    time: str
    subject: str
    unit: str
    threshold: str
    action: str
    severity: str
    total_consumed: float
    limit: float
    remaining: float
    window_end: str | None = None
    project_name: str | None = None
    agent_name: str | None = None
    model: str | None = None
    deployment_name: str | None = None
    #: The cost control's own start and end dates (UTC), when the event carries them.
    start_date: str | None = None
    end_date: str | None = None
    #: ``estimate`` for a spending budget (priced as in "How cost controls price usage"), ``usage`` for a token budget.
    cost_basis: str | None = None
    #: Cost (spending budget) or tokens (token budget) of the request that triggered the event.
    call_cost: float | None = None
    tokens_consumed: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    reasoning_tokens: int | None = None
    data: dict[str, Any] = field(default_factory=dict, repr=False)

    def dedupe_key_for(self, per: str | None = None) -> tuple[Any, ...]:
        """Key to notify once per threshold and period. Foundry sends one event per request over a threshold.

        The event doesn't carry the cost control's name, so the key uses the source, unit, limit, start date,
        threshold and window end, which tell cost controls on one resource apart. Add the agent or project only for a
        budget that is shared per agent (``per="agent"``) or per project (``per="project"``); for a budget everything
        shares (``per=None``) every request counts toward one total, so one notification is enough.
        """
        if per not in (None, "agent", "project"):
            raise ValueError("per must be None, 'agent' or 'project'")
        who = self.agent_name if per == "agent" else self.project_name if per == "project" else None
        return (self.source, self.unit, self.limit, self.start_date, self.threshold, self.window_end, per, who)

    @property
    def dedupe_key(self) -> tuple[Any, ...]:
        """Key for a budget per agent (or per project when no agent is named): one notification per agent or project.

        Use :meth:`dedupe_key_for` when you know how the budget is shared. For a budget everything shares, this key
        notifies once per agent, which is more than needed but never hides an alert.
        """
        return self.dedupe_key_for("agent" if self.agent_name else "project" if self.project_name else None)

    @classmethod
    def from_cloud_event(cls, event: Mapping[str, Any]) -> CostControlAlertEvent:
        if event.get("type") != "costControl.alert":
            raise ValueError(f"Not a cost control alert: {event.get('type')!r}")
        d = dict(event.get("data") or {})
        return cls(
            source=event.get("source", ""),
            time=event.get("time", ""),
            subject=event.get("subject", ""),
            unit=d.get("unit", ""),
            threshold=str(d.get("threshold", "")),
            action=d.get("action", ""),
            severity=d.get("severity", ""),
            total_consumed=float(d.get("totalConsumed", 0) or 0),
            limit=float(d.get("limit", 0) or 0),
            remaining=float(d.get("remaining", 0) or 0),
            window_end=d.get("windowEnd"),
            project_name=d.get("projectName"),
            agent_name=d.get("agentName"),
            model=d.get("model"),
            deployment_name=d.get("deploymentName"),
            start_date=d.get("startDate"),
            end_date=d.get("endDate"),
            cost_basis=d.get("costBasis"),
            call_cost=_num(d.get("callCost")),
            tokens_consumed=_num(d.get("tokensConsumed")),
            input_tokens=_whole(d.get("inputTokens")),
            output_tokens=_whole(d.get("outputTokens")),
            cached_tokens=_whole(d.get("cachedTokens")),
            reasoning_tokens=_whole(d.get("reasoningTokens")),
            data=d,
        )


def applies_to_project(cc: CostControl, project: str) -> bool:
    """``True`` if ``cc`` can count usage in ``project``: the project's own cost controls, and a Foundry resource's
    cost control that covers every project or has a project filter naming it."""
    if cc.project:
        return cc.project == project
    names = cc.filters.get("project")
    return not names or project in names


@dataclass(frozen=True)
class ProjectBudget:
    """What the project's **Cost** tab shows in its *Project budget* card."""

    #: The spending budget for the whole project (the lowest when several apply), or ``None``.
    budget: CostControl | None
    #: How many whole-project spending budgets apply ("Lowest of N cost controls").
    count: int
    #: Budgets per agent or per session, which the card counts separately.
    per_agent_or_session: int


def project_budget(cost_controls: Iterable[CostControl], project: str) -> ProjectBudget:
    """Pick the project budget the way the Foundry portal's card does.

    A whole-project budget is a **Spending** cost control that applies to the project and isn't narrowed to model
    deployments, agents or sessions, and isn't a token budget. Resource-wide budgets are not project budgets: a
    resource's cost control counts only when a project filter names the project. Budgets per agent or per session are
    counted separately. With several, the lowest amount is returned (the docs call it "Lowest of N").
    """
    whole: list[CostControl] = []
    split = 0
    for cc in cost_controls:
        if cc.category != BudgetCategory.COST or not applies_to_project(cc, project):
            continue
        if not cc.project and project not in cc.filters.get("project", []):
            continue  # a resource-wide budget isn't the project's budget
        if any(k in cc.filters for k in ("deployment", "agent", "sessionId")):
            continue
        if cc.sharing in (BudgetSharing.PER_AGENT, BudgetSharing.PER_SESSION):
            split += 1
        elif cc.sharing == BudgetSharing.SHARED:
            whole.append(cc)
    return ProjectBudget(min(whole, key=lambda c: c.amount, default=None), len(whole), split)


class AlertDeduper:
    """Notify once per threshold and period although Foundry sends an alert event for every request over it.

    ``store`` is anything with ``__contains__`` and ``add`` (a ``set``, or a wrapper over Redis or Table Storage); the
    default is an in-memory set that is cleared when it reaches ``max_size`` so a long-lived process can't grow it
    without bound.

        deduper = AlertDeduper()
        if deduper.first_time(event, per="agent"):
            notify(event)
    """

    def __init__(self, store: Any = None, *, max_size: int = 10_000) -> None:
        self._store = store if store is not None else set()
        self._max = max_size

    def first_time(self, event: CostControlAlertEvent, per: str | None = None) -> bool:
        """``True`` the first time this event's key is seen, then ``False`` for every repeat."""
        key = event.dedupe_key_for(per)
        if key in self._store:
            return False
        if isinstance(self._store, set) and len(self._store) >= self._max:
            self._store.clear()
        self._store.add(key)
        return True


# ----------------------------------------------------------------- states
class CostControlState(_StrEnum):
    """The states the Foundry portal shows in the **State** column of a cost control list.

    ``Needs setup`` (the cost control doesn't cover any usage yet) isn't modelled: it needs knowledge of which
    projects, deployments or agents exist and have traffic, which the cost control definition doesn't hold.
    """

    SCHEDULED = "Scheduled"  #: the start date is in the future
    ON_TRACK = "On track"  #: usage is below the Near threshold point
    NEAR_THRESHOLD = "Near threshold"  #: usage reached the lowest alert threshold, or 80 % without an alert
    LIMIT_REACHED = "Limit reached"  #: usage reached the budget or the Block threshold
    EXPIRED = "Expired"  #: the end date has passed
    ACTIVE = "Active"  #: in effect, but usage can't be read ("Usage unknown"); doesn't mean requests are blocked


@dataclass(frozen=True)
class CostControlStatus:
    state: CostControlState
    #: ``True`` when the limit is reached but the cost control has no Block action, so requests continue.
    over_budget: bool = False
    percent_used: float | None = None


def evaluate_cost_control(
    *,
    amount: float,
    usage: float | None,
    alert_percents: Sequence[float] = (),
    block_percents: Sequence[float] = (),
    start: str | datetime | None = None,
    end: str | datetime | None = None,
    now: datetime | None = None,
) -> CostControlStatus:
    """State of a cost control, as defined in the Foundry docs ("Cost control states").

    ``usage`` is in the budget's unit and may be ``None`` when it can't be read. Thresholds are percentages of the
    budget. With several alert or block thresholds the lowest one is reached first.
    """
    now = now or datetime.now(timezone.utc)
    begins = _parse_utc(iso_utc(start)) if isinstance(start, str) and start else start if isinstance(start, datetime) else None
    ends = _parse_utc(iso_utc(end)) if isinstance(end, str) and end else end if isinstance(end, datetime) else None
    if ends is not None and now >= ends:
        return CostControlStatus(CostControlState.EXPIRED)
    if begins is not None and now < begins:
        return CostControlStatus(CostControlState.SCHEDULED)
    if usage is None or not amount:
        return CostControlStatus(CostControlState.ACTIVE)
    pct = usage / amount * 100
    block = min(block_percents, default=None)
    near = min(alert_percents, default=80.0)
    if pct >= 100 or (block is not None and pct >= block):
        return CostControlStatus(CostControlState.LIMIT_REACHED, over_budget=block is None, percent_used=round(pct, 1))
    state = CostControlState.NEAR_THRESHOLD if pct >= near else CostControlState.ON_TRACK
    return CostControlStatus(state, percent_used=round(pct, 1))


# ========================================================== Cost Management budgets
@dataclass
class BudgetNotification:
    threshold: float
    contact_emails: list[str] = field(default_factory=list)
    contact_groups: list[str] = field(default_factory=list)  #: action group resource IDs
    contact_roles: list[str] = field(default_factory=list)
    threshold_type: str = "Actual"  #: Actual | Forecasted
    operator: str = "GreaterThan"  #: EqualTo | GreaterThan | GreaterThanOrEqualTo
    enabled: bool = True
    locale: str = "en-us"
    name: str | None = None

    def key(self) -> str:
        return self.name or f"{self.threshold_type}_{self.operator}_{self.threshold:g}_Percent"


@dataclass
class Budget(ArmResource):
    """An Azure Cost Management budget. Alerts on billed cost; it can't block requests."""

    amount: float = 0
    time_grain: TimeGrain | str = TimeGrain.MONTHLY
    start_date: str | None = None
    end_date: str | None = None
    #: Cost Management filter expression (``{"and": [{"tags": {...}}, {"dimensions": {...}}]}``).
    filter: dict[str, Any] | None = None
    notifications: list[BudgetNotification] = field(default_factory=list)
    current_spend: float | None = None
    forecast_spend: float | None = None
    currency: str | None = None
    etag: str | None = None

    def to_dict(self) -> dict[str, Any]:
        if not self.name:
            raise ValueError("budget.name is required")
        if self.amount <= 0:
            raise ValueError("amount must be positive")
        for kind in ("Actual", "Forecasted"):
            if sum(1 for n in self.notifications if n.threshold_type == kind) > 5:
                raise ValueError(f"A budget allows at most 5 {kind} notifications")
        for n in self.notifications:
            if not (n.contact_emails or n.contact_groups or n.contact_roles):
                raise ValueError(f"Notification at {n.threshold:g}% needs an email, action group or role to notify")
            if n.threshold_type not in ("Actual", "Forecasted"):
                raise ValueError("Notification threshold_type must be 'Actual' or 'Forecasted'")
        if not self.start_date:
            today = datetime.now(timezone.utc).date()
            self.start_date = iso_utc(date(today.year, today.month, 1))
        props: dict[str, Any] = {
            "category": "Cost",
            "amount": self.amount,
            "timeGrain": str(self.time_grain),
            "timePeriod": {"startDate": iso_utc(self.start_date)},
        }
        if self.end_date:
            props["timePeriod"]["endDate"] = iso_utc(self.end_date)
        if self.filter:
            props["filter"] = self.filter
        if self.notifications:
            props["notifications"] = {
                n.key(): {
                    "enabled": n.enabled,
                    "operator": n.operator,
                    "threshold": n.threshold,
                    "thresholdType": n.threshold_type,
                    "contactEmails": n.contact_emails,
                    "contactGroups": n.contact_groups,
                    "contactRoles": n.contact_roles,
                    "locale": n.locale,
                }
                for n in self.notifications
            }
        body: dict[str, Any] = {"properties": props}
        if self.etag:
            body["eTag"] = self.etag
        return body

    def percent_used(self) -> float | None:
        """Actual spend as a percentage of the amount, or ``None`` when the service reported no spend."""
        if self.current_spend is None or not self.amount:
            return None
        return round(self.current_spend / self.amount * 100, 2)

    def percent_forecast(self) -> float | None:
        if self.forecast_spend is None or not self.amount:
            return None
        return round(self.forecast_spend / self.amount * 100, 2)

    def notification_status(self) -> list[dict[str, Any]]:
        """For each notification, whether the spend the service reported has reached its threshold.

        Computed from ``currentSpend`` and ``forecastSpend`` on the budget itself, so it makes no extra call and
        invents nothing: ``reached`` is ``None`` when the service didn't report the spend it needs. This is
        the budget's own arithmetic, not a record of emails the service actually sent.
        """
        out = []
        for n in self.notifications:
            pct = self.percent_forecast() if n.threshold_type == "Forecasted" else self.percent_used()
            out.append({
                "notification": n.key(), "threshold_percent": n.threshold, "type": n.threshold_type,
                "spend_percent": pct, "reached": None if pct is None else pct >= n.threshold,
                "enabled": n.enabled,
            })
        return out

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Budget:
        p = _props(d)
        tp = p.get("timePeriod") or {}
        notes = [
            BudgetNotification(
                threshold=float(n.get("threshold", 0)),
                contact_emails=list(n.get("contactEmails") or []),
                contact_groups=list(n.get("contactGroups") or []),
                contact_roles=list(n.get("contactRoles") or []),
                threshold_type=n.get("thresholdType", "Actual"),
                operator=n.get("operator", "GreaterThan"),
                enabled=bool(n.get("enabled", True)),
                locale=n.get("locale", "en-us"),
                name=k,
            )
            for k, n in (p.get("notifications") or {}).items()
        ]
        cs = p.get("currentSpend") or {}
        fs = p.get("forecastSpend") or {}
        grain = p.get("timeGrain", "Monthly")
        return cls(
            id=d.get("id"),
            name=d.get("name"),
            type=d.get("type"),
            raw=dict(d),
            amount=float(p.get("amount", 0) or 0),
            time_grain=TimeGrain(grain) if grain in TimeGrain._value2member_map_ else grain,  # never rewrite a period we don't know
            start_date=tp.get("startDate"),
            end_date=tp.get("endDate"),
            filter=p.get("filter"),
            notifications=notes,
            current_spend=cs.get("amount"),
            forecast_spend=fs.get("amount"),
            currency=cs.get("unit"),
            etag=d.get("eTag"),
        )


# ======================================================== Cost Management queries
class Granularity(_StrEnum):
    NONE = "None"
    DAILY = "Daily"
    MONTHLY = "Monthly"


@dataclass
class CostQuery:
    """Builder for a Cost Management ``query`` request.

    >>> q = (CostQuery.month_to_date()
    ...        .group_by_tag("project")
    ...        .group_by("MeterSubCategory")
    ...        .where_tag("deployment", ["gpt-5.4-mini"]))
    """

    timeframe: str = "MonthToDate"
    start: str | None = None
    end: str | None = None
    granularity: Granularity = Granularity.DAILY
    cost_type: str = "ActualCost"  #: ActualCost | AmortizedCost | Usage
    metric: str = "Cost"  #: Cost | PreTaxCost | CostUSD | PreTaxCostUSD
    grouping: list[dict[str, str]] = field(default_factory=list)
    filters: list[dict[str, Any]] = field(default_factory=list)
    #: Also return ``UsageQuantity`` (units consumed) next to the cost, e.g. to derive an effective unit price.
    with_quantity: bool = False

    #: Time frames the Cost Management API accepts besides ``Custom``.
    TIMEFRAMES = ("MonthToDate", "BillingMonthToDate", "TheLastMonth", "TheLastBillingMonth", "WeekToDate",
                  "TheCurrentMonth")

    # constructors
    @classmethod
    def month_to_date(cls) -> CostQuery:
        return cls()

    @classmethod
    def for_timeframe(cls, timeframe: str) -> CostQuery:
        """One of the API's named time frames: ``WeekToDate``, ``TheLastMonth``, ``BillingMonthToDate``, ..."""
        if timeframe not in cls.TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(cls.TIMEFRAMES)} (use between() for a date range)")
        return cls(timeframe=timeframe)

    @classmethod
    def week_to_date(cls) -> CostQuery:
        return cls.for_timeframe("WeekToDate")

    @classmethod
    def last_month(cls) -> CostQuery:
        return cls.for_timeframe("TheLastMonth")

    @classmethod
    def billing_month_to_date(cls) -> CostQuery:
        return cls.for_timeframe("BillingMonthToDate")

    @classmethod
    def last_billing_month(cls) -> CostQuery:
        return cls.for_timeframe("TheLastBillingMonth")

    @classmethod
    def current_month(cls) -> CostQuery:
        return cls.for_timeframe("TheCurrentMonth")

    @classmethod
    def between(cls, start: date | datetime | str, end: date | datetime | str) -> CostQuery:
        return cls(timeframe="Custom", start=iso_utc(start), end=iso_utc(end))

    @classmethod
    def last_days(cls, days: int) -> CostQuery:
        from datetime import timedelta

        if days < 1:
            raise ValueError("days must be at least 1")
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        # `days` whole calendar days ending with today (a 23:59:59 start would drop most of the first day)
        return cls.between(today - timedelta(days=days - 1), today.replace(hour=23, minute=59, second=59))

    # fluent modifiers
    def daily(self) -> CostQuery:
        self.granularity = Granularity.DAILY
        return self

    def total(self) -> CostQuery:
        self.granularity = Granularity.NONE
        return self

    def amortized(self) -> CostQuery:
        self.cost_type = "AmortizedCost"
        return self

    def group_by(self, dimension: str) -> CostQuery:
        """Group by a dimension: ResourceId, ResourceGroupName, MeterCategory, MeterSubCategory, Meter, …"""
        self.grouping.append({"type": "Dimension", "name": dimension})
        return self

    def group_by_tag(self, tag: str) -> CostQuery:
        """Group by a tag such as ``project``, ``deployment`` or ``agent.name`` (Foundry auto-tags)."""
        self.grouping.append({"type": "TagKey", "name": tag})
        return self

    def where(self, dimension: str, values: Sequence[str]) -> CostQuery:
        self.filters.append({"dimensions": {"name": dimension, "operator": "In", "values": list(values)}})
        return self

    def where_tag(self, tag: str, values: Sequence[str]) -> CostQuery:
        self.filters.append({"tags": {"name": tag, "operator": "In", "values": list(values)}})
        return self

    @staticmethod
    def _condition(kind: str, name: str, values: Sequence[str]) -> dict[str, Any]:
        if kind not in ("dimension", "tag"):
            raise ValueError("kind must be 'dimension' or 'tag'")
        return {("dimensions" if kind == "dimension" else "tags"): {"name": name, "operator": "In",
                                                                    "values": list(values)}}

    def where_any(self, *conditions: tuple[str, str, Sequence[str]]) -> CostQuery:
        """Match rows that satisfy ANY of the conditions. Each is ``("dimension" | "tag", name, values)``.

        >>> q.where_any(("tag", "team", ["risk"]), ("dimension", "ResourceGroupName", ["rg-ai"]))
        """
        if len(conditions) < 2:
            raise ValueError("where_any needs at least two conditions")
        self.filters.append({"or": [self._condition(*c) for c in conditions]})
        return self

    def where_not(self, kind: str, name: str, values: Sequence[str]) -> CostQuery:
        """Exclude rows that match the condition."""
        self.filters.append({"not": self._condition(kind, name, values)})
        return self

    def quantity(self) -> CostQuery:
        """Also return ``UsageQuantity`` for each row."""
        self.with_quantity = True
        return self

    # ------------------------------------------------------------------- files
    @classmethod
    def from_dict(cls, body: Mapping[str, Any]) -> CostQuery:
        """Rebuild a query from a request body (the inverse of :meth:`to_dict`), e.g. a saved JSON file."""
        dataset = dict(body.get("dataset") or {})
        agg = dataset.get("aggregation") or {}
        metric = next((a.get("name") for a in agg.values() if a.get("name") != "UsageQuantity"), "Cost")
        flt = dataset.get("filter")
        filters = list(flt["and"]) if isinstance(flt, dict) and set(flt) == {"and"} else ([flt] if flt else [])
        tp = body.get("timePeriod") or {}
        try:
            granularity = Granularity(dataset.get("granularity") or "Daily")
        except ValueError as exc:
            raise ValueError(f"Unknown granularity {dataset.get('granularity')!r}") from exc
        return cls(
            timeframe=body.get("timeframe", "MonthToDate"), start=tp.get("from"), end=tp.get("to"),
            granularity=granularity, cost_type=body.get("type", "ActualCost"), metric=metric or "Cost",
            grouping=[dict(g) for g in dataset.get("grouping") or []], filters=filters,
            with_quantity=any(a.get("name") == "UsageQuantity" for a in agg.values()),
        )

    def save(self, path: str) -> None:
        """Write the query as JSON, so a saved view can live in a file or in source control."""
        import pathlib

        pathlib.Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> CostQuery:
        import pathlib

        try:
            body = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} isn't valid JSON: {exc}") from exc
        if not isinstance(body, dict):
            raise ValueError(f"{path} must contain a JSON object (a Cost Management query body)")
        return cls.from_dict(body)

    def to_dict(self) -> dict[str, Any]:
        if len(self.grouping) > 2:
            raise ValueError("Cost Management queries allow at most 2 group-by clauses")
        aggregation: dict[str, Any] = {"totalCost": {"name": self.metric, "function": "Sum"}}
        if self.with_quantity:
            aggregation["totalQuantity"] = {"name": "UsageQuantity", "function": "Sum"}
        dataset: dict[str, Any] = {
            "granularity": str(self.granularity),
            "aggregation": aggregation,
        }
        if self.grouping:
            dataset["grouping"] = self.grouping
        if len(self.filters) == 1:
            dataset["filter"] = self.filters[0]
        elif self.filters:
            dataset["filter"] = {"and": self.filters}
        body: dict[str, Any] = {"type": self.cost_type, "timeframe": self.timeframe, "dataset": dataset}
        if self.timeframe == "Custom":
            body["timePeriod"] = {"from": self.start, "to": self.end}
        return body


# ============================================================= Foundry resources
@dataclass
class AccountView(ArmResource):
    location: str | None = None
    kind: str | None = None
    sku: str | None = None
    endpoint: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    #: The Application Insights connection (``Usage data``) that records usage for the account's cost controls.
    app_insights_connection_id: str | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> AccountView:
        p = _props(d)
        return cls(
            d.get("id"), d.get("name"), d.get("type"), dict(d),
            d.get("location"), d.get("kind"), (d.get("sku") or {}).get("name"),
            p.get("endpoint"), dict(d.get("tags") or {}),
            (p.get("costControlConnections") or {}).get("appInsightsConnectionId"),
        )


@dataclass
class ProjectView(ArmResource):
    location: str | None = None
    display_name: str | None = None
    description: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ProjectView:
        p = _props(d)
        name = d.get("name") or ""
        return cls(
            d.get("id"), name.split("/")[-1], d.get("type"), dict(d),
            d.get("location"), p.get("displayName"), p.get("description"), dict(d.get("tags") or {}),
        )


@dataclass
class DeploymentView(ArmResource):
    """A model deployment. ``capacity`` is the TPM allocation in thousands for Standard SKUs."""

    model_name: str | None = None
    model_version: str | None = None
    model_format: str | None = None
    sku_name: str | None = None
    capacity: int | None = None
    rai_policy: str | None = None
    provisioning_state: str | None = None
    rate_limits: list[dict[str, Any]] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)
    #: ``Default`` or ``Priority`` (priority processing is priced differently). Needs API 2026-11-01 or newer.
    service_tier: str | None = None
    #: ``Running`` or ``Paused``.
    deployment_state: str | None = None
    #: Name of the deployment that serves requests this one would otherwise reject.
    spillover_deployment: str | None = None

    @property
    def is_provisioned(self) -> bool:
        """``True`` for provisioned throughput (PTU) SKUs such as ``ProvisionedManaged``, ``GlobalProvisionedManaged``."""
        return "provisioned" in (self.sku_name or "").lower()

    @property
    def tokens_per_minute(self) -> int | None:
        """Token rate limit reported by the service, else thousands of TPM for Standard SKUs.

        ``None`` for provisioned SKUs: their capacity is in PTUs, which don't convert to tokens per minute.
        """
        for rl in self.rate_limits:
            if rl.get("key") == "token":
                return int(rl.get("count", 0))
        if self.is_provisioned:
            return None
        return self.capacity * 1000 if self.capacity is not None else None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> DeploymentView:
        p = _props(d)
        m = p.get("model") or {}
        sku = d.get("sku") or {}
        return cls(
            d.get("id"), d.get("name"), d.get("type"), dict(d),
            m.get("name"), m.get("version"), m.get("format"),
            sku.get("name"), sku.get("capacity"), p.get("raiPolicyName"),
            p.get("provisioningState"), list(p.get("rateLimits") or []), dict(d.get("tags") or {}),
            p.get("serviceTier"), p.get("deploymentState"), p.get("spilloverDeploymentName"),
        )


@dataclass
class QuotaView:
    """One row of ``Microsoft.CognitiveServices/locations/usages`` (subscription quota)."""

    name: str
    display_name: str
    current_value: float
    limit: float
    unit: str

    @property
    def available(self) -> float:
        return max(self.limit - self.current_value, 0)

    @property
    def percent_used(self) -> float:
        return round(self.current_value / self.limit * 100, 2) if self.limit else 0.0

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(available=self.available, percent_used=self.percent_used)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> QuotaView:
        n = d.get("name") or {}
        return cls(
            n.get("value", ""), n.get("localizedValue", n.get("value", "")),
            float(d.get("currentValue", 0) or 0), float(d.get("limit", 0) or 0), d.get("unit", ""),
        )


# ========================================================================= metrics
@dataclass
class MetricPoint:
    timestamp: str
    value: float | None


@dataclass
class MetricSeries:
    metric: str
    unit: str
    dimensions: dict[str, str]
    points: list[MetricPoint]

    @property
    def total(self) -> float:
        return sum(p.value or 0 for p in self.points)


def metric_series_to_table(series: Sequence[MetricSeries]) -> Table:
    dim_names = sorted({k for s in series for k in s.dimensions})
    cols = [Column("TimeGenerated", "datetime"), Column("Metric")]
    cols += [Column(d) for d in dim_names] + [Column("Value", "real")]
    rows = [
        [p.timestamp, s.metric, *[s.dimensions.get(d) for d in dim_names], p.value]
        for s in series
        for p in s.points
    ]
    return Table(cols, rows, "Metrics")


# ===================================================================== alerts
@dataclass
class MetricAlert(ArmResource):
    """Azure Monitor metric alert, e.g. on ``ProcessedPromptTokens`` per deployment."""

    description: str = ""
    severity: int = 2
    enabled: bool = True
    scopes: list[str] = field(default_factory=list)
    metric_name: str = "TokenTransaction"
    metric_namespace: str = "Microsoft.CognitiveServices/accounts"
    operator: str = "GreaterThan"
    threshold: float = 0
    time_aggregation: str = "Total"
    dimensions: list[dict[str, Any]] = field(default_factory=list)
    window_size: str = "PT1H"
    evaluation_frequency: str = "PT15M"
    action_group_ids: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        crit = {
            "criterionType": "StaticThresholdCriterion",
            "name": "c1",
            "metricName": self.metric_name,
            "metricNamespace": self.metric_namespace,
            "operator": self.operator,
            "threshold": self.threshold,
            "timeAggregation": self.time_aggregation,
            "dimensions": self.dimensions,
        }
        return {
            "location": "global",
            "tags": self.tags,
            "properties": {
                "description": self.description,
                "severity": self.severity,
                "enabled": self.enabled,
                "scopes": self.scopes,
                "evaluationFrequency": self.evaluation_frequency,
                "windowSize": self.window_size,
                "criteria": {
                    "odata.type": "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria",
                    "allOf": [crit],
                },
                "autoMitigate": True,
                "actions": [{"actionGroupId": a} for a in self.action_group_ids],
            },
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> MetricAlert:
        p = _props(d)
        crit = ((p.get("criteria") or {}).get("allOf") or [{}])[0]
        return cls(
            id=d.get("id"), name=d.get("name"), type=d.get("type"), raw=dict(d),
            description=p.get("description", ""), severity=int(p.get("severity", 2)),
            enabled=bool(p.get("enabled", True)), scopes=list(p.get("scopes") or []),
            metric_name=crit.get("metricName", ""), metric_namespace=crit.get("metricNamespace", ""),
            operator=crit.get("operator", "GreaterThan"), threshold=float(crit.get("threshold", 0) or 0),
            time_aggregation=crit.get("timeAggregation", "Total"), dimensions=list(crit.get("dimensions") or []),
            window_size=p.get("windowSize", "PT1H"), evaluation_frequency=p.get("evaluationFrequency", "PT15M"),
            action_group_ids=[a.get("actionGroupId") for a in p.get("actions") or [] if a.get("actionGroupId")],
            tags=dict(d.get("tags") or {}),
        )


@dataclass
class ActionGroup(ArmResource):
    short_name: str = ""
    enabled: bool = True
    emails: dict[str, str] = field(default_factory=dict)  #: receiver name -> address
    webhooks: dict[str, str] = field(default_factory=dict)  #: receiver name -> URI
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": "Global",
            "tags": self.tags,
            "properties": {
                "groupShortName": (self.short_name or (self.name or "ag"))[:12],
                "enabled": self.enabled,
                "emailReceivers": [
                    {"name": n, "emailAddress": e, "useCommonAlertSchema": True} for n, e in self.emails.items()
                ],
                "webhookReceivers": [
                    {"name": n, "serviceUri": u, "useCommonAlertSchema": True} for n, u in self.webhooks.items()
                ],
            },
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ActionGroup:
        p = _props(d)
        return cls(
            d.get("id"), d.get("name"), d.get("type"), dict(d),
            p.get("groupShortName", ""), bool(p.get("enabled", True)),
            {r["name"]: r.get("emailAddress", "") for r in p.get("emailReceivers") or []},
            {r["name"]: r.get("serviceUri", "") for r in p.get("webhookReceivers") or []},
            dict(d.get("tags") or {}),
        )


@dataclass
class LogAlert(ArmResource):
    """Scheduled query (log search) alert – e.g. KQL over App Insights token traces per team."""

    location: str = ""
    description: str = ""
    severity: int = 2
    enabled: bool = True
    scopes: list[str] = field(default_factory=list)
    query: str = ""
    operator: str = "GreaterThan"
    threshold: float = 0
    time_aggregation: str = "Count"
    metric_measure_column: str | None = None
    window_size: str = "PT1H"
    evaluation_frequency: str = "PT15M"
    action_group_ids: list[str] = field(default_factory=list)
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        crit: dict[str, Any] = {
            "query": self.query,
            "timeAggregation": self.time_aggregation,
            "operator": self.operator,
            "threshold": self.threshold,
            "failingPeriods": {"numberOfEvaluationPeriods": 1, "minFailingPeriodsToAlert": 1},
        }
        if self.metric_measure_column:
            crit["metricMeasureColumn"] = self.metric_measure_column
        return {
            "location": self.location,
            "tags": self.tags,
            "kind": "LogAlert",
            "properties": {
                "description": self.description,
                "severity": self.severity,
                "enabled": self.enabled,
                "scopes": self.scopes,
                "evaluationFrequency": self.evaluation_frequency,
                "windowSize": self.window_size,
                "criteria": {"allOf": [crit]},
                "actions": {"actionGroups": self.action_group_ids},
            },
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> LogAlert:
        p = _props(d)
        crit = ((p.get("criteria") or {}).get("allOf") or [{}])[0]
        return cls(
            id=d.get("id"), name=d.get("name"), type=d.get("type"), raw=dict(d),
            location=d.get("location", ""), description=p.get("description", ""),
            severity=int(p.get("severity", 2)), enabled=bool(p.get("enabled", True)),
            scopes=list(p.get("scopes") or []), query=crit.get("query", ""),
            operator=crit.get("operator", "GreaterThan"), threshold=float(crit.get("threshold", 0) or 0),
            time_aggregation=crit.get("timeAggregation", "Count"),
            metric_measure_column=crit.get("metricMeasureColumn"),
            window_size=p.get("windowSize", "PT1H"), evaluation_frequency=p.get("evaluationFrequency", "PT15M"),
            action_group_ids=list((p.get("actions") or {}).get("actionGroups") or []),
            tags=dict(d.get("tags") or {}),
        )


# ======================================================================= APIM
@dataclass
class ApimProduct(ArmResource):
    display_name: str = ""
    description: str = ""
    state: str = "published"
    subscription_required: bool = True
    approval_required: bool = False
    subscriptions_limit: int | None = None

    def to_dict(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "displayName": self.display_name or self.name,
            "description": self.description,
            "state": self.state,
            "subscriptionRequired": self.subscription_required,
        }
        if self.subscription_required:
            props["approvalRequired"] = self.approval_required
            if self.subscriptions_limit is not None:
                props["subscriptionsLimit"] = self.subscriptions_limit
        return {"properties": props}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ApimProduct:
        p = _props(d)
        return cls(
            d.get("id"), d.get("name"), d.get("type"), dict(d),
            p.get("displayName", ""), p.get("description") or "", p.get("state", "published"),
            bool(p.get("subscriptionRequired", True)), bool(p.get("approvalRequired", False)),
            p.get("subscriptionsLimit"),
        )


@dataclass
class ApimSubscription(ArmResource):
    """APIM subscription – the usual unit for per-team / per-app token limits and chargeback."""

    display_name: str = ""
    scope: str = ""  #: e.g. ``/products/{productId}`` or ``/apis/{apiId}`` or ``/apis``
    state: str = "active"
    owner_id: str | None = None
    allow_tracing: bool = False

    def to_dict(self) -> dict[str, Any]:
        props: dict[str, Any] = {
            "displayName": self.display_name or self.name,
            "scope": self.scope,
            "state": self.state,
            "allowTracing": self.allow_tracing,
        }
        if self.owner_id:
            props["ownerId"] = self.owner_id
        return {"properties": props}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ApimSubscription:
        p = _props(d)
        scope = p.get("scope", "")
        if "/products/" in scope:
            scope = "/products/" + scope.split("/products/")[1]
        elif "/apis/" in scope:
            scope = "/apis/" + scope.split("/apis/")[1]
        return cls(
            d.get("id"), d.get("name"), d.get("type"), dict(d),
            p.get("displayName", ""), scope, p.get("state", "active"), p.get("ownerId"),
            bool(p.get("allowTracing", False)),
        )


@dataclass
class ApimApi(ArmResource):
    display_name: str = ""
    path: str = ""
    service_url: str | None = None
    api_type: str | None = None

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> ApimApi:
        p = _props(d)
        return cls(
            d.get("id"), d.get("name"), d.get("type"), dict(d),
            p.get("displayName", ""), p.get("path", ""), p.get("serviceUrl"), p.get("type"),
        )


def dumps(obj: Any) -> str:
    """``json.dumps`` that understands the models in this module."""
    if hasattr(obj, "to_json"):
        obj = obj.to_json()
    return json.dumps(_jsonable(obj), indent=2, default=str)
