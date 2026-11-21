"""Plan 2: documented features the SDK lacked (docs/DOCS_CONFORMANCE.md, G1 to G7)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from foundry_finops import (
    AlertDeduper,
    BudgetExceededError,
    BudgetHeaders,
    CostControl,
    CostControlAlertEvent,
    CostControlState,
    Threshold,
    evaluate_cost_control,
    project_budget,
)
from foundry_finops.errors import NotFoundError
from foundry_finops.export import NAMES
from foundry_finops.models import BudgetCategory, BudgetSharing, TimeGrain
from foundry_finops.roles import FOUNDRY_COST_READER_ACTIONS, foundry_cost_reader_definition

from .fakes import FakeSession, make_client

NOW = datetime(2026, 12, 15, 12, tzinfo=timezone.utc)
START, END = "2026-12-01T00:00:00Z", "2027-07-01T00:00:00Z"
CONN = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct/connections/eg"


def cc(name="c", **kw):
    return CostControl(name=name, amount=kw.pop("amount", 100), start_date=START, end_date=END, **kw)


class StateTests(unittest.TestCase):
    """G1: the states from "Cost control states" in the Foundry docs."""

    def state(self, usage, **kw):
        return evaluate_cost_control(amount=100, usage=usage, start=START, end=END, now=NOW, **kw)

    def test_scheduled_and_expired_come_from_the_dates(self):
        early = evaluate_cost_control(amount=100, usage=90, start=START, end=END, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
        late = evaluate_cost_control(amount=100, usage=90, start=START, end=END, now=datetime(2027, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(early.state, CostControlState.SCHEDULED)
        self.assertEqual(late.state, CostControlState.EXPIRED)  # the end date is exclusive

    def test_unreadable_usage_is_active(self):
        self.assertEqual(self.state(None).state, CostControlState.ACTIVE)

    def test_near_threshold_uses_the_lowest_alert_or_80_percent(self):
        self.assertEqual(self.state(79).state, CostControlState.ON_TRACK)
        self.assertEqual(self.state(80).state, CostControlState.NEAR_THRESHOLD)
        self.assertEqual(self.state(55, alert_percents=[50, 75]).state, CostControlState.NEAR_THRESHOLD)

    def test_limit_reached_at_the_block_threshold_or_the_budget(self):
        blocking = self.state(90, block_percents=[90])
        self.assertEqual(blocking.state, CostControlState.LIMIT_REACHED)
        self.assertFalse(blocking.over_budget)
        no_block = self.state(100)
        self.assertEqual(no_block.state, CostControlState.LIMIT_REACHED)
        self.assertTrue(no_block.over_budget)  # tagged Over budget: requests continue


class BlockHeaderTests(unittest.TestCase):
    """G2: headers on blocked and covered responses."""

    def test_blocked_response_headers(self):
        h = BudgetHeaders.from_headers({
            "X-MS-Budget-Cause": "budget_exceeded", "x-ms-applied-cost-control-id": "/id",
            "x-ms-budget-window-reset": "2027-01-01T00:00:00Z", "x-ms-cost-control-type": "Tokens"})
        self.assertTrue(h.blocked)
        self.assertEqual((h.policy_id, h.cost_control_type), ("/id", "Tokens"))
        err = BudgetExceededError("blocked", status_code=429, headers={"x-ms-cost-control-type": "Cost"})
        self.assertEqual(err.cost_control_type, "Cost")

    def test_covered_success_response(self):
        h = BudgetHeaders.from_headers({"x-ms-budget-state": "ok", "x-ms-applicable-policy-id": "/p",
                                        "x-ms-available-pre-call-budget": "12.5"})
        self.assertTrue(h.covered)
        self.assertFalse(h.blocked)
        self.assertEqual(h.available_pre_call_budget, 12.5)
        self.assertFalse(BudgetHeaders.from_headers({}).covered)
        self.assertIsNone(BudgetHeaders.from_headers({"x-ms-available-pre-call-budget": "n/a"}).available_pre_call_budget)

    def test_alert_event_token_budget_fields(self):
        e = CostControlAlertEvent.from_cloud_event({"type": "costControl.alert", "source": "/s", "data": {
            "unit": "tokens", "threshold": "80.0%", "totalConsumed": "8000000", "limit": "10000000", "remaining": "2000000",
            "tokensConsumed": "1500", "costBasis": "usage", "inputTokens": 1000, "outputTokens": "500",
            "startDate": START, "endDate": END}})
        self.assertEqual((e.cost_basis, e.tokens_consumed, e.input_tokens, e.output_tokens, e.call_cost),
                         ("usage", 1500.0, 1000, 500, None))
        self.assertEqual(e.total_consumed, 8_000_000)

    def test_deduper_notifies_once_per_key(self):
        data = {"unit": "usd", "threshold": "80.0%", "limit": 500, "windowEnd": "w", "agentName": "a1", "projectName": "p"}
        ev = CostControlAlertEvent.from_cloud_event({"type": "costControl.alert", "source": "/s", "data": data})
        d = AlertDeduper()
        self.assertTrue(d.first_time(ev, per="agent"))
        self.assertFalse(d.first_time(ev, per="agent"))
        data2 = dict(data, agentName="a2")
        ev2 = CostControlAlertEvent.from_cloud_event({"type": "costControl.alert", "source": "/s", "data": data2})
        self.assertTrue(d.first_time(ev2, per="agent"))
        self.assertFalse(d.first_time(ev2, per=None) and d.first_time(ev, per=None))  # a shared budget: once

    def test_deduper_store_is_bounded(self):
        d = AlertDeduper(max_size=2)
        for i in range(5):
            ev = CostControlAlertEvent.from_cloud_event({"type": "costControl.alert", "source": f"/s{i}", "data": {}})
            self.assertTrue(d.first_time(ev))
        self.assertLessEqual(len(d._store), 2)


class FilterRuleTests(unittest.TestCase):
    """G3: filter rules from the cost control definition."""

    def test_rules(self):
        with self.assertRaisesRegex(ValueError, "non-blank"):
            cc(filters={"sessionId": ["  "]}).to_dict()
        with self.assertRaisesRegex(ValueError, "input or output"):
            cc(category=BudgetCategory.TOKENS, filters={"tokenType": ["both"]}).to_dict()
        with self.assertRaisesRegex(ValueError, "only its own project"):
            cc(project="p1", filters={"project": ["p2"]}).to_dict()
        cc(project="p1", filters={"project": ["p1"], "agent": ["a"]}).to_dict()
        with self.assertRaisesRegex(ValueError, "32 KiB"):
            cc(filters={"deployment": ["d" * 64] * 600}).to_dict()


class CostControlOperationTests(unittest.TestCase):
    URL = "/accounts/acct/costControls/cap"

    def setUp(self):
        self.s = FakeSession()
        self.fo = make_client(self.s)
        self.body = {"name": "cap", "etag": '"e1"', "properties": {
            "category": "Cost", "amount": 5, "timeGrain": "Daily", "timePeriod": {"startDate": START, "endDate": END},
            "notifications": {"Actual_GreaterThanOrEqualTo_80_Percent": {
                "enabled": True, "operator": "GreaterThanOrEqualTo", "threshold": 80, "thresholdType": "Percentage",
                "eventGridConnectionId": CONN}},
            "providerProperties": {"actions": {"Block_GreaterThanOrEqualTo_100_Percent": {
                "type": "Block", "operator": "GreaterThanOrEqualTo", "threshold": 100, "thresholdType": "Percentage"}}}}}

    def test_exists_uses_head(self):
        self.s.add("HEAD", self.URL, 200, None)
        self.assertTrue(self.fo.cost_controls.exists("acct", "cap"))
        self.assertEqual(self.s.calls[-1]["method"], "HEAD")
        s2 = FakeSession()
        self.assertFalse(make_client(s2).cost_controls.exists("acct", "cap"))  # no route: 404

    def test_remove_alert_sends_a_null_notification_and_nothing_else(self):
        self.s.add("GET", self.URL, 200, self.body)
        self.s.add("PATCH", self.URL, 200, self.body)
        self.fo.cost_controls.remove_alert("acct", "cap", if_match='"e1"')
        call = self.s.calls[-1]
        self.assertEqual(call["json"], {"properties": {"notifications": {"Actual_GreaterThanOrEqualTo_80_Percent": None}}})
        self.assertEqual(call["headers"]["If-Match"], '"e1"')

    def test_remove_block_sends_a_null_action(self):
        self.s.add("GET", self.URL, 200, self.body)
        self.s.add("PATCH", self.URL, 200, self.body)
        self.fo.cost_controls.remove_block("acct", "cap")
        self.assertEqual(self.s.calls[-1]["json"], {"properties": {"providerProperties": {
            "actions": {"Block_GreaterThanOrEqualTo_100_Percent": None}}}})

    def test_remove_without_one_is_a_no_op(self):
        body = {"name": "cap", "properties": {"category": "Cost", "amount": 5, "timeGrain": "Daily"}}
        self.s.add("GET", self.URL, 200, body)
        self.fo.cost_controls.remove_alert("acct", "cap")
        self.assertEqual([c["method"] for c in self.s.calls], ["GET"])

    def test_usage_connection_read_set_and_clear(self):
        acct = {"name": "acct", "properties": {"costControlConnections": {"appInsightsConnectionId": CONN}}}
        self.s.add("GET", "/accounts/acct", 200, acct)
        self.assertEqual(self.fo.cost_controls.get_usage_connection("acct"), CONN)
        self.assertEqual(self.fo.foundry.get_account("acct").app_insights_connection_id, CONN)
        self.s.add("PATCH", "/accounts/acct", 200, acct)
        self.fo.cost_controls.set_usage_connection("acct", CONN)
        self.assertEqual(self.s.calls[-1]["json"], {"properties": {"costControlConnections": {"appInsightsConnectionId": CONN}}})
        self.fo.cost_controls.set_usage_connection("acct", None)
        self.assertEqual(self.s.calls[-1]["json"], {"properties": {"costControlConnections": None}})

    def test_delete_project_cost_controls_deletes_only_the_projects_own(self):
        item = {"name": "p-cap", "properties": {"category": "Cost", "amount": 5, "timeGrain": "Daily"}}
        self.s.add("GET", "/projects/p1/costControls", 200, {"value": [item]})
        self.s.add("DELETE", "/projects/p1/costControls/p-cap", 200, None)
        self.assertEqual(self.fo.cost_controls.delete_project_cost_controls("acct", "p1"), ["p-cap"])
        self.assertEqual([c["method"] for c in self.s.calls], ["GET", "DELETE"])


class ProjectBudgetTests(unittest.TestCase):
    """The *Project budget* card: whole-project spending budgets, counted as the docs describe."""

    def test_selection(self):
        own = cc("own", amount=500, project="p1")
        cheaper = cc("cheaper", amount=300, filters={"project": ["p1"]})  # a resource's control filtered to the project
        wide = cc("wide", amount=100)  # resource-wide: not a project budget
        tokens = cc("tok", amount=1000, category=BudgetCategory.TOKENS, project="p1")
        deploy = cc("dep", amount=50, project="p1", filters={"deployment": ["gpt"]})
        per_agent = cc("agents", amount=10, project="p1", sharing=BudgetSharing.PER_AGENT)
        other = cc("other", amount=5, filters={"project": ["p2"]})
        pb = project_budget([own, cheaper, wide, tokens, deploy, per_agent, other], "p1")
        self.assertEqual((pb.budget.name, pb.count, pb.per_agent_or_session), ("cheaper", 2, 1))
        self.assertIsNone(project_budget([wide, other], "p1").budget)

    def test_applicable_lists_own_and_resource_controls(self):
        s = FakeSession()
        fo = make_client(s)
        def mk(n, **extra):
            return {"name": n, "properties": {"category": "Cost", "amount": 5, "timeGrain": "Daily", **extra}}

        s.add("GET", "/accounts/acct/costControls", 200, {"value": [
            mk("wide"), mk("p1only", filter={"additionalInfo": {"name": "project", "operator": "In", "values": ["p1"]}}),
            mk("p2only", filter={"additionalInfo": {"name": "project", "operator": "In", "values": ["p2"]}})]})
        s.add("GET", "/accounts/acct/projects", 200, {"value": [{"name": "acct/p1"}]})
        s.add("GET", "/projects/p1/costControls", 200, {"value": [{**mk("mine", amount=3), "id": "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct/projects/p1/costControls/mine"}]})
        names = [c.name for c in fo.cost_controls.applicable("acct", "p1")]
        self.assertEqual(sorted(names), ["mine", "p1only", "wide"])
        self.assertEqual(fo.cost_controls.project_budget("acct", "p1").budget.name, "mine")


class PolicyTests(unittest.TestCase):
    """G6: policy compliance behind the fleet's policy cards."""

    def test_states_filters_non_compliant_and_follows_paging(self):
        s = FakeSession()
        fo = make_client(s)
        page1 = {"value": [{"resourceId": "/r1", "policyAssignmentName": "a"}, {"resourceId": "/r1", "policyAssignmentName": "b"}],
                 "@odata.nextLink": "https://management.azure.com/next?x=1"}
        s.add("POST", "/next", 200, {"value": [{"resourceId": "/r2", "policyAssignmentName": "a"}]})
        s.add("POST", "policyStates/latest/queryResults", 200, page1)
        rows = fo.policy.violations_by_resource("/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct")
        self.assertEqual([(r["resource_id"], r["violations"]) for r in rows], [("/r1", 2), ("/r2", 1)])
        first = s.calls[0]
        self.assertEqual(first["method"], "POST")
        self.assertEqual(first["params"]["$filter"], "(IsCompliant eq false)")
        self.assertEqual(first["params"]["api-version"], "2019-10-01")

    def test_download_has_a_policy_query(self):
        self.assertIn("policy_violations", NAMES)


class CostReaderRoleTests(unittest.TestCase):
    def test_definition_matches_the_documented_role(self):
        d = foundry_cost_reader_definition("sub-1")
        self.assertEqual(d["Name"], "Foundry Cost Reader")
        self.assertEqual(d["AssignableScopes"], ["/subscriptions/sub-1"])
        self.assertEqual(d["Actions"], list(FOUNDRY_COST_READER_ACTIONS))
        self.assertEqual((d["NotActions"], d["DataActions"], d["NotDataActions"]), ([], [], []))
        with self.assertRaises(ValueError):
            foundry_cost_reader_definition("")


class ThresholdSanity(unittest.TestCase):
    def test_threshold_model_is_unchanged(self):
        self.assertEqual(Threshold(80).threshold_type, "Percentage")
        self.assertEqual(TimeGrain.MONTHLY, "Monthly")
        self.assertIsNotNone(NotFoundError)


if __name__ == "__main__":
    unittest.main()
