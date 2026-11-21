"""Parity with the Microsoft client: the same call, models and wire request as ``azure-mgmt-cognitiveservices``.

These tests are what makes moving to the Microsoft client a change of imports only. They run against the real
package (a dependency), so a release that changes the beta surface fails here first.
"""

from __future__ import annotations

import inspect
import json
import unittest
from datetime import datetime, timezone

from azure.core import MatchConditions
from azure.core.pipeline.transport import RequestsTransport
from azure.mgmt.cognitiveservices import CognitiveServicesManagementClient
from azure.mgmt.cognitiveservices import models as cs

from foundry_finops import CostControl, FinOpsClient, Threshold, cost_controls
from foundry_finops._auth import StaticTokenCredential
from foundry_finops.models import BudgetCategory, BudgetSharing, TimeGrain

from .fakes import FakeSession, make_client

CONN = "/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct/connections/eg"
START, END = "2026-12-01T00:00:00Z", "2027-07-01T00:00:00Z"


def official_resource(cls=cs.AccountCostControl):
    """The cost control of the Foundry docs, built by hand with the Microsoft models."""
    return cls(properties=cs.CostControlProperties(
        category="Cost", amount=500, time_grain="Monthly",
        time_period=cs.CostControlTimePeriod(start_date=datetime(2026, 12, 1, tzinfo=timezone.utc),
                                             end_date=datetime(2027, 7, 1, tzinfo=timezone.utc)),
        filter=cs.CostControlFilter(additional_info=cs.CostControlAdditionalInfoFilter(
            name="project", values_property=["fraud-risk"])),
        grouping=[cs.CostControlGrouping(name="agent")],
        notifications={"Actual_GreaterThanOrEqualTo_80_Percent": cs.CostControlNotification(
            event_grid_connection_id=CONN, threshold_type="Percentage", threshold=80, enabled=True)},
        provider_properties=cs.CostControlProviderProperties(actions={
            "Block_GreaterThanOrEqualTo_100_Percent": cs.CostControlAction(threshold_type="Percentage", threshold=100)}),
    ))


def spec() -> CostControl:
    return CostControl(
        name="fraud-risk-monthly", category=BudgetCategory.COST, amount=500, time_grain=TimeGrain.MONTHLY,
        start_date=START, end_date=END, filters={"project": ["fraud-risk"]}, sharing=BudgetSharing.PER_AGENT,
        alert=Threshold(80, event_grid_connection_id=CONN), block=Threshold(100))


class ClientShapeTests(unittest.TestCase):
    def test_finops_client_is_the_microsoft_client(self):
        self.assertTrue(issubclass(FinOpsClient, CognitiveServicesManagementClient))

    def test_constructor_starts_with_the_microsoft_signature(self):
        ours = list(inspect.signature(FinOpsClient.__init__).parameters)[1:4]
        theirs = list(inspect.signature(CognitiveServicesManagementClient.__init__).parameters)[1:4]
        self.assertEqual(ours, theirs)  # credential, subscription_id, base_url

    def test_cost_control_groups_have_the_microsoft_signatures(self):
        client = make_client(FakeSession())
        for group in ("account_cost_controls", "project_cost_controls"):
            official = getattr(CognitiveServicesManagementClient(
                StaticTokenCredential("t"), "sub-1"), group)
            for method in ("list", "get", "create_or_update", "update", "delete", "check_existence"):
                self.assertEqual(inspect.signature(getattr(getattr(client, group), method)),
                                 inspect.signature(getattr(official, method)), (group, method))


class WireParityTests(unittest.TestCase):
    """The spec and the Microsoft models produce the same request."""

    def request(self, call):
        s = FakeSession()
        s.add("PUT", "/costControls/", 200, {"name": "fraud-risk-monthly", "properties": {
            "category": "Cost", "amount": 500, "timeGrain": "Monthly"}})
        call(make_client(s))
        req = s.calls[-1]
        return req["method"], req["url"].split("?")[0], req["json"], req["headers"].get("If-None-Match")

    def test_account_cost_control_request_is_identical(self):
        via_official = self.request(lambda c: c.account_cost_controls.create_or_update(
            "rg", "acct", "fraud-risk-monthly", official_resource(), match_condition=MatchConditions.IfMissing))
        via_helper = self.request(lambda c: cost_controls.upsert(c, "rg", "acct", spec(), only_if_new=True))
        self.assertEqual(via_official, via_helper)

    def test_project_cost_control_request_is_identical(self):
        via_official = self.request(lambda c: c.project_cost_controls.create_or_update(
            "rg", "acct", "fraud-risk", "fraud-risk-monthly", official_resource(cs.ProjectCostControl)))
        via_helper = self.request(lambda c: cost_controls.upsert(c, "rg", "acct", spec(), project="fraud-risk"))
        self.assertEqual(via_official, via_helper)

    def test_models_round_trip_through_the_spec(self):
        resource = official_resource()
        back = CostControl.from_resource(resource.as_dict() | {"name": "fraud-risk-monthly"}).to_resource()
        self.assertEqual(json.loads(json.dumps(back.as_dict()))["properties"],
                         json.loads(json.dumps(resource.as_dict()))["properties"])


class PlainMicrosoftClientTests(unittest.TestCase):
    """The helpers keep working after the move: they only use the official groups."""

    def test_helpers_run_on_the_plain_client(self):
        s = FakeSession()
        s.add("GET", "/accounts/acct/projects", 200, {"value": []})
        s.add("GET", "/accounts/acct/costControls", 200, {"value": [{
            "id": "/subscriptions/s/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct/costControls/a",
            "name": "a", "properties": {"category": "Cost", "amount": 3, "timeGrain": "Daily"}}]})
        plain = CognitiveServicesManagementClient(
            StaticTokenCredential("t"), "sub-1", transport=RequestsTransport(session=s, session_owner=False))
        self.assertEqual([c.name for c in cost_controls.list_specs(plain, "rg", "acct")], ["a"])
        self.assertFalse(isinstance(plain, FinOpsClient))


if __name__ == "__main__":
    unittest.main()
