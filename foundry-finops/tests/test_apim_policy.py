"""The prototype gateway policy parses and keeps its rules. It is not run against API Management."""

from __future__ import annotations

import shutil
import subprocess
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

APIM = Path(__file__).resolve().parents[1] / "examples" / "apim"
POLICY = APIM / "llm-caller-attribution.policy.xml"


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

    def test_the_gateway_calls_the_model_with_its_own_identity_after_recording_the_caller(self):
        children = [c.tag for c in self.inbound]
        self.assertIn("authentication-managed-identity", children)
        self.assertGreater(children.index("authentication-managed-identity"), children.index("trace"))


class DeployFilesTests(unittest.TestCase):
    def test_the_template_loads_the_policy_file_and_keeps_subscription_keys(self):
        bicep = (APIM / "deploy" / "main.bicep").read_text(encoding="utf-8")
        self.assertIn("loadTextContent('../llm-caller-attribution.policy.xml')", bicep)
        self.assertIn("subscriptionRequired: true", bicep)        # never an open proxy to the model
        self.assertIn("verbosity: 'information'", bicep)           # or the trace record is not written
        self.assertIn("name: 'tenant-id'", bicep)
        self.assertIn("name: 'api-audience'", bicep)

    @unittest.skipUnless(shutil.which("bash"), "bash is needed")
    def test_the_scripts_parse(self):
        for name in ("deploy.sh", "smoke-test.sh"):
            result = subprocess.run(["bash", "-n", str(APIM / "deploy" / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
