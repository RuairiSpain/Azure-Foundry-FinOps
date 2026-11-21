"""Reports computed from data the Azure APIs already return. Nothing here is estimated or invented:

* :func:`detect_anomalies` flags days whose billed cost jumps above the group's own recent history.
* :func:`allocate_cost` splits a cost across groups in proportion to weights **you** supply (an allocation rule).
* :func:`ptu_headroom` joins provisioned deployments, regional quota and the utilisation metric.

Each function states its method and parameters in its output, so a number can always be traced back to the
inputs that produced it.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from . import resources
from ._client import FinOpsClient
from .models import Table
from .operations.monitor import (  # noqa: F401  (project_matches re-exported for callers)
    FoundryMetrics,
    project_matches,
)

__all__ = ["Anomaly", "allocate_cost", "anomalies_from_table", "detect_anomalies", "ptu_headroom"]


# ------------------------------------------------------------------- anomalies
@dataclass
class Anomaly:
    """One day whose cost is out of line with the preceding days of the same group."""

    date: str
    group: str
    cost: float
    #: Median cost of the preceding ``window`` days (days without a row count as zero cost).
    baseline: float
    #: Modified z-score ``0.6745 x (cost - median) / MAD`` (``inf`` when the history is perfectly flat).
    score: float
    #: Change against the baseline in percent, ``None`` when the baseline is zero.
    percent_change: float | None
    direction: str  # up | down


def detect_anomalies(
    rows: Iterable[Mapping[str, Any]],
    *,
    date_key: str = "UsageDate",
    group_key: str | None = None,
    value_key: str = "Cost",
    window: int = 28,
    min_history: int = 7,
    threshold: float = 3.5,
    min_change: float = 0.0,
    direction: str = "up",
) -> list[Anomaly]:
    """Find days where cost departs from the group's own recent history.

    Method: per group, build a daily series from first to last date (a day with no row counts as zero: Cost Management
    returns no row when nothing was charged). For every day with at least ``min_history`` earlier days, compare the cost
    with the median of the previous ``window`` days using the **modified z-score** (median and median absolute
    deviation, robust to earlier spikes). A day is reported when its score is beyond ``threshold`` (3.5 is the usual
    cut-off) and it differs from the median by at least ``min_change`` in the cost currency. ``direction`` is ``up``,
    ``down`` or ``both``. Newest first.

    This detects statistical outliers in your own data. It does not know about planned launches or price changes, and it
    is not Azure's anomaly alerting.
    """
    if direction not in ("up", "down", "both"):
        raise ValueError("direction must be up, down or both")
    if window < min_history or min_history < 3:
        raise ValueError("window must be at least min_history, and min_history at least 3")
    series: dict[str, dict[date, float]] = {}
    for r in rows:
        raw = r.get(date_key)
        if raw in (None, ""):
            continue
        day = date.fromisoformat(str(raw)[:10])
        group = (str(r.get(group_key)) if r.get(group_key) else "(untagged)") if group_key else "(all)"
        value = r.get(value_key)
        if value is None:
            continue
        series.setdefault(group, {})[day] = series.get(group, {}).get(day, 0.0) + float(value)

    found: list[Anomaly] = []
    for group, by_day in series.items():
        first, last = min(by_day), max(by_day)
        days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
        values = [by_day.get(d, 0.0) for d in days]
        for i in range(min_history, len(values)):
            history = values[max(0, i - window):i]
            med = statistics.median(history)
            mad = statistics.median(abs(x - med) for x in history)
            delta = values[i] - med
            if abs(delta) < max(min_change, 1e-12):
                continue
            score = float("inf") if mad == 0 else 0.6745 * delta / mad
            way = "up" if delta > 0 else "down"
            if (direction != "both" and way != direction) or abs(score) <= threshold:
                continue
            found.append(Anomaly(
                days[i].isoformat(), group, round(values[i], 4), round(med, 4),
                score if score in (float("inf"), float("-inf")) else round(score, 2),
                None if med == 0 else round(delta / med * 100, 1), way,
            ))
    return sorted(found, key=lambda a: (a.date, a.group), reverse=True)


def anomalies_from_table(table: Table, **kwargs: Any) -> list[Anomaly]:
    """:func:`detect_anomalies` over a Cost Management :class:`~foundry_finops.models.Table`.

    The group column is ``TagValue`` for tag groupings, otherwise the first column that isn't the date, cost or
    currency; with no such column the rows are treated as one group.
    """
    names = table.column_names
    group = kwargs.pop("group_key", None)
    if group is None:
        if "TagValue" in names:
            group = "TagValue"
        else:
            skip = {"UsageDate", "Cost", "PreTaxCost", "Currency", "UsageQuantity", "TagKey"}
            group = next((n for n in names if n not in skip), None)
    return detect_anomalies(table.to_records(), group_key=group, **kwargs)


# ------------------------------------------------------------------ allocation
def allocate_cost(
    total: float, weights: Mapping[str, float], *, decimals: int = 2
) -> list[dict[str, Any]]:
    """Split ``total`` across the keys of ``weights`` in proportion to their weights.

    Use it for a shared cost (a gateway, a telemetry workspace) that you want to spread by a measure you choose, such as
    each team's tokens. The weights are inputs; the result is an **allocation**, not billed cost. Amounts are rounded to
    ``decimals`` and the rounding remainder is handed out by largest remainder, so the parts add up to the rounded total
    exactly. Highest share first.
    """
    if not weights:
        raise ValueError("weights is empty: there is nothing to allocate to")
    if any(w < 0 for w in weights.values()):
        raise ValueError("weights can't be negative")
    wsum = sum(weights.values())
    if wsum <= 0:
        raise ValueError("The weights add up to zero, so there is no basis to allocate on")
    unit = 10 ** decimals
    target = round(total * unit)
    exact = {k: total * unit * w / wsum for k, w in weights.items()}
    base = {k: int(v // 1) for k, v in exact.items()}
    leftover = target - sum(base.values())
    for k in sorted(exact, key=lambda k: exact[k] - base[k], reverse=True)[: max(leftover, 0)]:
        base[k] += 1
    rows = [{"key": k, "weight": weights[k], "share_percent": round(weights[k] / wsum * 100, 4),
             "allocated": base[k] / unit} for k in weights]
    return sorted(rows, key=lambda r: -r["allocated"])


# ------------------------------------------------------------------- PTU report
def ptu_headroom(
    fo: FinOpsClient,
    account: str,
    *,
    hours: int = 24,
    resource_group: str | None = None,
    location: str | None = None,
) -> list[dict[str, Any]]:
    """Provisioned-throughput (PTU) deployments with their capacity, utilisation and the regional quota behind them.

    For each provisioned deployment of the Foundry resource:

    * ``capacity_ptu`` and the deployment's ``service_tier`` and ``deployment_state``, from the deployment itself;
    * ``utilization_avg_percent`` (mean of the interval averages) and ``utilization_max_percent`` over ``hours`` from the
      ``AzureOpenAIProvisionedManagedUtilizationV2`` metric. Microsoft documents that at 100 percent or more calls are
      throttled with HTTP 429, so ``at_or_over_100_percent`` says whether the maximum reached it;
    * the regional quota row (``quota_used``, ``quota_limit``, ``quota_available``) whose name ends in
      ``.<sku>.<model>``. If the service reports no such row the quota fields are ``None``.

    A value is ``None`` when Azure returned nothing for it. Nothing is estimated.
    """
    rg = resource_group or fo.resource_group
    if not rg:
        raise ValueError("resource_group is required (pass it or set a default on FinOpsClient)")
    deployments = [d for d in resources.list_deployments(fo, rg, account) if d.is_provisioned]
    if not deployments:
        return []
    region = location or resources.get_account(fo, rg, account).location
    quota = list(resources.list_quota(fo, region)) if region else []
    account_id = fo.account_id(account, rg)
    interval = "PT1H" if hours <= 48 else "P1D"
    stats: dict[str, dict[str, list[float]]] = {}
    for label, aggregation in (("avg", "Average"), ("max", "Maximum")):
        series = fo.metrics.query(
            account_id, [FoundryMetrics.PROVISIONED_UTILIZATION_V2], hours=hours, interval=interval,
            aggregation=aggregation, split_by=(FoundryMetrics.DEPLOYMENT_DIMENSION,),
        )
        for s in series:
            name = (s.dimensions.get(FoundryMetrics.DEPLOYMENT_DIMENSION.lower())
                    or s.dimensions.get(FoundryMetrics.DEPLOYMENT_DIMENSION) or "")
            stats.setdefault(name.lower(), {}).setdefault(label, []).extend(
                p.value for p in s.points if p.value is not None)

    out = []
    for d in deployments:
        st = stats.get((d.name or "").lower(), {})
        avg, mx = st.get("avg") or [], st.get("max") or []
        suffix = f".{d.sku_name}.{d.model_name}".lower()
        q = next((x for x in quota if x.name.lower().endswith(suffix)), None)
        out.append({
            "deployment": d.name, "model": d.model_name, "sku": d.sku_name, "capacity_ptu": d.capacity,
            "service_tier": d.service_tier, "deployment_state": d.deployment_state,
            "utilization_avg_percent": round(sum(avg) / len(avg), 2) if avg else None,
            "utilization_max_percent": round(max(mx), 2) if mx else None,
            "at_or_over_100_percent": (max(mx) >= 100) if mx else None,
            "metric_points": len(mx) or len(avg),
            "quota_name": q.name if q else None, "quota_used": q.current_value if q else None,
            "quota_limit": q.limit if q else None, "quota_available": q.available if q else None,
            "window_hours": hours,
        })
    return sorted(out, key=lambda r: -(r["utilization_max_percent"] or -1))


def _as_dicts(items: Sequence[Anomaly]) -> list[dict[str, Any]]:
    return [asdict(a) for a in items]
