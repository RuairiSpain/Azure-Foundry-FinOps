"""The prototype gateway policy parses and keeps its rules. It is not run against API Management."""

from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

POLICY = Path(__file__).resolve().parents[1] / "examples" / "apim" / "llm-caller-attribution.policy.xml"


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.root = ET.parse(POLICY).getroot()
        self.inbound = self.root.find("inbound")

    def test_shape(self):
        self.assertEqual(self.root.tag, "policies")
        self.assertIsNotNone(self.inbound)
        for section in ("backend", "outbound", "on-error"):
            self.assertIsNotNone(self.root.find(section))

    def test_missing_token_is_allowed_and_bad_token_is_rejected(self):
        jwt = self.inbound.find(".//validate-jwt")
        self.assertEqual(jwt.get("failed-validation-httpcode"), "401")
        # validation sits in a <choose> so a request with no bearer token is recorded as unknown, not refused
        self.assertIsNotNone(self.inbound.find("choose/when/validate-jwt"))

    def test_object_id_is_not_a_metric_dimension(self):
        dims = [d.get("name") for d in self.inbound.findall("llm-emit-token-metric/dimension")]
        self.assertTrue(dims)
        self.assertLessEqual(len(dims), 5)  # the policy allows five custom dimensions
        self.assertNotIn("caller_oid", dims)

    def test_per_call_record_carries_the_object_id_and_request_id(self):
        message = self.inbound.find("trace/message").text
        for field in ("request_id", "caller_oid", "caller_type", "agent_id"):
            self.assertIn(field, message)

    def test_unknown_is_the_default_type(self):
        var = next(v for v in self.inbound.findall("set-variable") if v.get("name") == "finops-caller-type")
        self.assertIn('return "unknown"', var.get("value"))


if __name__ == "__main__":
    unittest.main()
