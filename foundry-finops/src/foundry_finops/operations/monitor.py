"""Azure Monitor: platform metrics (near real-time tokens) and Log Analytics / App Insights KQL."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from ..models import Column, MetricPoint, MetricSeries, Table, iso_utc, metric_series_to_table
from ._base import _Operations


class FoundryMetrics:
    """Metric names on ``Microsoft.CognitiveServices/accounts`` (see Foundry metrics reference)."""

    INPUT_TOKENS = "ProcessedPromptTokens"
    OUTPUT_TOKENS = "GeneratedTokens"
    TOTAL_TOKENS = "TokenTransaction"
    REQUESTS = "AzureOpenAIRequests"
    LATENCY_TTLT_MS = "AzureOpenAITTLTInMS"
    #: Generic Foundry Models metrics (non-OpenAI models)
    MODEL_INPUT_TOKENS = "InputTokens"
    MODEL_OUTPUT_TOKENS = "OutputTokens"
    MODEL_TOTAL_TOKENS = "TotalTokens"
    MODEL_REQUESTS = "ModelRequests"

    #: Estimated USD cost of Foundry model usage per minute (Preview; an estimate, not an invoice or billed charge).
    #: Dimensions: ProjectId, ModelDeploymentName, ModelName, ModelVersion, Region. Not exportable.
    ESTIMATED_COST = "FoundryModelEstimatedCost"
    #: Provisioned (PTU) deployment utilisation in percent: (PTUs consumed / PTUs deployed) x 100. At or above 100
    #: calls are throttled (HTTP 429). ``PROVISIONED_UTILIZATION_V2`` is the current one; the original is deprecated.
    PROVISIONED_UTILIZATION_V2 = "AzureOpenAIProvisionedManagedUtilizationV2"
    PROVISIONED_UTILIZATION = "ProvisionedUtilization"
    #: Tokens minus cached tokens (PTU deployments); also split by ServiceTierRequest and ServiceTierResponse.
    ACTIVE_TOKENS = "ActiveTokens"
    #: Percentage of prompt tokens that hit the prompt cache.
    CACHE_MATCH_RATE = "AzureOpenAIContextTokensCacheMatchRate"
    MODEL_ROUTER_INPUT_TOKENS = "ModelRouterInputTokens"
    MODEL_ROUTER_OUTPUT_TOKENS = "ModelRouterOutputTokens"
    MODEL_ROUTER_CACHED_TOKENS = "ModelRouterCachedTokens"
    MODEL_ROUTER_REQUESTS = "ModelRouterRequests"
    AUDIO_PROMPT_TOKENS = "AudioPromptTokens"
    AUDIO_COMPLETION_TOKENS = "AudioCompletionTokens"
    #: Anthropic prompt-cache tokens.
    CACHE_READ_INPUT_TOKENS = "cacheReadInputTokens"
    CACHE_WRITE_1H_INPUT_TOKENS = "ephemeral1hInputTokens"
    CACHE_WRITE_5M_INPUT_TOKENS = "ephemeral5mInputTokens"

    TOKENS = (INPUT_TOKENS, OUTPUT_TOKENS, TOTAL_TOKENS)
    DEPLOYMENT_DIMENSION = "ModelDeploymentName"
    MODEL_DIMENSION = "ModelName"
    PROJECT_DIMENSION = "ProjectId"


def project_matches(dimension_value: str, project: str) -> bool:
    """Does a ``ProjectId`` metric dimension value refer to the Foundry project named ``project``?

    The metric doesn't document the format of its ``ProjectId`` values, so this accepts the plain name or a resource
    ID that ends in ``/projects/<name>``, case-insensitively, and nothing else. Callers must treat "no match" as
    "unknown", never guess.
    """
    v, p = str(dimension_value).strip().lower(), str(project).strip().lower()
    return bool(p) and (v == p or v.endswith("/projects/" + p))


def _timespan(start: datetime | str | None, end: datetime | str | None, hours: int) -> str:
    if hours < 1 and start is None:
        raise ValueError("hours must be at least 1")
    end_dt = end or datetime.now(timezone.utc)
    if start is None:
        if not isinstance(end_dt, datetime):
            raise ValueError("start is required when end is given as a string")
        start = end_dt - timedelta(hours=hours)
    return f"{iso_utc(start)}/{iso_utc(end_dt)}"


class MetricsOperations(_Operations):
    """Read Azure Monitor platform metrics for any resource (Foundry, APIM, …)."""

    def definitions(self, resource_id: str) -> list[dict[str, Any]]:
        url = f"{resource_id}/providers/Microsoft.Insights/metricDefinitions"
        return list((self._arm("GET", url, self._v.metrics).body or {}).get("value") or [])

    def query(
        self,
        resource_id: str,
        metrics: Sequence[str] = FoundryMetrics.TOKENS,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        hours: int = 24,
        interval: str = "PT1H",
        aggregation: str = "Total",
        split_by: Sequence[str] = (FoundryMetrics.DEPLOYMENT_DIMENSION,),
        filter: str | None = None,
        top: int = 100,
    ) -> list[MetricSeries]:
        """Return one :class:`MetricSeries` per metric × dimension combination."""
        params: dict[str, Any] = {
            "metricnames": ",".join(metrics),
            "timespan": _timespan(start, end, hours),
            "interval": interval,
            "aggregation": aggregation,
            "top": top,
        }
        flt = filter or " and ".join(f"{d} eq '*'" for d in split_by)
        if flt:
            params["$filter"] = flt
        url = f"{resource_id}/providers/Microsoft.Insights/metrics"
        body = self._arm("GET", url, self._v.metrics, params=params).body or {}
        agg_key = aggregation.split(",")[0].strip().lower()
        out: list[MetricSeries] = []
        for metric in body.get("value") or []:
            mname = (metric.get("name") or {}).get("value", "")
            for ts in metric.get("timeseries") or []:
                dims = {
                    (m.get("name") or {}).get("value", ""): m.get("value", "")
                    for m in ts.get("metadatavalues") or []
                }
                pts = [MetricPoint(p.get("timeStamp", ""), p.get(agg_key)) for p in ts.get("data") or []]
                out.append(MetricSeries(mname, metric.get("unit", ""), dims, pts))
        return out

    def estimated_cost(
        self,
        resource_id: str,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        hours: int = 24,
        by: Sequence[str] = (FoundryMetrics.PROJECT_DIMENSION,),
        interval: str = "PT1H",
        top: int = 1000,
    ) -> list[dict[str, Any]]:
        """Near-real-time **estimated** USD cost of Foundry model usage, from the ``FoundryModelEstimatedCost`` metric.

        One record per time interval and ``by`` combination, with the values exactly as Azure Monitor returns them.
        Microsoft documents the metric as a Preview estimate that applies to Foundry models only; Cost Management
        stays the system of record for billed cost. ``by`` takes metric dimensions (``ProjectId``,
        ``ModelDeploymentName``, ``ModelName``, ``ModelVersion``, ``Region``). Dimension values are not translated: a
        ``ProjectId`` is whatever the service reports.
        """
        series = self.query(
            resource_id, [FoundryMetrics.ESTIMATED_COST], start=start, end=end, hours=hours, interval=interval,
            aggregation="Total", split_by=tuple(by), top=top,
        )
        out: list[dict[str, Any]] = []
        for s in series:
            for p in s.points:
                out.append({"timestamp": p.timestamp, **s.dimensions, "estimated_cost_usd": p.value})
        return out

    def estimated_cost_totals(
        self, resource_id: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """:meth:`estimated_cost` summed per ``by`` combination over the whole period, highest first.

        Intervals the service reported no value for are skipped, not counted as zero.
        """
        by = tuple(kwargs.get("by", (FoundryMetrics.PROJECT_DIMENSION,)))
        totals: dict[tuple[str, ...], float] = {}
        for r in self.estimated_cost(resource_id, **kwargs):
            if r["estimated_cost_usd"] is None:
                continue
            key = tuple(str(r.get(d.lower()) or r.get(d) or "") for d in by)
            totals[key] = totals.get(key, 0.0) + float(r["estimated_cost_usd"])
        rows = [{**{d.lower(): v for d, v in zip(by, k, strict=True)}, "estimated_cost_usd": round(v, 6)}
                for k, v in totals.items()]
        return sorted(rows, key=lambda r: -r["estimated_cost_usd"])

    def query_table(self, resource_id: str, metrics: Sequence[str] = FoundryMetrics.TOKENS, **kwargs: Any) -> Table:
        """Same as :meth:`query` but flattened into a :class:`Table` (one row per point)."""
        return metric_series_to_table(self.query(resource_id, metrics, **kwargs))


class LogsOperations(_Operations):
    """Run KQL against a Log Analytics workspace or an Application Insights resource."""

    def _post(self, url: str, query: str, timespan: str | None, extra: dict[str, Any] | None = None) -> list[Table]:
        body: dict[str, Any] = {"query": query}
        if timespan:
            body["timespan"] = timespan
        if extra:
            body.update(extra)
        if not query or not query.strip():
            raise ValueError("query is required")
        resp = self._t.request("POST", url, scope=self._cloud.logs_scope, json_body=body)
        tables = []
        for t in (resp.body or {}).get("tables") or []:
            cols = [Column(c.get("name", ""), c.get("type", "string")) for c in t.get("columns") or []]
            tables.append(Table(cols, list(t.get("rows") or []), t.get("name", "PrimaryResult")))
        return tables

    def query_workspace(
        self, workspace_id: str, query: str, *, timespan: str | None = "P1D",
        additional_workspaces: Sequence[str] = (),
    ) -> Table:
        """Query a workspace by its **workspace (customer) ID** GUID. ``timespan`` is ISO-8601 (``P7D``)."""
        extra = {"workspaces": list(additional_workspaces)} if additional_workspaces else None
        url = f"{self._cloud.log_analytics.rstrip('/')}/v1/workspaces/{self._seg(workspace_id, 'workspace_id')}/query"
        return _first(self._post(url, query, timespan, extra))

    def query_resource(self, resource_id: str, query: str, *, timespan: str | None = "P1D") -> Table:
        """Resource-centric query, e.g. against the Application Insights resource used for tracing."""
        if not resource_id.startswith("/subscriptions/") or "?" in resource_id or ".." in resource_id:
            raise ValueError("resource_id must be a full Azure resource ID starting with /subscriptions/")
        url = f"{self._cloud.log_analytics.rstrip('/')}/v1{resource_id}/query"
        return _first(self._post(url, query, timespan))

    def query(self, target: str, query: str, *, timespan: str | None = "P1D") -> Table:
        """Dispatch to :meth:`query_resource` for ARM IDs, else :meth:`query_workspace`."""
        if target.lower().startswith("/subscriptions/"):
            return self.query_resource(target, query, timespan=timespan)
        return self.query_workspace(target, query, timespan=timespan)


def _first(tables: list[Table]) -> Table:
    return tables[0] if tables else Table([], [])
