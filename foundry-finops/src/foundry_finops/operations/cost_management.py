"""Azure Cost Management: billed cost queries, forecasts, budgets, exports and tag inheritance."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from ..errors import HttpResponseError
from ..models import Budget, Column, CostQuery, Table
from ._base import _Operations
from .pricing import EffectivePrices

#: Billing tags that Foundry stamps on model charges.
FOUNDRY_COST_TAGS = ("project", "deployment", "agent.name")


class CostManagementOperations(_Operations):
    """Billed cost (8–24 h latency). ``scope`` defaults to the client's resource group or subscription.

    Scopes can be a subscription, resource group, a single resource ID (e.g. the Foundry
    resource – required to filter by the ``project`` tag), management group or billing scope.
    """

    def _scope(self, scope: str | None) -> str:
        if scope:
            return scope if scope.startswith("/") else f"/{scope}"
        return self.resource_group_scope() if self._rg else self.subscription_scope

    # ------------------------------------------------------------------ query
    def query(self, query: CostQuery | Mapping[str, Any], *, scope: str | None = None) -> Table:
        """Run a Cost Management query and return every page as one :class:`Table`.

        ``UsageDate`` (``yyyymmdd``) is normalised to an ISO date string.
        """
        body = query.to_dict() if isinstance(query, CostQuery) else dict(query)
        url = f"{self._scope(scope)}/providers/Microsoft.CostManagement/query"
        resp = self._arm("POST", url, self._v.cost_management, body=body)
        props = (resp.body or {}).get("properties") or {}
        columns = [Column(c["name"], c.get("type", "String")) for c in props.get("columns") or []]
        rows: list[list[Any]] = list(props.get("rows") or [])
        next_link = props.get("nextLink")
        while next_link:
            page = self._t.request("POST", next_link, scope=self._cloud.arm_scope, json_body=body).body or {}
            pprops = page.get("properties") or {}
            rows.extend(pprops.get("rows") or [])
            next_link = pprops.get("nextLink")
        return _normalise_dates(Table(columns, rows, "Cost"))

    def forecast(
        self, query: CostQuery | Mapping[str, Any], *, scope: str | None = None, include_actual: bool = True
    ) -> Table:
        """Cost forecast for the query's time period (``timeframe`` should be ``Custom``)."""
        body = query.to_dict() if isinstance(query, CostQuery) else dict(query)
        body.setdefault("includeActualCost", include_actual)
        body.setdefault("includeFreshPartialCost", False)
        body["dataset"].pop("grouping", None)  # forecast doesn't support grouping
        url = f"{self._scope(scope)}/providers/Microsoft.CostManagement/forecast"
        props = (self._arm("POST", url, self._v.cost_management, body=body).body or {}).get("properties") or {}
        columns = [Column(c["name"], c.get("type", "String")) for c in props.get("columns") or []]
        return _normalise_dates(Table(columns, list(props.get("rows") or []), "Forecast"))

    def effective_prices(
        self,
        *,
        scope: str | None = None,
        days: int = 30,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        meter_categories: Sequence[str] | None = None,
    ) -> EffectivePrices:
        """What each meter actually cost per unit, derived from your own billed cost and quantity.

        Runs one Cost Management query (cost and ``UsageQuantity`` grouped by ``Meter`` and ``UnitOfMeasure``) and
        divides. Because it comes from your bill, it reflects the discounts and agreements that apply to you, which the
        public retail list does not. It needs billed usage in the period: a meter with none gets no price. Use
        ``scope=fo.foundry.account_id(...)`` for one Foundry resource, and ``meter_categories`` to narrow to given
        ``MeterCategory`` values. Turn the result into a sheet with ``.to_price_sheet()`` and compare it with retail using
        :func:`foundry_finops.operations.pricing.effective_discounts`.
        """
        if bool(start) != bool(end):
            raise ValueError("Pass both start and end, or neither (then the last `days` days are used)")
        try:
            known = {str(d.get("name")) for d in self.dimensions(scope=scope)}
        except HttpResponseError:
            known = set()  # the pre-check is a courtesy: let the query itself report the problem
        missing = [n for n in ("Meter", "UnitOfMeasure") if known and n not in known]
        if missing:
            raise ValueError(f"This scope's Cost Management dimensions don't include {', '.join(missing)}. "
                             f"Available: {', '.join(sorted(known))}")
        q = CostQuery.between(start, end) if start and end else CostQuery.last_days(days)
        q = q.total().quantity().group_by("Meter").group_by("UnitOfMeasure")
        if meter_categories:
            q.where("MeterCategory", meter_categories)
        return EffectivePrices.from_records(self.query(q, scope=scope))

    # ------------------------------------------------------- Foundry shortcuts
    def cost_by(
        self,
        tag_or_dimension: str,
        *,
        scope: str | None = None,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        days: int = 30,
        daily: bool = True,
        dimension: bool | None = None,
    ) -> Table:
        """Cost grouped by a Foundry tag (``project``/``deployment``/``agent.name``/your tag) or dimension.

        To group by the ``project`` tag, pass the Foundry resource ID as ``scope``. A name that
        starts with a capital letter is treated as a dimension (``MeterSubCategory``); pass
        ``dimension=False`` for a tag whose name is capitalised, or ``True`` to force a dimension.
        """
        if bool(start) != bool(end):
            raise ValueError("Pass both start and end, or neither (then the last `days` days are used)")
        if not start and days < 1:
            raise ValueError("days must be at least 1")
        q = CostQuery.between(start, end) if start and end else CostQuery.last_days(days)
        q = q.daily() if daily else q.total()
        if dimension is None:  # Cost Management dimensions are PascalCase, Foundry/team tags are lower case
            dimension = tag_or_dimension[:1].isupper()
        q = q.group_by(tag_or_dimension) if dimension else q.group_by_tag(tag_or_dimension)
        return self.query(q, scope=scope)

    def dimensions(self, *, scope: str | None = None) -> list[dict[str, Any]]:
        url = f"{self._scope(scope)}/providers/Microsoft.CostManagement/dimensions"
        return list((self._arm("GET", url, self._v.cost_management).body or {}).get("value") or [])

    # ---------------------------------------------------------------- budgets
    def _budgets(self, scope: str | None) -> str:
        return f"{self._scope(scope)}/providers/Microsoft.CostManagement/budgets"

    def list_budgets(self, *, scope: str | None = None) -> Iterator[Budget]:
        for d in self._list(self._budgets(scope), self._v.cost_management):
            yield Budget.from_dict(d)

    def get_budget(self, name: str, *, scope: str | None = None) -> Budget:
        return Budget.from_dict(self._arm("GET", f"{self._budgets(scope)}/{self._seg(name)}", self._v.cost_management).body)

    def create_or_update_budget(self, budget: Budget, *, scope: str | None = None) -> Budget:
        """Create or replace a budget. Up to 5 Actual and 5 Forecasted notifications.

        Pass ``budget.etag`` (from :meth:`get_budget`) for optimistic concurrency.
        """
        if not budget.name:
            raise ValueError("budget.name is required")
        url = f"{self._budgets(scope)}/{budget.name}"
        return Budget.from_dict(self._arm("PUT", url, self._v.cost_management, body=budget.to_dict()).body)

    def delete_budget(self, name: str, *, scope: str | None = None) -> None:
        self._arm("DELETE", f"{self._budgets(scope)}/{self._seg(name)}", self._v.cost_management)

    # ---------------------------------------------------------------- exports
    def _exports(self, scope: str | None) -> str:
        return f"{self._scope(scope)}/providers/Microsoft.CostManagement/exports"

    def list_exports(self, *, scope: str | None = None) -> list[dict[str, Any]]:
        body = self._arm("GET", self._exports(scope), self._v.cost_management).body or {}
        return list(body.get("value") or [])

    EXPORT_TYPES = ("Usage", "ActualCost", "AmortizedCost", "FocusCost", "PriceSheet", "ReservationTransactions",
                    "ReservationRecommendations", "ReservationDetails")
    EXPORT_TIMEFRAMES = ("MonthToDate", "BillingMonthToDate", "TheLastMonth", "TheLastBillingMonth", "WeekToDate",
                         "TheCurrentMonth", "Custom")

    def get_export(self, name: str, *, scope: str | None = None) -> dict[str, Any]:
        """One export's definition, schedule and delivery settings."""
        return dict(self._arm("GET", f"{self._exports(scope)}/{self._seg(name)}", self._v.cost_management).body or {})

    def create_or_update_export(
        self,
        name: str,
        *,
        storage_account_id: str,
        container: str,
        root_folder: str = "foundry-finops",
        recurrence: str = "Daily",
        cost_type: str = "ActualCost",
        timeframe: str = "MonthToDate",
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        file_format: str = "Csv",
        scope: str | None = None,
        granularity: str = "Daily",
        compression: str | None = None,
        partition_data: bool | None = None,
        overwrite_behavior: str | None = None,
        description: str | None = None,
        columns: Sequence[str] | None = None,
        data_version: str | None = None,
        data_from: datetime | str | None = None,
        data_to: datetime | str | None = None,
        active: bool = True,
        etag: str | None = None,
    ) -> dict[str, Any]:
        """Create or replace a scheduled cost export to a storage account (the recommended bulk-data path).

        ``cost_type`` is one of :attr:`EXPORT_TYPES`; ``FocusCost`` delivers the FinOps Open Cost and Usage
        Specification format. ``file_format`` is ``Csv`` or ``Parquet``; ``compression`` ``gzip``, ``snappy`` or
        ``none``; ``overwrite_behavior`` ``OverwritePreviousReport`` or ``CreateNewReport``. ``timeframe="Custom"``
        needs ``data_from`` and ``data_to`` (the period the export covers), while ``start`` and ``end`` are the
        schedule's recurrence period. Pass ``etag`` (from :meth:`get_export`) to update only if unchanged. Options left at
        ``None`` are not sent, so the service's own defaults apply.
        """
        from ..models import iso_utc

        if cost_type not in self.EXPORT_TYPES:
            raise ValueError(f"cost_type must be one of {', '.join(self.EXPORT_TYPES)}")
        if timeframe not in self.EXPORT_TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(self.EXPORT_TIMEFRAMES)}")
        if recurrence not in ("Daily", "Weekly", "Monthly", "Annually"):
            raise ValueError("recurrence must be Daily, Weekly, Monthly or Annually")
        if file_format not in ("Csv", "Parquet"):
            raise ValueError("file_format must be Csv or Parquet")
        if granularity not in ("Daily", "Monthly"):
            raise ValueError("granularity must be Daily or Monthly")
        if compression is not None and compression not in ("gzip", "snappy", "none"):
            raise ValueError("compression must be gzip, snappy or none")
        if overwrite_behavior is not None and overwrite_behavior not in ("OverwritePreviousReport", "CreateNewReport"):
            raise ValueError("overwrite_behavior must be OverwritePreviousReport or CreateNewReport")
        if timeframe == "Custom" and not (data_from and data_to):
            raise ValueError("timeframe='Custom' needs data_from and data_to")

        now = datetime.now(timezone.utc)
        try:
            default_end = now.replace(year=now.year + 1)
        except ValueError:  # 29 February
            default_end = now.replace(year=now.year + 1, day=28)
        definition: dict[str, Any] = {"type": cost_type, "timeframe": timeframe, "dataSet": {"granularity": granularity}}
        configuration = {k: v for k, v in (("columns", list(columns) if columns else None),
                                           ("dataVersion", data_version)) if v}
        if configuration:
            definition["dataSet"]["configuration"] = configuration
        if timeframe == "Custom":
            definition["timePeriod"] = {"from": iso_utc(data_from), "to": iso_utc(data_to)}  # type: ignore[arg-type]
        props: dict[str, Any] = {
            "schedule": {
                "status": "Active" if active else "Inactive",
                "recurrence": recurrence,
                "recurrencePeriod": {"from": iso_utc(start or now), "to": iso_utc(end or default_end)},
            },
            "format": file_format,
            "deliveryInfo": {"destination": {"resourceId": storage_account_id, "container": container,
                                             "rootFolderPath": root_folder}},
            "definition": definition,
        }
        for key, value in (("compressionMode", compression), ("partitionData", partition_data),
                           ("dataOverwriteBehavior", overwrite_behavior), ("exportDescription", description)):
            if value is not None:
                props[key] = value
        body: dict[str, Any] = {"properties": props}
        if etag:
            body["eTag"] = etag
        return dict(self._arm("PUT", f"{self._exports(scope)}/{self._seg(name)}", self._v.cost_management,
                              body=body).body or {})

    def run_export_and_wait(
        self, name: str, *, scope: str | None = None, timeout: float = 600.0, poll_seconds: float = 10.0
    ) -> dict[str, Any]:
        """Start an on-demand run, wait until it finishes, and return the newest run record.

        The record carries the service's own fields (``status``, ``fileName``, ``manifestFile``, ``error`` ...).
        Reading the exported files needs access to the storage account, which this package doesn't provide.
        Raises :class:`~foundry_finops.errors.ServiceError` on timeout and ``HttpResponseError`` if the run failed.
        """
        import time

        from ..errors import HttpResponseError, ServiceError

        started = datetime.now(timezone.utc)
        self.run_export(name, scope=scope)
        deadline = time.monotonic() + timeout
        while True:
            runs = self.export_history(name, scope=scope)
            props = [(r.get("properties") or {}) for r in runs]
            fresh = [p for p in props if _after(p.get("submittedTime"), started)]
            if fresh:
                newest = max(fresh, key=lambda p: str(p.get("submittedTime") or ""))
                status = str(newest.get("status") or "")
                if status == "Completed":
                    return newest
                if status in ("Failed", "Timeout", "DataNotAvailable", "NewDataNotAvailable"):
                    raise HttpResponseError(
                        f"Export run ended with status {status}: {(newest.get('error') or {}).get('message', '')}",
                        status_code=200, body=newest,
                    )
            if time.monotonic() >= deadline:
                raise ServiceError(f"Timed out waiting for export {name!r}", status_code=0)
            self._t.sleep(poll_seconds)

    def run_export(self, name: str, *, scope: str | None = None) -> None:
        self._arm("POST", f"{self._exports(scope)}/{self._seg(name)}/run", self._v.cost_management)

    def export_history(self, name: str, *, scope: str | None = None) -> list[dict[str, Any]]:
        url = f"{self._exports(scope)}/{self._seg(name)}/runHistory"
        return list((self._arm("GET", url, self._v.cost_management).body or {}).get("value") or [])

    def delete_export(self, name: str, *, scope: str | None = None) -> None:
        self._arm("DELETE", f"{self._exports(scope)}/{self._seg(name)}", self._v.cost_management)

    # --------------------------------------------------- cost allocation rules
    def _allocation_rules(self, billing_account_id: str) -> str:
        account = self._seg(billing_account_id, "billing_account_id")
        return (f"/providers/Microsoft.Billing/billingAccounts/{account}"
                "/providers/Microsoft.CostManagement/costAllocationRules")

    def list_cost_allocation_rules(self, billing_account_id: str, *, api_version: str | None = None) -> list[dict[str, Any]]:
        """The allocation rules of a billing account. Needs an agreement type that supports them (see ``allocation_rules``)."""
        from ..allocation_rules import ALLOCATION_RULES_API_VERSION

        url = self._allocation_rules(billing_account_id)
        return list(self._list(url, api_version or ALLOCATION_RULES_API_VERSION))

    def put_cost_allocation_rule(self, billing_account_id: str, name: str, rule: Mapping[str, Any], *,
                                 api_version: str | None = None) -> dict[str, Any]:
        """Create or replace an allocation rule. Build ``rule`` with :func:`foundry_finops.allocation_rules.cost_allocation_rule`.

        A rule built there is ``NotActive`` unless you ask otherwise, so it changes no cost until activated. This is a
        billing-account write: the caller needs the right on the account, and Azure applies active rules to cost.
        """
        from ..allocation_rules import ALLOCATION_RULES_API_VERSION, check_rule_name

        if "properties" not in rule:
            raise ValueError("rule must be the request body, with a 'properties' key")
        url = f"{self._allocation_rules(billing_account_id)}/{self._seg(check_rule_name(name), 'rule name')}"
        return dict(self._arm("PUT", url, api_version or ALLOCATION_RULES_API_VERSION, body=dict(rule)).body or {})

    def delete_cost_allocation_rule(self, billing_account_id: str, name: str, *, api_version: str | None = None) -> None:
        from ..allocation_rules import ALLOCATION_RULES_API_VERSION, check_rule_name

        url = f"{self._allocation_rules(billing_account_id)}/{self._seg(check_rule_name(name), 'rule name')}"
        self._arm("DELETE", url, api_version or ALLOCATION_RULES_API_VERSION)

    # --------------------------------------------------------- tag inheritance
    def set_tag_inheritance(self, enabled: bool = True, *, prefer_container_tags: bool = False,
                            scope: str | None = None, api_version: str | None = None) -> dict[str, Any]:
        """Apply subscription/resource-group tags to usage records (needed for team tags on RG/subscription).

        Only supported at EA enrollment/department, MCA billing profile and subscription scopes.
        """
        api_version = api_version or self._v.cost_management  # Settings is part of the stable API now
        url = f"{self._scope(scope)}/providers/Microsoft.CostManagement/settings/taginheritance"
        if not enabled:
            self._arm("DELETE", url, api_version)
            return {}
        body = {"kind": "taginheritance", "properties": {"preferContainerTags": prefer_container_tags}}
        return dict(self._arm("PUT", url, api_version, body=body).body or {})


def _after(timestamp: Any, moment: datetime) -> bool:
    """``True`` if an ISO timestamp from the service is at or after ``moment`` (a run started by this call)."""
    if not timestamp:
        return False
    try:
        when = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when >= moment - timedelta(seconds=5)  # allow for clock differences with the service


def _normalise_dates(table: Table) -> Table:
    for idx, col in enumerate(table.columns):
        if col.name in ("UsageDate", "BillingMonth") or col.name.lower() == "usagedate":
            for row in table.rows:
                v = row[idx]
                if isinstance(v, (int, float)) and v > 19000000:
                    s = str(int(v))
                    row[idx] = f"{s[:4]}-{s[4:6]}-{s[6:8]}"
                elif isinstance(v, str) and "T" in v:
                    row[idx] = v.split("T")[0]
            col.type = "datetime"
    return table
