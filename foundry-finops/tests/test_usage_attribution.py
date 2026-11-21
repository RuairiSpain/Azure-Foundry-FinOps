"""Optional caller identity on usage records, and the usage-based split of a shared cost."""

from __future__ import annotations

import unittest

from foundry_finops.analysis import UNATTRIBUTED, allocate_by_usage
from foundry_finops.operations.pricing import PriceSheet
from foundry_finops.tracing import UsageTracker, finops_context, identity_from_claims

ANN, BEN, CAL = "oid-ann", "oid-ben", "oid-cal"
CENTRES = {ANN: "CC-FIN", BEN: "CC-FIN", CAL: "CC-HR"}


def calls(*rows):
    return [{"caller_oid": oid, "cost": cost} for oid, cost in rows]


class IdentityTests(unittest.TestCase):
    def tracker(self):
        return UsageTracker(PriceSheet.from_mapping({"m": {"input": 1, "output": 1}}))

    def record(self, **ctx):
        with finops_context(**ctx):
            return self.tracker().record({"model": "m", "usage": {"input_tokens": 10, "output_tokens": 0}})

    def test_no_identity_is_unknown_not_an_error(self):
        rec = self.record()
        self.assertEqual(rec.caller_type, "unknown")
        self.assertEqual(rec.attributes, {})

    def test_user_agent_and_explicit_types(self):
        self.assertEqual(self.record(caller_oid="o").caller_type, "user")
        self.assertEqual(self.record(agent_id="a:1").caller_type, "agent")
        self.assertEqual(self.record(caller_oid="o", agent_id="a:1").caller_type, "user")
        self.assertEqual(self.record(caller_type="service").caller_type, "unknown")  # not one of the known types
        self.assertEqual(self.record(caller_type="agent", caller_oid="o").caller_type, "agent")

    def test_identity_from_claims_leaves_out_what_is_missing(self):
        self.assertEqual(identity_from_claims({"oid": "o1", "tid": "t1", "upn": "x@y"}),
                         {"caller_oid": "o1", "caller_tid": "t1"})
        self.assertEqual(identity_from_claims({"tid": "t1"}), {"caller_tid": "t1"})
        self.assertEqual(identity_from_claims({"oid": ""}), {})
        with finops_context(**identity_from_claims({})):
            self.assertEqual(self.tracker().record({"model": "m", "usage": {}}).caller_type, "unknown")


class AllocateByUsageTests(unittest.TestCase):
    def split(self, total, rows, **kw):
        return allocate_by_usage(total, calls(*rows), CENTRES.get, **kw)

    def test_split_follows_usage_and_adds_up(self):
        out = self.split(100, [(ANN, 3), (BEN, 3), (CAL, 2)])
        by = {r["key"]: r["allocated"] for r in out["rows"]}
        self.assertEqual(by, {"CC-FIN": 75.0, "CC-HR": 25.0})
        self.assertEqual(out["coverage_percent"], 100.0)
        self.assertEqual(sum(by.values()), 100.0)

    def test_unknown_callers_are_spread_in_proportional_mode(self):
        out = self.split(100, [(ANN, 3), (CAL, 1), ("", 4), ("oid-stranger", 2)])
        by = {r["key"]: r["allocated"] for r in out["rows"]}
        self.assertEqual(by, {"CC-FIN": 75.0, "CC-HR": 25.0})          # nobody named is still charged to someone
        self.assertEqual(out["coverage_percent"], 40.0)
        self.assertEqual(out["unattributed_percent"], 60.0)

    def test_hold_mode_keeps_unattributed_usage_apart(self):
        out = self.split(100, [(ANN, 3), (CAL, 1), ("", 6)], unattributed="hold")
        by = {r["key"]: r["allocated"] for r in out["rows"]}
        self.assertEqual(by, {UNATTRIBUTED: 60.0, "CC-FIN": 30.0, "CC-HR": 10.0})
        self.assertEqual(sum(by.values()), 100.0)

    def test_hold_mode_without_unknown_usage_has_no_unattributed_row(self):
        out = self.split(10, [(ANN, 1)], unattributed="hold")
        self.assertEqual([r["key"] for r in out["rows"]], ["CC-FIN"])

    def test_nothing_attributed_puts_everything_in_unattributed(self):
        for mode in ("proportional", "hold"):
            out = self.split(50, [("", 5), ("oid-stranger", 1)], unattributed=mode)
            self.assertEqual([(r["key"], r["allocated"]) for r in out["rows"]], [(UNATTRIBUTED, 50.0)])
            self.assertEqual(out["coverage_percent"], 0.0)

    def test_tokens_can_be_the_measure(self):
        rows = [{"caller_oid": ANN, "tokens": 900}, {"caller_oid": CAL, "tokens": 100}]
        out = allocate_by_usage(10, rows, CENTRES.get, measure="tokens")
        self.assertEqual({r["key"]: r["allocated"] for r in out["rows"]}, {"CC-FIN": 9.0, "CC-HR": 1.0})

    def test_rounding_remainder_keeps_the_total_exact(self):
        out = self.split(100, [(ANN, 1), (CAL, 1), ("oid-x", 0)])
        self.assertEqual(round(sum(r["allocated"] for r in out["rows"]), 2), 100.0)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            self.split(10, [])
        with self.assertRaises(ValueError):
            self.split(10, [(ANN, 0)])
        with self.assertRaises(ValueError):
            self.split(10, [(ANN, -1)])
        with self.assertRaises(ValueError):
            self.split(10, [(ANN, 1)], unattributed="spread")


if __name__ == "__main__":
    unittest.main()


class UsageQueryTests(unittest.TestCase):
    WORKSPACE = "11111111-2222-3333-4444-555555555555"
    APP_INSIGHTS = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.Insights/components/ai"

    def test_client_source_uses_the_table_names_of_the_target(self):
        from foundry_finops import queries

        classic = queries.usage_by_caller("client", self.APP_INSIGHTS)
        workspace = queries.usage_by_caller("client", self.WORKSPACE)
        self.assertIn("traces", classic)
        self.assertIn('customDimensions["finops.caller_oid"]', classic)
        self.assertIn("AppTraces", workspace)
        self.assertIn('Properties["finops.caller_oid"]', workspace)
        for q in (classic, workspace):                   # the same columns either way
            self.assertIn("by oid, kind", q)

    def test_gateway_source_joins_the_token_log_and_needs_a_workspace(self):
        from foundry_finops import queries

        q = queries.usage_by_caller("gateway", self.WORKSPACE)
        self.assertIn("ApiManagementGatewayLlmLog", q)
        self.assertIn("leftouter", q)                    # calls with no trace record stay, as unknown
        self.assertIn('coalesce(kind, "unknown")', q)
        with self.assertRaises(ValueError):
            queries.usage_by_caller("gateway", self.APP_INSIGHTS)

    def test_unknown_source_is_refused(self):
        from foundry_finops import queries

        with self.assertRaises(ValueError):
            queries.usage_by_caller("spreadsheet", self.WORKSPACE)

    def test_the_client_query_reads_the_fields_the_tracker_writes(self):
        from foundry_finops import queries

        tracker = UsageTracker(PriceSheet.from_mapping({"m": {"input": 1, "output": 1}}))
        with finops_context(caller_oid="o1"):
            rec = tracker.record({"model": "m", "usage": {"input_tokens": 10, "output_tokens": 0}})
        self.assertEqual(rec.caller_type, "user")
        q = queries.usage_by_caller("client", self.APP_INSIGHTS)
        for key in ("finops.caller_oid", "finops.caller_type", "cost", "total_tokens"):
            self.assertIn(f'"{key}"', q)
