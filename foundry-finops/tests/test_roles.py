"""`--roles auto`: query access decided from the identity's real RBAC (permissions and scopes)."""

from __future__ import annotations

import base64
import contextlib
import io
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from foundry_finops import FinOpsClient, FinOpsError, RetryPolicy, StaticTokenCredential
from foundry_finops.cli import main
from foundry_finops.export import NAMES, QUERIES, ExportContext, run_export
from foundry_finops.roles import (
    NO_ROLE_NEEDED,
    QUERY_REQUIREMENTS,
    Permission,
    RoleAccess,
    RoleGrant,
    discover,
)
from foundry_finops.service import LiveService, Settings

from .fakes import FakeSession

SUB = "/subscriptions/sub-1"
RG = f"{SUB}/resourceGroups/rg"
ACCT = f"{RG}/providers/Microsoft.CognitiveServices/accounts/acct"
SCOPES = {"subscription": SUB, "resource_group": RG, "account": ACCT, "apim": RG, "anywhere": SUB}


def jwt(**claims: str) -> str:
    def enc(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(claims)}.sig"


def role(guid: str, name: str, actions: list[str], not_actions: list[str] | None = None) -> tuple[str, dict]:
    rid = f"{SUB}/providers/Microsoft.Authorization/roleDefinitions/{guid}"
    return rid, {"id": rid, "properties": {"roleName": name, "permissions": [
        {"actions": actions, "notActions": not_actions or []}]}}


def assignment(rid: str, scope: str) -> dict:
    return {"properties": {"roleDefinitionId": rid, "scope": scope}}


def azure(assignments: list[tuple[tuple[str, dict], str]], token: str | None = None) -> tuple[FinOpsClient, FakeSession]:
    s = FakeSession()
    for (rid, definition), _ in assignments:
        s.add("GET", rid.rsplit("/", 1)[-1], 200, definition)
    s.add("GET", "/providers/Microsoft.Authorization/roleAssignments", 200,
          {"value": [assignment(rid, scope) for (rid, _), scope in assignments]})
    client = FinOpsClient("sub-1", StaticTokenCredential(token or jwt(oid="user-oid")), resource_group="rg",
                          session=s, retry=RetryPolicy(total=0))
    return client, s


READER = role("guid-reader-0001", "Reader", ["*/read"])
MON = role("guid-monitoring-0002", "Monitoring Reader", ["Microsoft.Insights/*/read", "Microsoft.OperationalInsights/workspaces/query/read"])
CMR = role("guid-costmgmt-0003", "Cost Management Reader", ["Microsoft.CostManagement/*/read", "Microsoft.Consumption/*/read"])


class DiscoveryTests(unittest.TestCase):
    def test_reads_assignments_for_the_token_identity_including_groups(self):
        client, s = azure([(READER, SUB)])
        access = discover(client, SCOPES)
        self.assertEqual(access.describe(), [{"role": "Reader", "scope": SUB}])
        call = next(c for c in s.calls if "roleAssignments" in c["url"])
        self.assertEqual(call["params"]["$filter"], "assignedTo('user-oid')")

    def test_unreadable_assignments_give_actionable_advice(self):
        client, s = azure([])
        s.add("GET", "/providers/Microsoft.Authorization/roleAssignments", 403,
              {"error": {"code": "AuthorizationFailed", "message": "no"}})
        with self.assertRaisesRegex(FinOpsError, "--roles"):
            discover(client, SCOPES)

    def test_token_without_oid_is_explained(self):
        client, _ = azure([], token="not-a-jwt")
        with self.assertRaisesRegex(FinOpsError, "can't be determined"):
            discover(client, SCOPES)


class AccessTests(unittest.TestCase):
    def access(self, *grants: tuple[tuple[str, dict], str]) -> RoleAccess:
        client, _ = azure(list(grants))
        return discover(client, SCOPES)

    def test_reader_at_subscription_allows_everything(self):
        a = self.access((READER, SUB))
        self.assertEqual([q.name for q in QUERIES if a.check(q.name)], [])

    def test_no_assignments_allows_only_public_queries(self):
        a = self.access()
        self.assertEqual([q.name for q in QUERIES if a.check(q.name) is None], sorted(NO_ROLE_NEEDED))

    def test_scope_matters(self):
        a = self.access((MON, RG), (CMR, ACCT))
        ok = {q.name for q in QUERIES if a.check(q.name) is None}
        self.assertIn("token_usage_by_deployment", ok)   # Monitoring Reader on the RG covers the account
        self.assertIn("alerts", ok)                      # ... and the RG itself
        self.assertIn("cost_by_agent", ok)               # Cost Management Reader on the account
        self.assertNotIn("budgets", ok)                  # budgets are read at the RG, wider than the assignment
        self.assertNotIn("quota_usage", ok)              # subscription-wide call, assignments are narrower
        self.assertIn("at " + RG, a.check("budgets") or "")

    def test_assignment_in_another_subscription_does_not_count(self):
        other = "/subscriptions/other/resourceGroups/rg"
        a = self.access((READER, other))
        self.assertIsNotNone(a.check("cost_by_agent"))
        self.assertIsNotNone(a.check("kql_cost_control_blocks"))

    def test_management_group_assignment_covers_the_subscription(self):
        mg = "/providers/Microsoft.Management/managementGroups/corp"
        self.assertIsNone(self.access((READER, mg)).check("quota_usage"))

    def test_custom_role_permissions_and_not_actions_are_honoured(self):
        custom = role("guid-custom-0004", "FinOps Analyst", ["Microsoft.CostManagement/*/read"],
                      ["Microsoft.CostManagement/exports/read"])
        a = self.access((custom, SUB))
        self.assertIsNone(a.check("cost_by_agent"))
        self.assertIsNotNone(a.check("cost_exports"))
        self.assertIsNotNone(a.check("token_usage_by_model"))

    def test_log_queries_need_a_log_role_somewhere_in_the_subscription(self):
        self.assertIsNone(self.access((MON, RG)).check("kql_tokens_by_model_tracing"))
        self.assertIsNotNone(self.access((CMR, SUB)).check("kql_tokens_by_model_tracing"))

    def test_every_query_has_a_requirement(self):
        names = {n for n in NAMES if not n.startswith("kql_")} - NO_ROLE_NEEDED
        self.assertEqual(names, set(QUERY_REQUIREMENTS))

    def test_wildcard_matching_is_case_insensitive_and_exact(self):
        p = Permission(("microsoft.insights/*/read",))
        self.assertTrue(p.allows("Microsoft.Insights/metrics/read"))
        self.assertFalse(p.allows("Microsoft.Insights/metrics/write"))
        self.assertFalse(Permission(("Microsoft.Insights/read",)).allows("Microsoft.Insights/metrics/read"))
        self.assertTrue(RoleGrant("r", SUB, (Permission(("*",)),)).allows("anything/at/all"))


class ExportTests(unittest.TestCase):
    def test_skipped_queries_are_not_called_and_the_manifest_records_the_roles(self):
        client, s = azure([(MON, RG)])
        access = discover(client, SCOPES)
        svc = LiveService(Settings(subscription_id="sub-1", resource_group="rg", foundry_account="acct"), client)
        s.add("GET", "/providers/Microsoft.Insights/metrics", 200, {"value": []})
        ctx = ExportContext(client=client, service=svc, account="acct", start=date(2026, 1, 1), end=date(2026, 1, 31),
                            today=date(2026, 2, 10))
        with tempfile.TemporaryDirectory() as tmp:
            result = run_export(ctx, Path(tmp), only=["token_usage_by_model", "cost_by_agent"], access=access)
            manifest = json.loads((Path(tmp) / "FinOps/2026-02-10/_manifest.json").read_text())
        by = {r.name: r for r in result.results}
        self.assertEqual(by["token_usage_by_model"].status, "ok")
        self.assertEqual(by["cost_by_agent"].status, "skipped")
        self.assertIn("Microsoft.CostManagement/query/read", by["cost_by_agent"].detail)
        self.assertFalse([c for c in s.calls if "CostManagement" in c["url"]])
        self.assertEqual(manifest["parameters"]["roles"], "auto")
        self.assertEqual(manifest["parameters"]["discovered_roles"], [{"role": "Monitoring Reader", "scope": RG}])


class CliTests(unittest.TestCase):
    def test_list_with_auto_is_a_dry_run(self):
        client, _ = azure([(MON, RG)])
        access = discover(client, SCOPES)
        out = io.StringIO()
        with mock.patch("foundry_finops.cli._discover_access", return_value=access), contextlib.redirect_stdout(out):
            rc = main(["--subscription", "sub-1", "-g", "rg", "download", "--account", "acct", "--list",
                       "--roles", "auto"])
        self.assertEqual(rc, 0)
        self.assertIn("run   token_usage_by_model", out.getvalue())
        self.assertIn("skip  cost_by_agent", out.getvalue())

    def test_auto_still_needs_the_usual_arguments(self):
        with self.assertRaises(SystemExit) as cm:
            main(["download", "--roles", "auto", "--list"])
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
