"""Idle capacity, reconciliation against Azure Monitor, and cost allocation rule definitions."""

from __future__ import annotations

import unittest

from foundry_finops.allocation_rules import (
    MAX_VALUES,
    cost_allocation_rule,
    rule_name,
    split_percentages,
)
from foundry_finops.analysis import IDLE, UNATTRIBUTED, allocate_by_usage, reconcile_usage

from .fakes import FakeSession, make_client

CENTRES = {"a": "CC-FIN", "b": "CC-HR"}


def calls(*rows):
    return [{"caller_oid": oid, "cost": cost} for oid, cost in rows]


class IdleCapacityTests(unittest.TestCase):
    def split(self, total, rows, **kw):
        return allocate_by_usage(total, calls(*rows), CENTRES.get, **kw)

    def test_spread_is_the_default_and_reports_the_idle_part(self):
        out = self.split(100, [("a", 30), ("b", 10)])
        self.assertEqual({r["key"]: r["allocated"] for r in out["rows"]}, {"CC-FIN": 75.0, "CC-HR": 25.0})
        self.assertEqual(out["idle_cost"], 60.0)        # information only: the fixed cost the calls didn't use
        self.assertEqual(out["idle"], "spread")

    def test_hold_charges_cost_centres_only_for_what_their_calls_cost(self):
        out = self.split(100, [("a", 30), ("b", 10)], idle="hold")
        by = {r["key"]: r["allocated"] for r in out["rows"]}
        self.assertEqual(by, {IDLE: 60.0, "CC-FIN": 30.0, "CC-HR": 10.0})
        self.assertEqual(round(sum(by.values()), 2), 100.0)
        self.assertEqual({r["key"]: r["share_percent"] for r in out["rows"]}[IDLE], 60.0)   # a share of the whole total

    def test_no_idle_part_when_the_calls_cost_more_than_the_total(self):
        out = self.split(100, [("a", 90), ("b", 30)], idle="hold")
        self.assertEqual(out["idle_cost"], 0.0)
        self.assertEqual([r["key"] for r in out["rows"]], ["CC-FIN", "CC-HR"])
        self.assertEqual(round(sum(r["allocated"] for r in out["rows"]), 2), 100.0)

    def test_idle_and_unattributed_can_both_be_held(self):
        out = self.split(100, [("a", 20), ("b", 10), ("", 10)], idle="hold", unattributed="hold")
        by = {r["key"]: r["allocated"] for r in out["rows"]}
        self.assertEqual(by, {IDLE: 60.0, "CC-FIN": 20.0, "CC-HR": 10.0, UNATTRIBUTED: 10.0})
        self.assertEqual(round(sum(by.values()), 2), 100.0)

    def test_tokens_can_not_be_compared_with_money(self):
        rows = [{"caller_oid": "a", "tokens": 10}]
        with self.assertRaises(ValueError):
            allocate_by_usage(10, rows, CENTRES.get, measure="tokens", idle="hold")
        out = allocate_by_usage(10, rows, CENTRES.get, measure="tokens")      # spreading is fine, with no idle figure
        self.assertIsNone(out["idle_cost"])
        with self.assertRaises(ValueError):
            self.split(10, [("a", 1)], idle="park")


class ReconcileTests(unittest.TestCase):
    def rows(self, out):
        return {r["name"]: r for r in out["rows"]}

    def test_statuses(self):
        records = [{"deployment": "gpt-5.4", "tokens": 960}, {"deployment": "GPT-5.4-mini", "tokens": 500},
                   {"deployment": "o4-mini", "tokens": 1500}, {"deployment": "gpt-typo", "tokens": 70}]
        monitor = {"gpt-5.4": 1000, "gpt-5.4-mini": 1000, "o4-mini": 1000, "model-router": 400, "idle-one": 0}
        out = reconcile_usage(records, monitor)
        by = self.rows(out)
        self.assertEqual(by["gpt-5.4"]["status"], "ok")                  # 96 %: inside the 5 % tolerance
        self.assertEqual(by["gpt-5.4-mini"]["status"], "under")           # matched ignoring case
        self.assertEqual(by["gpt-5.4-mini"]["coverage_percent"], 50.0)
        self.assertEqual(by["o4-mini"]["status"], "over")
        self.assertEqual(by["model-router"]["status"], "none_recorded")
        self.assertEqual(by["gpt-typo"]["status"], "not_in_monitor")
        self.assertEqual(by["idle-one"]["status"], "ok")
        self.assertEqual(out["not_in_monitor"], ["gpt-typo"])

    def test_totals_use_only_deployments_on_both_sides(self):
        out = reconcile_usage([{"deployment": "a", "tokens": 90}, {"deployment": "typo", "tokens": 500}], {"a": 100})
        self.assertEqual((out["recorded_total"], out["azure_total"], out["coverage_percent"]), (90, 100, 90.0))
        self.assertEqual(out["unrecorded"], 10)                           # the typo can't hide a gap

    def test_records_without_a_deployment_are_named_and_unmatched(self):
        out = reconcile_usage([{"tokens": 5}], {"a": 10})
        self.assertIn("(no deployment recorded)", self.rows(out))
        self.assertEqual(out["coverage_percent"], 0.0)

    def test_a_name_only_your_side_has_with_nothing_recorded_is_not_a_row(self):
        out = reconcile_usage([{"deployment": "ghost", "tokens": 0}, {"deployment": "a", "tokens": 10}], {"a": 10})
        self.assertEqual([r["name"] for r in out["rows"]], ["a"])
        self.assertEqual(out["not_in_monitor"], [])

    def test_nothing_from_azure_gives_no_coverage_figure(self):
        out = reconcile_usage([{"deployment": "a", "tokens": 5}], {})
        self.assertIsNone(out["coverage_percent"])
        self.assertEqual(out["rows"][0]["status"], "not_in_monitor")

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            reconcile_usage([{"deployment": "a", "tokens": -1}], {"a": 1})
        with self.assertRaises(ValueError):
            reconcile_usage([], {}, tolerance_percent=-1)


class AllocationRuleTests(unittest.TestCase):
    def test_percentages_have_two_decimals_and_add_up_to_exactly_100(self):
        got = split_percentages({"a": 1, "b": 1, "c": 1})
        self.assertEqual(sorted(got.values()), [33.33, 33.33, 33.34])
        self.assertEqual(round(sum(got.values()), 2), 100.0)
        got = split_percentages({"a": 3, "b": 1, "dust": 0.0000001})
        self.assertEqual(got, {"a": 75.0, "b": 25.0})                   # a share too small for 0.01 % is dropped
        with self.assertRaises(ValueError):
            split_percentages({"a": 0})
        with self.assertRaises(ValueError):
            split_percentages({"a": -1, "b": 2})

    def test_the_definition_matches_the_documented_body(self):
        rule = cost_allocation_rule(source_tag="agent.name", source_values=["kb-answers", "care-triage"],
                                    target_tag="cost-centre", shares={"CC-RISK": 2, "CC-SUP": 1}, description="d")
        self.assertEqual(rule, {"properties": {
            "description": "d", "status": "NotActive",
            "details": {
                "sourceResources": [{"resourceType": "Tag", "name": "agent.name", "values": ["care-triage", "kb-answers"]}],
                "targetResources": [{"resourceType": "Tag", "policyType": "FixedProportion", "name": "cost-centre",
                                     "values": [{"name": "CC-RISK", "percentage": 66.67},
                                                {"name": "CC-SUP", "percentage": 33.33}]}],
            }}})

    def test_the_documented_limits_are_enforced(self):
        ok = {"source_tag": "t", "source_values": ["v"], "target_tag": "c", "shares": {"x": 1}}
        with self.assertRaises(ValueError):
            cost_allocation_rule(**{**ok, "status": "Processing"})         # read-only in Azure
        with self.assertRaises(ValueError):
            cost_allocation_rule(**{**ok, "source_values": [f"v{i}" for i in range(MAX_VALUES + 1)]})
        with self.assertRaises(ValueError):
            cost_allocation_rule(**{**ok, "shares": {f"c{i}": 1 for i in range(MAX_VALUES + 1)}})
        with self.assertRaises(ValueError):
            cost_allocation_rule(**{**ok, "source_values": [" "]})
        with self.assertRaises(ValueError):
            cost_allocation_rule(**{**ok, "target_tag": " "})
        self.assertEqual(cost_allocation_rule(**{**ok, "status": "Active"})["properties"]["status"], "Active")

    def test_rule_names_are_safe(self):
        self.assertEqual(rule_name("Support agents / EMEA"), "finops-Support-agents-EMEA")
        self.assertEqual(rule_name("???"), "finops-rule")
        self.assertLessEqual(len(rule_name("x" * 400)), 260)


class AllocationRuleCallTests(unittest.TestCase):
    ACCOUNT = "1234567:abcdef-1234_2026-01-01"

    def setUp(self):
        self.session = FakeSession()
        self.fo = make_client(self.session)
        self.rule = cost_allocation_rule(source_tag="agent.name", source_values=["a"], target_tag="cc", shares={"x": 1})

    def test_put_goes_to_the_billing_account_with_the_checked_version(self):
        self.session.add("PUT", "/costAllocationRules/finops-x", 200, {"name": "finops-x"})
        out = self.fo.cost.put_cost_allocation_rule(self.ACCOUNT, "finops-x", self.rule)
        call = self.session.calls[-1]
        self.assertEqual(out, {"name": "finops-x"})
        self.assertIn("/providers/Microsoft.Billing/billingAccounts/", call["url"])
        self.assertIn("/providers/Microsoft.CostManagement/costAllocationRules/finops-x", call["url"])
        self.assertEqual(call["params"]["api-version"], "2024-08-01")
        self.assertEqual(call["json"], self.rule)

    def test_list_and_delete(self):
        self.session.add("GET", "/costAllocationRules", 200, {"value": [{"name": "r1"}, {"name": "r2"}]})
        self.assertEqual([r["name"] for r in self.fo.cost.list_cost_allocation_rules(self.ACCOUNT)], ["r1", "r2"])
        self.session.add("DELETE", "/costAllocationRules/r1", 204)
        self.fo.cost.delete_cost_allocation_rule(self.ACCOUNT, "r1")
        self.assertEqual(self.session.calls[-1]["method"], "DELETE")

    def test_bad_arguments_never_reach_azure(self):
        before = len(self.session.calls)
        for bad_name in ("has space", "../x", ""):
            with self.assertRaises(ValueError):
                self.fo.cost.put_cost_allocation_rule(self.ACCOUNT, bad_name, self.rule)
        with self.assertRaises(ValueError):
            self.fo.cost.put_cost_allocation_rule(self.ACCOUNT, "ok", {"details": {}})
        with self.assertRaises(ValueError):
            self.fo.cost.put_cost_allocation_rule("  ", "ok", self.rule)
        self.assertEqual(len(self.session.calls), before)


if __name__ == "__main__":
    unittest.main()
