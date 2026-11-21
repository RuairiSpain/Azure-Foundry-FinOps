"""Features built from the existing Azure APIs: nothing estimated, nothing invented."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from foundry_finops import (
    BudgetNotification,
    CostControl,
    CostQuery,
    ForbiddenError,
    PriceSheet,
    ServiceError,
)
from foundry_finops.analysis import allocate_cost, detect_anomalies, ptu_headroom
from foundry_finops.cli import main
from foundry_finops.errors import HttpResponseError
from foundry_finops.models import Budget, Deployment, TimeGrain
from foundry_finops.operations.monitor import FoundryMetrics, project_matches
from foundry_finops.operations.pricing import (
    EffectivePrices,
    classify_meter,
    effective_discounts,
    unit_multiplier,
)

from .fakes import FakeSession, make_client

ACCT = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct"


def metric(name: str, series: list[tuple[dict, list]]) -> dict:
    """Azure Monitor metrics response: series = [(dimensions, [(timestamp, value), ...]), ...]."""
    return {"value": [{"name": {"value": name}, "unit": "Count", "timeseries": [
        {"metadatavalues": [{"name": {"value": k}, "value": v} for k, v in dims.items()],
         "data": [{"timeStamp": t, "total": v, "average": v, "maximum": v} for t, v in pts]}
        for dims, pts in series]}]}


class CostQueryTests(unittest.TestCase):
    def test_named_time_frames(self):
        for ctor, frame in ((CostQuery.week_to_date, "WeekToDate"), (CostQuery.last_month, "TheLastMonth"),
                            (CostQuery.billing_month_to_date, "BillingMonthToDate"),
                            (CostQuery.last_billing_month, "TheLastBillingMonth"),
                            (CostQuery.current_month, "TheCurrentMonth")):
            self.assertEqual(ctor().to_dict()["timeframe"], frame)
        with self.assertRaises(ValueError):
            CostQuery.for_timeframe("Yesterday")

    def test_or_not_and_quantity(self):
        q = (CostQuery.month_to_date().where_any(("tag", "team", ["risk"]), ("dimension", "ResourceGroupName", ["rg"]))
             .where_not("tag", "env", ["dev"]).quantity())
        d = q.to_dict()
        self.assertEqual(d["dataset"]["filter"]["and"][0]["or"][1], {"dimensions": {
            "name": "ResourceGroupName", "operator": "In", "values": ["rg"]}})
        self.assertIn("not", d["dataset"]["filter"]["and"][1])
        self.assertEqual(d["dataset"]["aggregation"]["totalQuantity"], {"name": "UsageQuantity", "function": "Sum"})
        with self.assertRaises(ValueError):
            CostQuery().where_any(("tag", "a", ["x"]))

    def test_more_than_two_group_bys_is_refused(self):
        with self.assertRaisesRegex(ValueError, "at most 2"):
            CostQuery().group_by("A").group_by("B").group_by_tag("c").to_dict()

    def test_save_load_round_trip(self):
        q = (CostQuery.between("2026-01-01", "2026-01-31").total().quantity().group_by("Meter")
             .where_tag("project", ["risk"]))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "q.json")
            q.save(path)
            self.assertEqual(CostQuery.load(path).to_dict(), q.to_dict())
            Path(path).write_text("[1]")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                CostQuery.load(path)
            Path(path).write_text("{nope")
            with self.assertRaisesRegex(ValueError, "isn't valid JSON"):
                CostQuery.load(path)


class DeploymentTests(unittest.TestCase):
    def test_provisioned_capacity_is_never_turned_into_tokens_per_minute(self):
        d = Deployment.from_dict({"name": "d", "sku": {"name": "GlobalProvisionedManaged", "capacity": 100},
                                  "properties": {"serviceTier": "Priority", "deploymentState": "Paused",
                                                 "spilloverDeploymentName": "backup", "model": {"name": "m"}}})
        self.assertTrue(d.is_provisioned)
        self.assertIsNone(d.tokens_per_minute)
        self.assertEqual((d.service_tier, d.deployment_state, d.spillover_deployment), ("Priority", "Paused", "backup"))
        std = Deployment.from_dict({"name": "s", "sku": {"name": "GlobalStandard", "capacity": 50}})
        self.assertEqual(std.tokens_per_minute, 50_000)
        self.assertIsNone(Deployment.from_dict({"name": "x"}).tokens_per_minute)


class CostControlTests(unittest.TestCase):
    def setUp(self):
        self.s = FakeSession()
        self.fo = make_client(self.s)
        self.body = {"name": "cap", "etag": '"e1"', "properties": {"category": "Cost", "amount": 5, "timeGrain": "Daily"}}

    def test_etag_is_read_and_if_match_is_sent(self):
        self.s.add("GET", "/costControls/cap", 200, self.body)
        self.assertEqual(self.fo.cost_controls.get("acct", "cap").etag, '"e1"')
        self.s.add("PATCH", "/costControls/cap", 200, self.body)
        self.fo.cost_controls.update("acct", "cap", amount=9, if_match='"e1"')
        self.assertEqual(self.s.calls[-1]["headers"]["If-Match"], '"e1"')
        self.s.add("PUT", "/costControls/cap", 200, self.body)
        self.fo.cost_controls.create_or_update("acct", CostControl(name="cap", amount=5), if_match='"e1"')
        self.assertEqual(self.s.calls[-1]["headers"]["If-Match"], '"e1"')
        self.s.add("DELETE", "/costControls/cap", 200, None)
        self.fo.cost_controls.delete("acct", "cap", if_match='"e1"')
        self.assertEqual(self.s.calls[-1]["headers"]["If-Match"], '"e1"')
        with self.assertRaises(ValueError):
            self.fo.cost_controls.create_or_update("acct", CostControl(name="cap", amount=5), only_if_new=True,
                                                   if_match='"e1"')

    def test_feature_not_registered_is_explained(self):
        self.s.add("PUT", "/costControls/cap", 403, {"error": {"code": "FeatureNotRegistered", "message": "not registered"}})
        with self.assertRaisesRegex(ForbiddenError, "CostControlsByIdentity"):
            self.fo.cost_controls.create_or_update("acct", CostControl(name="cap", amount=5))

    def test_names_in_filters_are_plain_names(self):
        for bad in ("", " p", "a/b", "../x", "a%20b", ".", "x" * 65, "a?b"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                CostControl(name="c", amount=5, filters={"project": [bad]}).to_dict()
        CostControl(name="c", amount=5, filters={"project": ["fraud-risk"], "deployment": ["gpt-5.4"]}).to_dict()


class BudgetStatusTests(unittest.TestCase):
    def test_status_uses_only_what_the_service_reported(self):
        b = Budget(name="b", amount=100, time_grain=TimeGrain.MONTHLY, current_spend=85.0, forecast_spend=120.0,
                   notifications=[BudgetNotification(80, ["a@b.c"]),
                                  BudgetNotification(100, ["a@b.c"], threshold_type="Forecasted"),
                                  BudgetNotification(90, ["a@b.c"])])
        status = {n["notification"]: n["reached"] for n in b.notification_status()}
        self.assertEqual(list(status.values()), [True, True, False])
        none = Budget(name="b", amount=100, notifications=[BudgetNotification(80, ["a@b.c"])])
        self.assertIsNone(none.percent_used())
        self.assertIsNone(none.notification_status()[0]["reached"])  # unknown, not "0 percent"


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.s = FakeSession()
        self.fo = make_client(self.s)

    def test_names_come_from_the_metric_reference(self):
        self.assertEqual(FoundryMetrics.ESTIMATED_COST, "FoundryModelEstimatedCost")
        self.assertEqual(FoundryMetrics.PROVISIONED_UTILIZATION_V2, "AzureOpenAIProvisionedManagedUtilizationV2")
        self.assertEqual(FoundryMetrics.PROJECT_DIMENSION, "ProjectId")

    def test_estimated_cost_rows_and_totals_skip_missing_values(self):
        self.s.add("GET", "/providers/Microsoft.Insights/metrics", 200, metric("FoundryModelEstimatedCost", [
            ({"projectid": "risk"}, [("t1", 1.5), ("t2", 2.0), ("t3", None)]),
            ({"projectid": "care"}, [("t1", 0.5)])]))
        rows = self.fo.metrics.estimated_cost(ACCT, hours=3)
        self.assertEqual(len(rows), 4)
        call = self.s.calls[-1]
        self.assertEqual(call["params"]["metricnames"], "FoundryModelEstimatedCost")
        self.assertEqual(call["params"]["$filter"], "ProjectId eq '*'")
        totals = self.fo.metrics.estimated_cost_totals(ACCT, hours=3)
        self.assertEqual(totals, [{"projectid": "risk", "estimated_cost_usd": 3.5},
                                  {"projectid": "care", "estimated_cost_usd": 0.5}])

    def test_project_matching_is_strict(self):
        self.assertTrue(project_matches("Fraud-Risk", "fraud-risk"))
        self.assertTrue(project_matches(ACCT + "/projects/fraud-risk", "fraud-risk"))
        self.assertFalse(project_matches("fraud-risk-2", "fraud-risk"))
        self.assertFalse(project_matches("", ""))


class PricingTests(unittest.TestCase):
    def test_units_and_meter_names(self):
        self.assertEqual((unit_multiplier("1K"), unit_multiplier("1M"), unit_multiplier("1 Hour")), (1000.0, 1.0, None))
        self.assertEqual(classify_meter("gpt-5.4 mini inp glbl"), ("gpt-5.4 mini", "input"))
        self.assertEqual(classify_meter("gpt-5.4 mini cached inp glbl")[1], "cached_input")
        self.assertIsNone(classify_meter("gpt-5.4 mini inp batch"))
        self.assertIsNone(classify_meter("Dall-E images"))

    def test_discount_is_applied_as_given_and_recorded(self):
        sheet = PriceSheet.from_mapping({"m": {"input": 2.0, "output": 8.0, "cached_input": 1.0}})
        d = sheet.scaled(25)
        self.assertEqual((d.prices["m"].input, d.prices["m"].output, d.prices["m"].cached_input), (1.5, 6.0, 0.75))
        self.assertEqual(d.discount_percent, 25)
        self.assertEqual(sheet.prices["m"].input, 2.0)  # the original is untouched
        self.assertAlmostEqual(d.scaled(20).discount_percent, 40.0)  # successive discounts compound
        for bad in (-1, 100, 150):
            with self.assertRaises(ValueError):
                sheet.scaled(bad)
        self.assertEqual(d.estimate("m", 1_000_000, 1_000_000), 7.5)

    def test_retail_sheet_with_discount(self):
        s = FakeSession()
        fo = make_client(s)
        items = [{"meterName": "gpt-x inp glbl", "unitOfMeasure": "1K", "retailPrice": 0.001, "type": "Consumption"},
                 {"meterName": "gpt-x outp glbl", "unitOfMeasure": "1K", "retailPrice": 0.004, "type": "Consumption"}]
        s.add("GET", "prices.azure.com", 200, {"Items": items})
        retail = fo.pricing.openai_price_sheet("eastus2")
        self.assertEqual((retail.source, retail.discount_percent), ("retail", 0.0))
        self.assertAlmostEqual(retail.prices["gpt-x"].input, 1.0)
        off = fo.pricing.openai_price_sheet("eastus2", discount_percent=10)
        self.assertAlmostEqual(off.prices["gpt-x"].output, 3.6)
        self.assertEqual((off.source, off.discount_percent), ("retail", 10))

    def test_effective_prices_come_from_cost_and_quantity_only(self):
        rows = [
            {"Meter": "gpt-x inp glbl", "UnitOfMeasure": "1K", "Cost": 8.0, "UsageQuantity": 20000.0, "Currency": "USD"},
            {"Meter": "gpt-x outp glbl", "UnitOfMeasure": "1K", "Cost": 6.0, "UsageQuantity": 2000.0, "Currency": "USD"},
            {"Meter": "images", "UnitOfMeasure": "100", "Cost": 5.0, "UsageQuantity": 10.0, "Currency": "USD"},
            {"Meter": "idle inp glbl", "UnitOfMeasure": "1K", "Cost": 0.0, "UsageQuantity": 0.0, "Currency": "USD"},
            {"Meter": "no-qty", "UnitOfMeasure": "1K", "Cost": 1.0, "UsageQuantity": None, "Currency": "USD"},
        ]
        e = EffectivePrices.from_records(rows)
        by = {r.meter: r for r in e.rows}
        self.assertAlmostEqual(by["gpt-x inp glbl"].unit_price, 0.0004)
        self.assertAlmostEqual(by["gpt-x inp glbl"].price_per_1m_tokens, 0.4)
        self.assertAlmostEqual(by["gpt-x outp glbl"].price_per_1m_tokens, 3.0)
        self.assertIsNone(by["images"].price_per_1m_tokens)  # not a token unit: no per-token price is made up
        self.assertEqual({s["meter"] for s in e.skipped}, {"idle inp glbl", "no-qty"})
        sheet = e.to_price_sheet()
        self.assertEqual(sheet.source, "billed")
        self.assertEqual(list(sheet.prices), ["gpt-x"])
        retail = PriceSheet.from_mapping({"gpt-x": {"input": 0.5, "output": 4.0}})
        disc = {(d["kind"]): d["discount_percent"] for d in effective_discounts(sheet, retail)}
        self.assertEqual(disc, {"input": 20.0, "output": 25.0})
        with self.assertRaises(ValueError):
            effective_discounts(sheet, PriceSheet({}, "EUR"))

    def test_mixed_currencies_are_refused(self):
        e = EffectivePrices.from_records([
            {"Meter": "a inp glbl", "UnitOfMeasure": "1K", "Cost": 1, "UsageQuantity": 1, "Currency": "USD"},
            {"Meter": "a outp glbl", "UnitOfMeasure": "1K", "Cost": 1, "UsageQuantity": 1, "Currency": "EUR"}])
        with self.assertRaisesRegex(ValueError, "several currencies"):
            e.to_price_sheet()

    def test_cost_effective_prices_query_and_dimension_check(self):
        s = FakeSession()
        fo = make_client(s)
        s.add("GET", "/providers/Microsoft.CostManagement/dimensions", 200,
              {"value": [{"name": "Meter"}, {"name": "UnitOfMeasure"}]})
        s.add("POST", "/providers/Microsoft.CostManagement/query", 200, {"properties": {
            "columns": [{"name": "Cost"}, {"name": "UsageQuantity"}, {"name": "Meter"}, {"name": "UnitOfMeasure"},
                        {"name": "Currency"}],
            "rows": [[4.0, 10000.0, "gpt-x inp glbl", "1K", "USD"]]}})
        out = fo.cost.effective_prices(scope=ACCT, days=7)
        self.assertAlmostEqual(out.rows[0].price_per_1m_tokens, 0.4)
        body = next(c for c in s.calls if c["method"] == "POST")["json"]
        self.assertEqual([g["name"] for g in body["dataset"]["grouping"]], ["Meter", "UnitOfMeasure"])
        self.assertIn("totalQuantity", body["dataset"]["aggregation"])
        s.add("GET", "/providers/Microsoft.CostManagement/dimensions", 200, {"value": [{"name": "Meter"}]})
        with self.assertRaisesRegex(ValueError, "UnitOfMeasure"):
            fo.cost.effective_prices(scope=ACCT, days=7)


class ExportApiTests(unittest.TestCase):
    STORAGE = "/subscriptions/s/resourceGroups/r/providers/Microsoft.Storage/storageAccounts/sa"

    def setUp(self):
        self.s = FakeSession()
        self.fo = make_client(self.s)
        self.fo._transport.sleep = lambda _s: None

    def test_all_documented_options_are_sent_and_unset_ones_are_not(self):
        self.s.add("PUT", "/exports/focus", 200, {"name": "focus"})
        self.fo.cost.create_or_update_export(
            "focus", storage_account_id=self.STORAGE, container="c", cost_type="FocusCost", file_format="Parquet",
            compression="snappy", partition_data=True, overwrite_behavior="CreateNewReport", description="FOCUS",
            columns=["Cost"], data_version="1.0", granularity="Monthly", recurrence="Monthly", etag='"x"',
            timeframe="Custom", data_from="2026-01-01", data_to="2026-01-31")
        body = self.s.calls[-1]["json"]
        p = body["properties"]
        self.assertEqual((p["format"], p["compressionMode"], p["partitionData"], p["dataOverwriteBehavior"],
                          p["exportDescription"]), ("Parquet", "snappy", True, "CreateNewReport", "FOCUS"))
        self.assertEqual(p["definition"]["type"], "FocusCost")
        self.assertEqual(p["definition"]["dataSet"], {"granularity": "Monthly", "configuration": {
            "columns": ["Cost"], "dataVersion": "1.0"}})
        self.assertEqual(p["definition"]["timePeriod"], {"from": "2026-01-01T00:00:00Z", "to": "2026-01-31T00:00:00Z"})
        self.assertEqual(body["eTag"], '"x"')
        self.s.add("PUT", "/exports/plain", 200, {})
        self.fo.cost.create_or_update_export("plain", storage_account_id=self.STORAGE, container="c")
        plain = self.s.calls[-1]["json"]["properties"]
        for key in ("compressionMode", "partitionData", "dataOverwriteBehavior", "exportDescription"):
            self.assertNotIn(key, plain)
        self.assertNotIn("configuration", plain["definition"]["dataSet"])

    def test_invalid_values_are_refused_before_any_call(self):
        bad = [{"cost_type": "Everything"}, {"file_format": "Json"}, {"compression": "zip"}, {"recurrence": "Hourly"},
               {"overwrite_behavior": "Maybe"}, {"granularity": "Hourly"}, {"timeframe": "Custom"},
               {"timeframe": "Yesterday"}]
        for kw in bad:
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.fo.cost.create_or_update_export("x", storage_account_id=self.STORAGE, container="c", **kw)
        self.assertEqual(self.s.calls, [])

    def test_get_export(self):
        self.s.add("GET", "/exports/e1", 200, {"name": "e1", "eTag": '"a"'})
        self.assertEqual(self.fo.cost.get_export("e1")["eTag"], '"a"')

    def test_run_and_wait_returns_the_finished_run(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.s.add("POST", "/exports/e1/run", 200, None)
        self.s.add("GET", "/exports/e1/runHistory", 200, {"value": [{"properties": {
            "status": "Completed", "submittedTime": now, "fileName": "f.csv", "manifestFile": "m.json"}}]})
        self.s.add("GET", "/exports/e1/runHistory", 200, {"value": [{"properties": {
            "status": "InProgress", "submittedTime": now}}]}, times=1)
        run = self.fo.cost.run_export_and_wait("e1")
        self.assertEqual((run["status"], run["manifestFile"]), ("Completed", "m.json"))

    def test_run_and_wait_reports_failure_and_timeout(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.s.add("POST", "/exports/e1/run", 200, None)
        self.s.add("GET", "/exports/e1/runHistory", 200, {"value": [{"properties": {
            "status": "Failed", "submittedTime": now, "error": {"message": "no storage"}}}]})
        with self.assertRaisesRegex(HttpResponseError, "no storage"):
            self.fo.cost.run_export_and_wait("e1")
        self.s.add("GET", "/exports/e1/runHistory", 200, {"value": []})
        with self.assertRaises(ServiceError):
            self.fo.cost.run_export_and_wait("e1", timeout=0)


class AnalysisTests(unittest.TestCase):
    @staticmethod
    def series(values, group="p", start=date(2026, 1, 1)):
        return [{"UsageDate": (start + timedelta(days=i)).isoformat(), "G": group, "Cost": v}
                for i, v in enumerate(values)]

    def test_a_spike_is_flagged_with_its_evidence(self):
        rows = self.series([10, 11, 9, 10, 12, 10, 11, 10, 11, 50, 10])
        found = detect_anomalies(rows, group_key="G")
        self.assertEqual([a.date for a in found], ["2026-01-10"])
        a = found[0]
        self.assertEqual((a.cost, a.baseline, a.direction, a.group), (50.0, 10.0, "up", "p"))
        self.assertEqual(a.percent_change, 400.0)
        self.assertGreater(a.score, 3.5)

    def test_steady_data_and_small_changes_are_not_flagged(self):
        self.assertEqual(detect_anomalies(self.series([10, 11, 9, 10, 12, 10, 11, 10, 11, 12, 10]), group_key="G"), [])
        rows = self.series([10] * 9 + [10.4])
        self.assertEqual(len(detect_anomalies(rows, group_key="G")), 1)  # flat history: any rise counts...
        self.assertEqual(detect_anomalies(rows, group_key="G", min_change=1.0), [])  # ...unless you set a floor

    def test_direction_and_missing_days(self):
        drop = self.series([10, 11, 9, 10, 12, 10, 11, 10, 11, 1, 10])
        self.assertEqual(detect_anomalies(drop, group_key="G"), [])
        self.assertEqual(detect_anomalies(drop, group_key="G", direction="down")[0].direction, "down")
        gap = [r for r in self.series([10, 11, 9, 10, 12, 10, 11, 10, 11, 10]) if r["UsageDate"] != "2026-01-09"]
        both = detect_anomalies(gap, group_key="G", direction="both")
        self.assertTrue(any(a.date == "2026-01-09" and a.cost == 0.0 for a in both))  # absent day = no charge = 0

    def test_groups_are_independent_and_short_history_is_skipped(self):
        rows = self.series([10, 11, 9, 10, 12, 10, 11, 10, 11, 50], "a") + self.series([5, 5, 5], "b")
        self.assertEqual({a.group for a in detect_anomalies(rows, group_key="G")}, {"a"})

    def test_bad_arguments(self):
        for kw in ({"direction": "sideways"}, {"window": 3, "min_history": 7}, {"min_history": 2}):
            with self.assertRaises(ValueError):
                detect_anomalies([], **kw)

    def test_allocation_adds_up_exactly(self):
        out = allocate_cost(100.0, {"a": 1, "b": 1, "c": 1})
        self.assertEqual(sum(r["allocated"] for r in out), 100.0)
        self.assertEqual(sorted(r["allocated"] for r in out), [33.33, 33.33, 33.34])
        out = allocate_cost(10, {"x": 3, "y": 1, "z": 0})
        self.assertEqual({r["key"]: r["allocated"] for r in out}, {"x": 7.5, "y": 2.5, "z": 0.0})
        self.assertEqual(out[0]["share_percent"], 75.0)
        for bad in ({}, {"a": -1}, {"a": 0}):
            with self.assertRaises(ValueError):
                allocate_cost(10, bad)

    def test_ptu_headroom_joins_deployments_quota_and_utilisation(self):
        s = FakeSession()
        fo = make_client(s)
        s.add("GET", "/accounts/acct", 200, {"name": "acct", "location": "swedencentral"})
        s.add("GET", "/deployments", 200, {"value": [
            {"name": "ptu-a", "sku": {"name": "GlobalProvisionedManaged", "capacity": 100},
             "properties": {"model": {"name": "gpt-x"}, "serviceTier": "Default"}},
            {"name": "std", "sku": {"name": "GlobalStandard", "capacity": 10}, "properties": {"model": {"name": "m"}}}]})
        s.add("GET", "/locations/swedencentral/usages", 200, {"value": [
            {"name": {"value": "OpenAI.GlobalProvisionedManaged.gpt-x", "localizedValue": "PTU gpt-x"},
             "currentValue": 100, "limit": 300, "unit": "Count"}]})
        s.add("GET", "/providers/Microsoft.Insights/metrics", 200, metric(
            "AzureOpenAIProvisionedManagedUtilizationV2", [({"modeldeploymentname": "ptu-a"},
                                                            [("t1", 60.0), ("t2", 100.0)])]))
        rows = ptu_headroom(fo, "acct", hours=6)
        self.assertEqual([r["deployment"] for r in rows], ["ptu-a"])  # the Standard deployment isn't PTU
        r = rows[0]
        self.assertEqual((r["capacity_ptu"], r["utilization_avg_percent"], r["utilization_max_percent"],
                          r["at_or_over_100_percent"]), (100, 80.0, 100.0, True))
        self.assertEqual((r["quota_used"], r["quota_limit"], r["quota_available"]), (100.0, 300.0, 200.0))

    def test_ptu_headroom_leaves_unknowns_empty(self):
        s = FakeSession()
        fo = make_client(s)
        s.add("GET", "/accounts/acct", 200, {"name": "acct", "location": "eastus2"})
        s.add("GET", "/deployments", 200, {"value": [{"name": "p", "sku": {"name": "ProvisionedManaged", "capacity": 50},
                                                      "properties": {"model": {"name": "m"}}}]})
        s.add("GET", "/usages", 200, {"value": []})
        s.add("GET", "/providers/Microsoft.Insights/metrics", 200, {"value": []})
        r = ptu_headroom(fo, "acct")[0]
        self.assertIsNone(r["utilization_max_percent"])
        self.assertIsNone(r["at_or_over_100_percent"])
        self.assertIsNone(r["quota_limit"])


class UsageFromEstimatedMetricTests(unittest.TestCase):
    CC = {"id": ACCT + "/costControls/cap", "name": "cap", "properties": {
        "category": "Cost", "amount": 10, "timeGrain": "Monthly",
        "filter": {"additionalInfo": {"name": "project", "operator": "In", "values": ["risk"]}}}}

    def service(self, metric_body):
        from foundry_finops.service import LiveService, Settings

        s = FakeSession()
        s.add("GET", "/costControls", 200, {"value": [self.CC]})
        s.add("GET", "/projects", 200, {"value": []})
        s.add("GET", "/providers/Microsoft.Insights/metrics", 200, metric_body)
        s.add("POST", "/providers/Microsoft.CostManagement/query", 200, {"properties": {
            "columns": [{"name": "Cost"}], "rows": [[7.0]]}})
        return LiveService(Settings(subscription_id="sub-1", resource_group="rg", foundry_account="acct"),
                           make_client(s)), s

    def test_matching_project_uses_the_near_real_time_metric(self):
        svc, s = self.service(metric("FoundryModelEstimatedCost", [
            ({"projectid": "risk", "modeldeploymentname": "g"}, [("t", 4.0)]),
            ({"projectid": "care", "modeldeploymentname": "g"}, [("t", 99.0)])]))
        [row] = svc.list_cost_controls()
        self.assertEqual(row["usage"], 4.0)
        self.assertIn("FoundryModelEstimatedCost", row["usage_note"])
        self.assertFalse([c for c in s.calls if "CostManagement" in c["url"]])

    def test_unmatched_project_falls_back_to_billed_cost_instead_of_guessing(self):
        svc, s = self.service(metric("FoundryModelEstimatedCost", [({"projectid": "other-id", "modeldeploymentname": "g"},
                                                                    [("t", 4.0)])]))
        [row] = svc.list_cost_controls()
        self.assertEqual(row["usage"], 7.0)
        self.assertTrue([c for c in s.calls if "CostManagement" in c["url"]])

    def test_a_failing_metric_falls_back_too(self):
        svc, s = self.service({"value": []})
        s.add("GET", "/providers/Microsoft.Insights/metrics", 400, {"error": {"code": "BadRequest", "message": "x"}})
        [row] = svc.list_cost_controls()
        self.assertEqual(row["usage"], 7.0)


class CliTests(unittest.TestCase):
    def run_cli(self, argv, session):
        out, err = io.StringIO(), io.StringIO()
        client = make_client(session)
        with mock.patch("foundry_finops.cli_reports.FinOpsClient", return_value=client), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = main(argv)
            except SystemExit as e:
                rc = e.code
        return rc, out.getvalue(), err.getvalue()

    def test_retail_prices_with_a_discount_need_no_subscription(self):
        s = FakeSession()
        s.add("GET", "prices.azure.com", 200, {"Items": [
            {"meterName": "gpt-x inp glbl", "unitOfMeasure": "1K", "retailPrice": 0.001, "type": "Consumption"},
            {"meterName": "gpt-x outp glbl", "unitOfMeasure": "1K", "retailPrice": 0.004, "type": "Consumption"}]})
        rc, out, err = self.run_cli(["--format", "json", "prices", "--discount-percent", "10"], s)
        self.assertEqual(rc, 0, err)
        row = json.loads(out)[0]
        self.assertAlmostEqual(row["output"], 3.6)
        self.assertEqual((row["source"], row["discount_percent"]), ("retail", 10))
        self.assertIn("reduced by 10% as you asked", err)

    def test_discount_is_refused_for_billed_prices_and_out_of_range(self):
        s = FakeSession()
        for argv in (["--subscription", "s", "prices", "--source", "billed", "--discount-percent", "5"],
                     ["prices", "--discount-percent", "100"], ["prices", "--compare"]):
            rc, _, _ = self.run_cli(argv, s)
            self.assertEqual(rc, 2, argv)

    def test_estimated_cost_command(self):
        s = FakeSession()
        s.add("GET", "/providers/Microsoft.Insights/metrics", 200, metric("FoundryModelEstimatedCost", [
            ({"projectid": "risk"}, [("t1", 2.5)])]))
        rc, out, err = self.run_cli(["--subscription", "sub-1", "-g", "rg", "--format", "json", "estimated-cost", "acct",
                                     "--hours", "6"], s)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out), [{"projectid": "risk", "estimated_cost_usd": 2.5}])
        self.assertIn("Not billed cost", err)

    def test_allocate_with_a_given_total(self):
        s = FakeSession()
        s.add("GET", "/providers/Microsoft.Insights/metrics", 200, metric("TokenTransaction", [
            ({"modeldeploymentname": "a"}, [("t", 300.0)]), ({"modeldeploymentname": "b"}, [("t", 100.0)])]))
        rc, out, err = self.run_cli(["--subscription", "sub-1", "-g", "rg", "--format", "json", "allocate", "acct",
                                     "--total", "200"], s)
        self.assertEqual(rc, 0, err)
        self.assertEqual({r["key"]: r["allocated"] for r in json.loads(out)}, {"a": 150.0, "b": 50.0})
        self.assertIn("not billed cost", err)

    def test_saved_query_command_and_demo_refusal(self):
        s = FakeSession()
        s.add("POST", "/providers/Microsoft.CostManagement/query", 200, {"properties": {
            "columns": [{"name": "Cost"}], "rows": [[1.0]]}})
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "q.json")
            CostQuery.month_to_date().total().save(path)
            rc, out, _ = self.run_cli(["--subscription", "sub-1", "-g", "rg", "--format", "json", "query", path], s)
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), [{"Cost": 1.0}])
        for cmd in (["anomalies"], ["ptu", "a"], ["estimated-cost", "a"], ["query", "f"]):
            rc, _, _ = self.run_cli(["--demo", *cmd], s)
            self.assertEqual(rc, 2, cmd)

    def test_anomalies_command(self):
        s = FakeSession()
        rows = [[v, 20260101 + i, "risk", "USD"] for i, v in enumerate([10, 11, 9, 10, 12, 10, 11, 10, 11, 60])]
        s.add("POST", "/providers/Microsoft.CostManagement/query", 200, {"properties": {
            "columns": [{"name": "Cost"}, {"name": "UsageDate"}, {"name": "TagValue"}, {"name": "Currency"}],
            "rows": rows}})
        rc, out, err = self.run_cli(["--subscription", "sub-1", "-g", "rg", "--format", "json", "anomalies",
                                     "--account", "acct", "--by", "project"], s)
        self.assertEqual(rc, 0, err)
        flagged = json.loads(out)
        self.assertEqual([(a["date"], a["group"]) for a in flagged], [("2026-01-10", "risk")])
        self.assertIn("1 day(s) flagged", err)

    def test_download_discount_is_validated(self):
        rc, _, _ = self.run_cli(["--subscription", "s", "-g", "rg", "download", "--account", "a", "--start", "2026-01-01",
                                 "--end", "2026-01-02", "--discount-percent", "120"], FakeSession())
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
