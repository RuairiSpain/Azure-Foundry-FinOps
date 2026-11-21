from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from foundry_finops_portal import PortalSettings, create_app
from foundry_finops_portal.azure_access import ArmPermissionChecker, OboCredential, _evaluate
from foundry_finops_portal.backends import BackendProvider
from foundry_finops_portal.directory import DirectoryUser, GraphDirectory, StaticDirectory, is_valid_identifier
from foundry_finops_portal.notifications import Dispatcher, validate_webhook_target

from .conftest import ALICE, BOB

ERIN = "55555555-5555-5555-5555-555555555555"


# ------------------------------------------------------------------ admin
def test_adding_a_person_checks_entra_first(client):
    assert client.post("/api/admin/users", json={"identifier": "ghost@contoso.example"}, headers=ALICE).status_code == 404
    assert client.post("/api/admin/users", json={"identifier": "not valid"}, headers=ALICE).status_code == 404
    r = client.post("/api/admin/users", json={"identifier": "erin.finance@contoso.example", "role": "viewer",
                    "grants": [{"scope_type": "project", "scope_value": "dev-sandbox"}]}, headers=ALICE)
    assert r.status_code == 200 and r.json()["oid"] == ERIN and r.json()["grants"][0]["scope_value"] == "dev-sandbox"
    assert client.post("/api/admin/users", json={"identifier": ERIN}, headers=ALICE).status_code == 409
    assert any(u["oid"] == ERIN for u in client.get("/api/admin/users", headers=ALICE).json())


def test_only_admins_manage_users(client):
    assert client.get("/api/admin/users", headers=BOB).status_code == 403
    assert client.post("/api/admin/users", json={"identifier": ERIN}, headers=BOB).status_code == 403
    assert client.get("/api/admin/audit", headers=BOB).status_code == 403


def test_the_last_administrator_cannot_be_removed_demoted_or_disabled(client):
    alice = "11111111-1111-1111-1111-111111111111"
    for call in (lambda: client.delete(f"/api/admin/users/{alice}", headers=ALICE),
                 lambda: client.patch(f"/api/admin/users/{alice}", json={"role": "viewer"}, headers=ALICE),
                 lambda: client.patch(f"/api/admin/users/{alice}", json={"enabled": False}, headers=ALICE)):
        r = call()
        assert r.status_code == 400 and "administrator" in r.json()["detail"]


def test_changing_grants_and_roles_takes_effect_and_is_audited(client):
    bob = "22222222-2222-2222-2222-222222222222"
    client.put(f"/api/admin/users/{bob}/grants", json=[{"scope_type": "project", "scope_value": "marketing-gen", "can_write": True}], headers=ALICE)
    assert {p["name"] for p in client.get("/api/fleet", headers=BOB).json()["projects"]} == {"marketing-gen"}
    client.patch(f"/api/admin/users/{bob}", json={"enabled": False}, headers=ALICE)
    assert client.get("/api/fleet", headers=BOB).status_code == 403
    actions = [a["action"] for a in client.get("/api/admin/audit", headers=ALICE).json()]
    assert "user.grants" in actions and "user.update" in actions
    assert client.put(f"/api/admin/users/{bob}/grants", json=[{"scope_type": "galaxy"}], headers=ALICE).status_code == 400


def test_directory_helpers():
    assert is_valid_identifier("a@b.com") and is_valid_identifier("11111111-1111-1111-1111-111111111111")
    assert not is_valid_identifier("a b") and not is_valid_identifier("../x") and not is_valid_identifier("a@b")
    d = StaticDirectory([DirectoryUser("11111111-1111-1111-1111-111111111111", "A@B.com", "A")])
    assert d.lookup("a@b.com").display_name == "A" and d.lookup("x@y.com") is None


def test_graph_lookup_uses_the_portals_token_and_a_safe_url():
    calls = []

    class Resp:
        def __init__(self, code, body=None):
            self.status_code, self._b = code, body

        def json(self):
            return self._b

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(self.status_code)

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            calls.append((url, headers["Authorization"]))
            return Resp(404) if "missing" in url else Resp(200, {"id": "oid", "userPrincipalName": "a@b.com", "displayName": "A", "accountEnabled": False})

    class Cred:
        def get_token(self, scope):
            assert scope == "https://graph.microsoft.com/.default"
            return type("T", (), {"token": "svc"})()

    g = GraphDirectory(Cred(), Session())
    u = g.lookup("a@b.com")
    assert u.oid == "oid" and not u.enabled and calls[0][1] == "Bearer svc" and calls[0][0].endswith("/users/a%40b.com")
    assert g.lookup("missing@b.com") is None
    assert g.lookup("a/../../b") is None and len(calls) == 2   # free text never reaches the URL


# ----------------------------------------------------- events and notifications
class FakeResp:
    def raise_for_status(self):
        pass


class FakeSession:
    def __init__(self):
        self.posts = []

    def post(self, url, json=None, timeout=None):
        self.posts.append((url, json))
        return FakeResp()


def event(agent="a1", project="fraud-risk", total=410):
    return {"specversion": "1.0", "type": "costControl.alert", "source": "/subscriptions/s", "id": "1",
            "data": {"unit": "usd", "threshold": "80.0%", "severity": "warning", "limit": 500, "totalConsumed": total,
                     "remaining": 90, "windowEnd": "2027-01-01T00:00:00Z", "projectName": project, "agentName": agent}}


@pytest.fixture
def intake(store):
    session = FakeSession()
    settings = PortalSettings(mode="demo", db_path=":memory:", alert_webhook_secret="s3cret", allow_private_webhooks=True)
    dispatcher = Dispatcher(settings, store, session)
    return TestClient(create_app(settings, store=store, dispatcher=dispatcher)), session, store


def test_alert_intake_needs_the_secret_and_is_off_without_one(client, intake):
    assert client.post("/api/events/cost-control-alert", json=event()).status_code == 503
    c, _, _ = intake
    assert c.post("/api/events/cost-control-alert", json=event()).status_code == 401
    assert c.post("/api/events/cost-control-alert?secret=wrong", json=event()).status_code == 401
    assert c.post("/api/events/cost-control-alert?secret=s3cret", json=event()).json() == {"stored": 1, "notified": 1}   # the demo seed has a log rule


def test_event_grid_validation_handshake(intake):
    c, _, _ = intake
    r = c.options("/api/events/cost-control-alert", headers={"WebHook-Request-Origin": "eventgrid.azure.net"})
    assert r.headers["webhook-allowed-origin"] == "eventgrid.azure.net"


def test_one_event_per_request_over_a_threshold_stores_and_notifies_once(intake):
    c, session, store = intake
    store.save_notification_rule({"name": "w", "scope_type": "project", "scope_value": "fraud-risk", "trigger": "event",
                                  "channel": "webhook", "target": "https://hooks.example.com/x"}, "t")
    url = "/api/events/cost-control-alert?secret=s3cret"
    # the demo seed has a log rule for fraud-risk too, so each new event notifies two rules
    assert c.post(url, json=[event(total=410), event(total=420)]).json() == {"stored": 1, "notified": 2}
    assert c.post(url, json=event(agent="a2")).json() == {"stored": 1, "notified": 2}   # another agent: its own budget
    assert len(session.posts) == 2 and "reached 80.0%" in session.posts[0][1]["subject"]
    assert c.post(url, json={"type": "something.else"}).json()["stored"] == 0
    events = c.get("/api/alert-events", headers=ALICE).json()
    assert len(events) == 2 and events[0]["notified"]
    assert c.post(url, json=event(agent="z", project="customer-care")).json()["notified"] == 0   # rule is for fraud-risk


def test_threshold_rules_fire_once_per_day(store):
    session = FakeSession()
    d = Dispatcher(PortalSettings(allow_private_webhooks=True), store, session)
    store.save_notification_rule({"name": "hot", "scope_type": "all", "trigger": "percent_used", "threshold": 80,
                                  "channel": "webhook", "target": "https://hooks.example.com/x"}, "t")
    controls = [{"name": "a", "project": "p", "percent_used": 85.0, "state": "Near threshold"},
                {"name": "b", "project": "p", "percent_used": 10.0, "state": "On track"}]
    assert d.evaluate(controls) == 1 and "a at 85.0%" in session.posts[0][1]["body"] and "b at" not in session.posts[0][1]["body"]
    assert d.evaluate(controls) == 0   # throttled


def test_webhook_targets_must_be_https_and_public():
    for bad in ("http://example.com", "https://127.0.0.1/x", "https://169.254.169.254/metadata", "https://10.0.0.5/", "ftp://x"):
        with pytest.raises(ValueError):
            validate_webhook_target(bad)
    validate_webhook_target("https://127.0.0.1/x", allow_private=True)


def test_rules_are_validated_and_scoped(client):
    body = {"name": "r", "scope_type": "project", "scope_value": "fraud-risk", "trigger": "percent_used", "threshold": 80,
            "channel": "log"}
    assert client.post("/api/notification-rules", json=body, headers=BOB).status_code == 200
    assert client.post("/api/notification-rules", json={**body, "scope_value": "customer-care"}, headers=BOB).status_code == 403
    assert client.post("/api/notification-rules", json={**body, "scope_type": "all"}, headers=BOB).status_code == 403
    assert client.post("/api/notification-rules", json={**body, "channel": "webhook", "target": "http://x"}, headers=ALICE).status_code == 400
    assert client.post("/api/notification-rules", json={**body, "channel": "email", "target": "nope"}, headers=ALICE).status_code == 400
    assert client.post("/api/notification-rules", json={**body, "threshold": 0}, headers=ALICE).status_code == 400
    mine = client.get("/api/notification-rules", headers=BOB).json()
    assert mine and all(r["scope_value"] in ("fraud-risk", "") for r in mine)


# ------------------------------------------------------------ live-mode wiring
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TENANT, CLIENT = "tenant-1", "client-1"


def bearer(oid="oid-admin", **over):
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": f"api://{CLIENT}", "tid": TENANT, "oid": oid,
              "preferred_username": f"{oid}@contoso.com", "name": oid, "scp": "access_as_user", "exp": int(time.time()) + 600}
    claims.update(over)
    return {"Authorization": "Bearer " + jwt.encode(claims, KEY, algorithm="RS256")}


class Perms:
    def __init__(self, allow):
        self.allow, self.asked = allow, []

    def allows(self, principal, resource_id, action):
        self.asked.append((principal.oid, resource_id, action))
        return self.allow


def entra_app(store, allow=True):
    from foundry_finops.service import Settings

    from foundry_finops_portal.backends import DemoBackend

    settings = PortalSettings(mode="entra", tenant_id=TENANT, client_id=CLIENT, client_secret="x", subscription_id="sub",
                              resource_group="rg", foundry_account="acct", bootstrap_admins=["oid-admin"], db_path=":memory:")

    class Provider(BackendProvider):
        def __init__(self):
            self.settings = settings
            self._demo = DemoBackend(Settings(demo=True))

    perms = Perms(allow)
    app = create_app(settings, store=store, directory=StaticDirectory([]), permissions=perms, clients=object(),  # type: ignore[arg-type]
                     key_resolver=lambda t: KEY.public_key(), backends=Provider())
    return TestClient(app), perms


def test_entra_mode_needs_a_valid_token_and_bootstraps_the_admin(store):
    c, _ = entra_app(store)
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers=bearer(scp="nothing")).status_code == 401
    me = c.get("/api/me", headers=bearer()).json()
    assert me["role"] == "admin" and me["azure"]["account"] == "acct"
    assert c.get("/api/me", headers=bearer(oid="stranger")).status_code == 403   # signed in, not added
    assert c.get("/api/config").json()["msal"]["scope"] == f"api://{CLIENT}/access_as_user"


def test_entra_writes_check_azure_rbac_for_the_person_before_the_portal_acts(store):
    c, perms = entra_app(store, allow=False)
    spec = {"name": "n", "project": "fraud-risk", "category": "Cost", "amount": 5, "start_date": "2026-12-01", "end_date": "2027-01-01"}
    r = c.post("/api/cost-controls", json=spec, headers=bearer())
    assert r.status_code == 403 and "Azure RBAC" in r.json()["detail"]
    assert perms.asked == [("oid-admin", "/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/acct/projects/fraud-risk",
                            "Microsoft.CognitiveServices/accounts/projects/costControls/write")]
    log = c.get("/api/admin/audit", headers=bearer()).json()
    assert log[0]["outcome"] == "denied" and "Azure RBAC" in log[0]["detail"]["reason"]


def test_entra_writes_go_ahead_when_both_layers_allow(store):
    c, perms = entra_app(store, allow=True)
    spec = {"name": "n", "category": "Cost", "amount": 5, "start_date": "2026-12-01", "end_date": "2027-01-01"}
    assert c.post("/api/cost-controls", json=spec, headers=bearer()).status_code == 200
    assert perms.asked[0][2] == "Microsoft.CognitiveServices/accounts/costControls/write"


def test_permission_evaluation_follows_azure_rules():
    payload = {"value": [{"actions": ["Microsoft.CognitiveServices/*"], "notActions": ["Microsoft.CognitiveServices/accounts/costControls/delete"]}]}
    assert _evaluate(payload, "Microsoft.CognitiveServices/accounts/costControls/write")
    assert not _evaluate(payload, "Microsoft.CognitiveServices/accounts/costControls/delete")
    assert not _evaluate({"value": []}, "x")


def test_arm_permission_checker_uses_the_persons_token_and_refuses_odd_ids():
    seen = []

    class Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"value": [{"actions": ["*/read"], "notActions": []}]}

    class Session:
        def get(self, url, params=None, headers=None, timeout=None):
            seen.append((url, headers["Authorization"], params))
            return Resp()

    from foundry_finops_portal.auth import Principal

    checker = ArmPermissionChecker(lambda p: f"obo-{p.oid}", Session())
    p = Principal("u1", "u@x", "U")
    assert checker.allows(p, "/subscriptions/s/resourceGroups/r", "Microsoft.Consumption/budgets/read")
    assert not checker.allows(p, "/subscriptions/s/resourceGroups/r", "Microsoft.Consumption/budgets/write")
    assert seen[0][1] == "Bearer obo-u1" and seen[0][0].endswith("/providers/Microsoft.Authorization/permissions")
    for bad in ("https://evil", "/subscriptions/s/../x", "/subscriptions/s?x=1"):
        with pytest.raises(ValueError):
            checker.allows(p, bad, "x")


def test_obo_credential_exchanges_the_persons_token_and_reports_failures():
    from azure.core.exceptions import ClientAuthenticationError

    class App:
        def acquire_token_on_behalf_of(self, user_assertion, scopes):
            if user_assertion == "bad":
                return {"error": "invalid_grant", "error_description": "consent needed"}
            return {"access_token": f"arm-for-{user_assertion}", "expires_in": 3600}

    assert OboCredential(App(), "user-token").get_token("https://management.azure.com/.default").token == "arm-for-user-token"
    with pytest.raises(ClientAuthenticationError, match="consent needed"):
        OboCredential(App(), "bad").get_token("s")


def test_entra_settings_are_validated():
    with pytest.raises(ValueError, match="Missing settings"):
        PortalSettings(mode="entra").validate()
    with pytest.raises(ValueError, match="PORTAL_ADMINS"):
        PortalSettings(mode="entra", tenant_id="t", client_id="c", client_secret="s", subscription_id="s",
                       resource_group="r", foundry_account="a").validate()
    with pytest.raises(ValueError):
        PortalSettings(mode="prod").validate()
    s = PortalSettings.from_env({"PORTAL_MODE": "entra", "PORTAL_ADMINS": "a, b;c", "PORTAL_READ_ONLY": "yes"})
    assert s.bootstrap_admins == ["a", "b", "c"] and s.read_only
