"""Regressions for the defects in docs/DOCS_CONFORMANCE.md (C1 to C7), each checked against its documented source."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from foundry_finops import CostControl, CostControlAlertEvent
from foundry_finops.export import QUERIES, allowed_by_roles
from foundry_finops.models import TimeGrain
from foundry_finops.roles import requirement_for
from foundry_finops.service import LiveService, Settings, window_start

from .fakes import FakeSession, make_client

START, END = "2026-12-01T00:00:00Z", "2027-07-01T00:00:00Z"


class RequiredPeriodTests(unittest.TestCase):
    """C1: ``timePeriod`` with both dates is required by the service."""

    def test_both_dates_are_required(self):
        for kwargs in ({}, {"start_date": START}, {"end_date": END}):
            with self.assertRaisesRegex(ValueError, "start_date and end_date"):
                CostControl(name="c", amount=5, **kwargs).to_dict()

    def test_end_must_follow_start(self):
        with self.assertRaisesRegex(ValueError, "later than"):
            CostControl(name="c", amount=5, start_date=END, end_date=START).to_dict()
        with self.assertRaisesRegex(ValueError, "ISO 8601"):
            CostControl(name="c", amount=5, start_date="soon", end_date=END).to_dict()

    def test_dates_and_seven_digit_timestamps_are_accepted(self):
        body = CostControl(name="c", amount=5, start_date="2026-12-01", end_date="2027-07-01T00:00:00.1234567Z").to_dict()
        self.assertEqual(body["properties"]["timePeriod"]["startDate"], START)


class PatchTests(unittest.TestCase):
    """C2: an empty ``properties`` object would clear every property, so the helpers never send one."""

    def test_helpers_send_only_explicit_changes(self):
        s = FakeSession()
        fo = make_client(s)
        body = {"name": "cap", "properties": {"category": "Cost", "amount": 5, "timeGrain": "Daily"}}
        s.add("GET", "/costControls/cap", 200, body)
        from foundry_finops import cost_controls

        cost_controls.remove_alert(fo, "rg", "acct", "cap")  # nothing to remove: no PATCH at all
        cost_controls.remove_block(fo, "rg", "acct", "cap")
        self.assertEqual([c["method"] for c in s.calls], ["GET", "GET"])


class ResetWindowTests(unittest.TestCase):
    """C3: Daily 00:00 UTC, Weekly Monday 00:00 UTC, Monthly the 1st (calendar-aligned)."""

    def test_windows_ignore_the_start_day(self):
        now = datetime(2026, 11, 21, 13, tzinfo=timezone.utc)  # a Saturday
        cases = {TimeGrain.DAILY: (2026, 11, 21), TimeGrain.WEEKLY: (2026, 11, 16), TimeGrain.MONTHLY: (2026, 11, 1)}
        for grain, ymd in cases.items():
            cc = CostControl(name="c", amount=1, time_grain=grain, start_date="2026-01-07T00:00:00Z", end_date=END)
            self.assertEqual(window_start(cc, now), datetime(*ymd, tzinfo=timezone.utc), grain)


class ForecastTests(unittest.TestCase):
    """C4: a missing forecast stays missing; current spend is never shown as the forecast."""

    def test_empty_forecast_is_none_with_a_reason(self):
        s = FakeSession()
        s.add("POST", "/providers/Microsoft.CostManagement/query", 200, {"properties": {
            "columns": [{"name": "Cost", "type": "Number"}, {"name": "Currency", "type": "String"}],
            "rows": [[42.0, "USD"]]}})
        s.add("POST", "/providers/Microsoft.CostManagement/forecast", 200, {"properties": {
            "columns": [{"name": "Cost", "type": "Number"}], "rows": []}})
        svc = LiveService(Settings(subscription_id="sub-1", resource_group="rg", foundry_account="acct"), make_client(s))
        out = svc.overview(30)
        self.assertEqual(out["kpis"]["spend_mtd"], 42.0)
        self.assertIsNone(out["kpis"]["forecast_month_end"])
        self.assertIn("no forecast", out["forecast_note"])


class FoundryRoleTests(unittest.TestCase):
    """C5: every Foundry role (old or new name) can view cost controls and read usage metrics, but not query logs."""

    def query(self, name):
        return next(q for q in QUERIES if q.name == name)

    def test_foundry_roles_cover_cost_controls_and_usage(self):
        for role in ("Foundry User", "Foundry Project Manager", "Azure AI User", "azure ai project manager"):
            for name in ("cost_controls", "token_usage_by_deployment", "estimated_cost_by_project"):
                self.assertTrue(allowed_by_roles(self.query(name), [role]), (role, name))

    def test_foundry_roles_do_not_cover_cost_data_or_logs(self):
        for name in ("cost_by_project", "budgets", "kql_tokens_by_agent_tracing"):
            q = next((q for q in QUERIES if q.name == name), None)
            if q:
                self.assertFalse(allowed_by_roles(q, ["Foundry User"]), name)


class CostControlPermissionTests(unittest.TestCase):
    """C6: listing cost controls also reads projects and the projects' cost controls."""

    def test_requirement_lists_the_project_permissions(self):
        actions = requirement_for("cost_controls").alternatives[0]
        for a in ("Microsoft.CognitiveServices/accounts/costControls/read",
                  "Microsoft.CognitiveServices/accounts/projects/costControls/read",
                  "Microsoft.CognitiveServices/accounts/projects/read"):
            self.assertIn(a, actions)


class AlertDedupeTests(unittest.TestCase):
    """C7: add the agent or project to the key only for a budget per agent or per project."""

    def event(self, agent, project="p", start="2026-12-01T00:00:00Z"):
        return CostControlAlertEvent.from_cloud_event({
            "type": "costControl.alert", "source": "/subscriptions/s/accounts/a",
            "data": {"unit": "usd", "threshold": "80.0%", "limit": 500, "totalConsumed": 400, "remaining": 100,
                     "windowEnd": "2027-01-01T00:00:00Z", "startDate": start, "agentName": agent, "projectName": project}})

    def test_shared_budget_notifies_once_for_all_agents(self):
        self.assertEqual(self.event("a1").dedupe_key_for(None), self.event("a2").dedupe_key_for(None))

    def test_budget_per_agent_notifies_per_agent(self):
        self.assertNotEqual(self.event("a1").dedupe_key_for("agent"), self.event("a2").dedupe_key_for("agent"))
        self.assertEqual(self.event("a1").dedupe_key_for("agent"), self.event("a1").dedupe_key_for("agent"))

    def test_budget_per_project_ignores_the_agent(self):
        self.assertEqual(self.event("a1").dedupe_key_for("project"), self.event("a2").dedupe_key_for("project"))
        self.assertNotEqual(self.event("a1", "p1").dedupe_key_for("project"), self.event("a1", "p2").dedupe_key_for("project"))

    def test_cost_controls_with_the_same_limit_are_told_apart_by_start_date(self):
        self.assertNotEqual(self.event("a", start="2026-12-01T00:00:00Z").dedupe_key_for(),
                            self.event("a", start="2027-01-01T00:00:00Z").dedupe_key_for())

    def test_unknown_sharing_is_refused(self):
        with self.assertRaises(ValueError):
            self.event("a").dedupe_key_for("session")


if __name__ == "__main__":
    unittest.main()
