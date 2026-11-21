"""The `download` command: one JSON file per query under FinOps/<today>/."""

from __future__ import annotations

import contextlib
import itertools
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from foundry_finops.cli import main
from foundry_finops.export import NAMES, QUERIES, ExportContext, _windows, run_export
from foundry_finops.service import LiveService, Settings

from .fakes import FakeSession, make_client

ACCT = "/subscriptions/sub-1/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct"
COST = {"properties": {"columns": [{"name": "Cost", "type": "Number"}, {"name": "UsageDate", "type": "Number"},
                                   {"name": "TagValue", "type": "String"}],
                       "rows": [[1.5, 20260105, "risk"], [2.0, 20260106, "risk"]]}}


def context(session: FakeSession, **kw) -> ExportContext:
    client = make_client(session)
    svc = LiveService(Settings(subscription_id="sub-1", resource_group="rg", foundry_account="acct"), client)
    args = {"start": date(2026, 1, 1), "end": date(2026, 1, 31), "today": date(2026, 2, 10)}
    args.update(kw)
    return ExportContext(client=client, service=svc, account="acct", **args)


class WindowTests(unittest.TestCase):
    def test_long_ranges_are_split_without_gaps(self):
        wins = _windows(date(2024, 1, 1), date(2026, 3, 1))
        self.assertGreater(len(wins), 2)
        self.assertEqual(wins[0][0], date(2024, 1, 1))
        self.assertEqual(wins[-1][1], date(2026, 3, 1))
        for (_, a_end), (b_start, _) in itertools.pairwise(wins):
            self.assertEqual((b_start - a_end).days, 1)
        self.assertTrue(all((b - a).days < 360 for a, b in wins))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_one_file_per_query_in_dated_folder(self):
        s = FakeSession()
        s.add("POST", "/providers/Microsoft.CostManagement/query", 200, COST)
        result = run_export(context(s, project="risk"), self.out)
        folder = self.out / "FinOps" / "2026-02-10"
        self.assertEqual(result.folder, folder)
        cost = json.loads((folder / "cost_by_project.json").read_text())
        self.assertEqual(cost["query"], "cost_by_project")
        self.assertEqual(cost["parameters"]["project"], "risk")
        self.assertEqual(cost["data"][0], {"Cost": 1.5, "UsageDate": "2026-01-05", "TagValue": "risk"})
        post = next(c for c in s.calls if c["method"] == "POST")
        self.assertTrue(post["url"].startswith("https://management.azure.com" + ACCT))
        self.assertEqual(post["json"]["dataset"]["filter"]["tags"]["values"], ["risk"])
        self.assertEqual(post["json"]["timePeriod"]["from"], "2026-01-01T00:00:00Z")
        self.assertEqual(post["json"]["timePeriod"]["to"], "2026-01-31T23:59:59Z")
        manifest = json.loads((folder / "_manifest.json").read_text())
        self.assertEqual([q["name"] for q in manifest["queries"]], NAMES)
        by_name = {q["name"]: q for q in manifest["queries"]}
        self.assertEqual(by_name["cost_by_agent"]["status"], "ok")
        self.assertEqual(by_name["kql_cost_control_blocks"]["status"], "skipped")
        self.assertIn("logs-target", by_name["kql_cost_control_blocks"]["detail"])
        self.assertEqual(by_name["cost_forecast"]["status"], "skipped")  # end date is in the past
        self.assertEqual(by_name["foundry_account"]["status"], "failed")  # no fake route -> 404, recorded
        for q in manifest["queries"]:
            if q["status"] == "ok":
                self.assertTrue((folder / q["file"]).is_file())

    def test_a_year_plus_range_makes_several_cost_calls(self):
        s = FakeSession()
        s.add("POST", "/providers/Microsoft.CostManagement/query", 200, COST)
        run_export(context(s, start=date(2024, 1, 1), end=date(2026, 1, 31)), self.out, only=["cost_daily_total"])
        self.assertEqual(len([c for c in s.calls if c["method"] == "POST"]), 3)
        data = json.loads((self.out / "FinOps/2026-02-10/cost_daily_total.json").read_text())["data"]
        self.assertEqual(len(data), 6)

    def test_forecast_runs_for_a_future_end_date(self):
        s = FakeSession()
        s.add("POST", "/providers/Microsoft.CostManagement/forecast", 200, COST)
        result = run_export(context(s, end=date(2026, 2, 28)), self.out, only=["cost_forecast"])
        self.assertEqual(result.results[0].status, "ok")
        body = next(c for c in s.calls if "/forecast" in c["url"])["json"]
        self.assertEqual(body["timePeriod"]["from"], "2026-02-10T00:00:00Z")

    def test_old_metrics_are_clamped_and_noted(self):
        s = FakeSession()
        s.add("GET", "/providers/Microsoft.Insights/metrics", 200, {"value": []})
        result = run_export(context(s, start=date(2025, 6, 1), end=date(2026, 2, 9)), self.out,
                            only=["token_usage_by_deployment"])
        self.assertEqual(result.results[0].status, "ok")
        payload = json.loads((self.out / "FinOps/2026-02-10/token_usage_by_deployment.json").read_text())
        self.assertTrue(any("clamped" in n for n in payload["notes"]))

    def test_unknown_query_and_bad_range_are_errors(self):
        s = FakeSession()
        with self.assertRaisesRegex(ValueError, "Unknown query"):
            run_export(context(s), self.out, only=["nope"])
        with self.assertRaisesRegex(ValueError, "before the start"):
            run_export(context(s, start=date(2026, 3, 1), end=date(2026, 2, 1)), self.out)

    def test_every_query_has_a_unique_file_safe_name(self):
        names = [q.name for q in QUERIES]
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(all(n.replace("_", "").isalnum() for n in names))


class CliTests(unittest.TestCase):
    def test_list_and_argument_errors(self):
        self.assertEqual(main(["download", "--list"]), 0)
        base = ["--subscription", "s", "-g", "rg", "download", "--account", "a"]
        for argv in (["--demo", "download", "--account", "a", "--start", "2026-01-01", "--end", "2026-01-02"],
                     [*base, "--start", "2026-01-01"],
                     [*base, "--start", "2026-02-01", "--end", "2026-01-01"],
                     [*base, "--start", "01/01/2026", "--end", "2026-01-02"],
                     ["--subscription", "s", "download", "--account", "a", "--start", "2026-01-01",
                      "--end", "2026-01-02"]):  # no resource group
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as cm:
                main(argv)
            self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()


class AuthFailureTests(unittest.TestCase):
    def test_bad_credentials_abort_instead_of_running_every_query(self):
        from foundry_finops import AuthenticationError

        s = FakeSession()
        ctx = context(s)

        class Broken:
            def get_token(self, *a, **k):
                raise RuntimeError("no login")

        ctx.client._transport._tokens._credential = Broken()
        ctx.client._transport._tokens._tokens.clear()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(AuthenticationError):
            run_export(ctx, Path(tmp))
            self.fail("unreachable")
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.suppress(AuthenticationError):
                run_export(ctx, Path(tmp))
            self.assertFalse(list(Path(tmp).rglob("*.json")))  # nothing half-written
        self.assertEqual(s.calls, [])
