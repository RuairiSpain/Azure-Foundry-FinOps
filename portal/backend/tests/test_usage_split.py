"""Splitting the resource's cost between cost centres by usage, with identity optional."""

from __future__ import annotations

from foundry_finops_portal.db import Store

from .conftest import ALICE, BOB, CAROL


def rows_by_code(report):
    return {r["code"]: r["allocated"] for r in report["rows"]}


def test_demo_split_follows_usage_and_adds_up(client):
    r = client.get("/api/cost-centres/usage-split?days=30", headers=ALICE)
    assert r.status_code == 200
    report = r.json()
    by = rows_by_code(report)
    # Bob (Risk) 30 %, Carol (Support) 25 %, Alice's agent (Platform) 5 % of the usage that has a cost centre
    assert set(by) == {"CC-RISK", "CC-SUP", "CC-PLAT"}
    assert by["CC-RISK"] > by["CC-SUP"] > by["CC-PLAT"] > 0
    assert round(sum(by.values()), 2) == report["total"]
    assert report["mode"] == "proportional"
    # no identity (20 %), a stranger (10 %) and Finance with no cost centre (10 %) are unattributed
    assert report["coverage_percent"] == 60.0 and report["unattributed_percent"] == 40.0
    assert report["unmapped_departments"][0]["department"] in {"(no department)", "Finance"}
    assert {u["department"] for u in report["unmapped_departments"]} == {"Finance", "(no department)"}


def test_hold_mode_gives_unattributed_usage_its_own_row(client):
    report = client.get("/api/cost-centres/usage-split?unattributed=hold", headers=ALICE).json()
    by = rows_by_code(report)
    assert by[report["unattributed_code"]] > 0
    assert round(sum(by.values()), 2) == report["total"]
    assert abs(by[report["unattributed_code"]] / report["total"] - 0.40) < 0.01


def test_mapping_a_department_moves_its_usage_into_a_cost_centre(client, store):
    mkt = client.get("/api/cost-centres", headers=ALICE).json()
    mkt_id = next(c["id"] for c in mkt if c["code"] == "CC-MKT")
    assert client.put("/api/cost-centres/department-map", headers=ALICE,
                      json={"department": " finance ", "cost_centre_id": mkt_id}).status_code == 200
    report = client.get("/api/cost-centres/usage-split", headers=ALICE).json()
    assert "CC-MKT" in rows_by_code(report)                      # Erin's department matched ignoring case and spaces
    assert report["coverage_percent"] == 70.0
    assert {u["department"] for u in report["unmapped_departments"]} == {"(no department)"}
    assert client.delete("/api/cost-centres/department-map?department=Finance", headers=ALICE).json() == {"deleted": True}
    assert "CC-MKT" not in rows_by_code(client.get("/api/cost-centres/usage-split", headers=ALICE).json())


def test_a_bad_mapping_is_refused(client):
    r = client.put("/api/cost-centres/department-map", headers=ALICE, json={"department": "Risk", "cost_centre_id": 9999})
    assert r.status_code == 400
    r = client.put("/api/cost-centres/department-map", headers=ALICE, json={"department": "  ", "cost_centre_id": 1})
    assert r.status_code == 400


def test_only_administrators_see_it(client):
    for who in (BOB, CAROL):
        assert client.get("/api/cost-centres/usage-split", headers=who).status_code == 403
        assert client.get("/api/cost-centres/department-map", headers=who).status_code == 403
        assert client.put("/api/cost-centres/department-map", headers=who,
                          json={"department": "x", "cost_centre_id": 1}).status_code == 403


def test_a_bad_mode_is_refused(client):
    assert client.get("/api/cost-centres/usage-split?unattributed=spread", headers=ALICE).status_code == 400


def test_the_department_is_kept_as_a_dated_snapshot(client, store):
    client.get("/api/cost-centres/usage-split", headers=ALICE)
    bob = "22222222-2222-2222-2222-222222222222"
    day, dept = store.latest_departments([bob])[bob]
    assert dept == "Risk" and len(day) == 10
    store.snapshot_departments("2000-01-01", {bob: "Old department"})   # an older day doesn't replace the latest
    assert store.latest_departments([bob])[bob] == (day, "Risk")


def test_people_without_a_department_are_asked_again_on_a_later_day(store):
    store.snapshot_departments("2000-01-01", {"o1": ""})
    store.snapshot_departments("2000-01-01", {"o2": "Risk"})
    got = store.latest_departments(["o1", "o2", "o3"])
    assert got == {"o1": ("2000-01-01", ""), "o2": ("2000-01-01", "Risk")}


def test_an_existing_version_1_database_gains_the_new_tables(tmp_path):
    import sqlite3

    path = str(tmp_path / "old.db")
    Store(path)  # creates the current schema
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE department_map; DROP TABLE department_snapshot; DROP TABLE usage_pool_members; "
                      "DROP TABLE usage_pools; DROP TABLE agent_aliases; PRAGMA user_version = 1;")
    con.close()
    store = Store(path)
    assert store.list_department_map() == [] and store.list_pools() == []
    store.snapshot_departments("2026-11-21", {"o": "Risk"})
    store.save_pool("p", "agent", ["a"])


# ---------------------------------------------------------------- the live reader
class _Live:
    """A LiveBackend that never touches Azure: only ``kql`` is replaced."""

    def __init__(self, logs_target="11111111-2222-3333-4444-555555555555", usage_source="gateway", table=None):
        from foundry_finops.service import Settings

        from foundry_finops_portal.backends import LiveBackend

        self.calls = []
        live = object.__new__(LiveBackend)
        live.settings = Settings(logs_target=logs_target, usage_source=usage_source)
        live.kql = lambda query, timespan="P7D": (self.calls.append((query, timespan)) or table)  # type: ignore[method-assign]
        self.backend = live


GATEWAY_TABLE = {
    "columns": [{"name": n, "type": "string"} for n in ("oid", "kind", "agent", "deployment", "tokens", "calls", "cost")],
    "rows": [["o1", "user", "care-triage", "gpt-5.4-mini", 900, 9, None], ["", "unknown", "", "gpt-5.4-mini", 100, 1, None]],
}


def test_the_live_reader_runs_the_gateway_query_and_shapes_the_rows():
    live = _Live(table=GATEWAY_TABLE)
    out = live.backend.usage_by_caller(30)
    assert live.calls[0][1] == "P30D" and "ApiManagementGatewayLlmLog" in live.calls[0][0]
    assert out == [
        {"caller_oid": "o1", "caller_type": "user", "agent": "care-triage", "deployment": "gpt-5.4-mini",
         "cost": None, "tokens": 900, "calls": 9},
        {"caller_oid": "", "caller_type": "unknown", "agent": "", "deployment": "gpt-5.4-mini",
         "cost": None, "tokens": 100, "calls": 1},
    ]


def test_the_live_reader_can_read_the_clients_records_instead():
    table = {"columns": [{"name": n, "type": "string"} for n in ("oid", "kind", "agent", "deployment", "cost", "tokens", "calls")],
             "rows": [["o1", "user", "a1", "m1", 4.5, 300, 3]]}
    live = _Live(logs_target="/subscriptions/s/resourceGroups/rg/providers/microsoft.insights/components/ai",
                 usage_source="client", table=table)
    assert live.backend.usage_by_caller(7) == [
        {"caller_oid": "o1", "caller_type": "user", "agent": "a1", "deployment": "m1", "cost": 4.5, "tokens": 300,
         "calls": 3}]
    assert live.calls[0][1] == "P7D" and "finops.caller_oid" in live.calls[0][0]


def test_the_live_reader_says_what_is_missing():
    import pytest

    with pytest.raises(NotImplementedError, match="PORTAL_LOGS_TARGET"):
        _Live(logs_target="").backend.usage_by_caller(30)
    with pytest.raises(NotImplementedError, match="workspace"):    # gateway source needs a workspace, not App Insights
        _Live(logs_target="/subscriptions/s/resourceGroups/rg/providers/Microsoft.Insights/components/ai").backend.usage_by_caller(30)


def test_a_missing_source_answers_501_with_the_reason(client, monkeypatch):
    from foundry_finops_portal.backends import DemoBackend

    def refuse(self, days):
        raise NotImplementedError("Per-caller usage isn't configured. Set PORTAL_LOGS_TARGET")

    monkeypatch.setattr(DemoBackend, "usage_by_caller", refuse)
    r = client.get("/api/cost-centres/usage-split", headers=ALICE)
    assert r.status_code == 501 and "PORTAL_LOGS_TARGET" in r.json()["detail"]


def test_token_only_records_are_split_by_tokens_and_the_report_says_so(client, monkeypatch):
    from foundry_finops_portal.backends import DemoBackend

    records = [
        {"caller_oid": "22222222-2222-2222-2222-222222222222", "caller_type": "user", "cost": None, "tokens": 900, "calls": 9},
        {"caller_oid": "33333333-3333-3333-3333-333333333333", "caller_type": "user", "cost": None, "tokens": 100, "calls": 1},
    ]
    monkeypatch.setattr(DemoBackend, "usage_by_caller", lambda self, days: records)
    report = client.get("/api/cost-centres/usage-split", headers=ALICE).json()
    assert report["measure"] == "tokens" and "tokens" in report["note"]
    by = rows_by_code(report)
    assert round(by["CC-RISK"] / report["total"], 2) == 0.9 and round(by["CC-SUP"] / report["total"], 2) == 0.1
    assert report["coverage_percent"] == 100.0


def test_usage_source_must_be_known():
    import pytest

    from foundry_finops_portal import PortalSettings

    PortalSettings(mode="demo", usage_source="client").validate()
    with pytest.raises(ValueError, match="PORTAL_USAGE_SOURCE"):
        PortalSettings(mode="demo", usage_source="spreadsheet").validate()
    assert PortalSettings.from_env({"PORTAL_LOGS_TARGET": "w", "PORTAL_USAGE_SOURCE": "CLIENT"}).usage_source == "client"


# --------------------------------------------------------------------------- pools
SUPPORT_AGENTS = ["care-triage", "refund-assistant", "kb-answers"]   # the customer-care project's agents in the demo data


def make_pool(client, name="Support agents", match_type="agent", values=None, **extra):
    body = {"name": name, "match_type": match_type, "values": values or SUPPORT_AGENTS, **extra}
    return client.post("/api/usage-pools", headers=ALICE, json=body)


def test_a_pool_splits_only_its_own_cost_by_the_usage_inside_it(client):
    created = make_pool(client)
    assert created.status_code == 200 and created.json()["values"] == sorted(SUPPORT_AGENTS)
    report = client.get("/api/cost-centres/usage-split", headers=ALICE).json()
    assert len(report["pools"]) == 1
    pool = report["pools"][0]
    assert pool["name"] == "Support agents" and pool["matched_usage"] is True
    assert round(sum(r["allocated"] for r in pool["rows"]), 2) == pool["total"] > 0
    assert report["pooled_cost"] == pool["total"]
    assert report["unpooled_cost"] > 0                       # the other projects' agents are not in the pool
    assert {t["code"]: t["allocated"] for t in report["totals"]} == {r["code"]: r["allocated"] for r in pool["rows"]}
    # the demo callers are the same for every agent, so the pool's coverage matches the whole resource's
    assert pool["coverage_percent"] == 60.0


def test_a_pool_can_be_on_deployments(client):
    assert make_pool(client, "Mini model", "deployment", ["gpt-5.4-mini"]).status_code == 200
    pool = client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"][0]
    assert pool["match_type"] == "deployment" and pool["total"] > 0 and pool["matched_usage"] is True


def test_each_pool_has_its_own_unattributed_setting_and_the_request_can_override_it(client):
    make_pool(client, unattributed="hold")
    held = client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"][0]
    assert held["mode"] == "hold" and any(r["code"] == "(unattributed)" for r in held["rows"])
    spread = client.get("/api/cost-centres/usage-split?unattributed=proportional", headers=ALICE).json()["pools"][0]
    assert spread["mode"] == "proportional" and not any(r["code"] == "(unattributed)" for r in spread["rows"])


def test_one_pool_can_be_asked_for_by_id(client):
    a = make_pool(client).json()
    b = make_pool(client, "Risk agents", values=["fraud-case-review", "aml-screening"]).json()
    only_b = client.get(f"/api/cost-centres/usage-split?pool={b['id']}", headers=ALICE).json()
    assert [p["name"] for p in only_b["pools"]] == ["Risk agents"]
    assert a["id"] != b["id"]
    assert client.get("/api/cost-centres/usage-split?pool=9999", headers=ALICE).status_code == 404


def test_a_pool_whose_members_match_no_usage_says_so(client, monkeypatch):
    from foundry_finops_portal.backends import DemoBackend

    monkeypatch.setattr(DemoBackend, "usage_by_caller", lambda self, days: [
        {"caller_oid": "22222222-2222-2222-2222-222222222222", "caller_type": "user", "agent": "some-client-id",
         "deployment": "gpt-5.4", "cost": 10.0}])
    make_pool(client)
    pool = client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"][0]
    assert pool["matched_usage"] is False and pool["coverage_percent"] == 0.0
    assert [(r["code"], r["allocated"]) for r in pool["rows"]] == [("(unattributed)", pool["total"])]


def test_pools_cannot_overlap_or_mix_bases(client):
    assert make_pool(client).status_code == 200
    r = make_pool(client, "Overlap", values=["care-triage", "aml-screening"])
    assert r.status_code == 400 and "already in pool" in r.json()["detail"]
    r = make_pool(client, "Models", "deployment", ["gpt-5.4"])
    assert r.status_code == 400 and "same basis" in r.json()["detail"]
    assert make_pool(client, "Support agents", values=["x"]).status_code == 400      # the name is taken
    for bad in ({"name": " ", "values": ["x"]}, {"name": "n", "values": []}, {"name": "n", "match_type": "tag"},
                {"name": "n", "unattributed": "spread"}):
        body = {"name": "n", "match_type": "agent", "values": ["x"], **bad}
        assert client.post("/api/usage-pools", headers=ALICE, json=body).status_code == 400, bad


def test_a_pool_can_be_changed_and_deleted(client):
    pool = make_pool(client).json()
    body = {"name": "Support", "match_type": "deployment", "values": ["gpt-5.4-mini"], "unattributed": "hold"}
    changed = client.put(f"/api/usage-pools/{pool['id']}", headers=ALICE, json=body)
    assert changed.status_code == 200 and changed.json()["match_type"] == "deployment"   # the only pool may change basis
    assert client.put("/api/usage-pools/9999", headers=ALICE, json=body).status_code == 404
    assert client.delete(f"/api/usage-pools/{pool['id']}", headers=ALICE).json() == {"deleted": True}
    assert client.get("/api/usage-pools", headers=ALICE).json() == []
    assert client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"] == []   # back to the whole resource


def test_pools_are_for_administrators(client):
    for who in (BOB, CAROL):
        assert client.get("/api/usage-pools", headers=who).status_code == 403
        assert client.post("/api/usage-pools", headers=who,
                           json={"name": "n", "match_type": "agent", "values": ["x"]}).status_code == 403


def test_a_version_2_database_gains_the_pool_tables(tmp_path):
    import sqlite3

    path = str(tmp_path / "v2.db")
    Store(path)
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE usage_pool_members; DROP TABLE usage_pools; DROP TABLE agent_aliases; "
                      "PRAGMA user_version = 2;")
    con.close()
    store = Store(path)
    assert store.save_pool("p", "deployment", ["gpt-5.4"])["values"] == ["gpt-5.4"]


def test_a_version_3_database_gains_idle_and_aliases(tmp_path):
    import sqlite3

    path = str(tmp_path / "v3.db")
    Store(path).save_pool("old", "agent", ["a"])
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE agent_aliases; ALTER TABLE usage_pools DROP COLUMN idle; PRAGMA user_version = 3;")
    con.close()
    store = Store(path)
    assert store.list_pools()[0]["idle"] == "spread"               # an existing pool keeps today's behaviour
    assert store.set_agent_alias("abc", "care-triage") == {"alias": "abc", "agent": "care-triage"}


# ------------------------------------------------------ idle capacity, reconciliation, aliases, rules
BOB_OID = "22222222-2222-2222-2222-222222222222"
CAROL_OID = "33333333-3333-3333-3333-333333333333"


def use_records(monkeypatch, records):
    from foundry_finops_portal.backends import DemoBackend

    monkeypatch.setattr(DemoBackend, "usage_by_caller", lambda self, days: records)


def test_a_pool_can_show_its_unused_fixed_cost_on_its_own_row(client, monkeypatch):
    use_records(monkeypatch, [{"caller_oid": BOB_OID, "caller_type": "user", "agent": "care-triage",
                               "deployment": "gpt-5.4-mini", "cost": 10.0}])
    make_pool(client, idle="hold")
    pool = client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"][0]
    by = {r["code"]: r["allocated"] for r in pool["rows"]}
    assert by["(idle capacity)"] == round(pool["total"] - 10.0, 2) == pool["idle_cost"] > 0
    assert by["CC-RISK"] == 10.0                                 # charged for what its calls would have cost
    assert round(sum(by.values()), 2) == pool["total"]
    assert pool["idle"] == "hold"
    spread = client.get("/api/cost-centres/usage-split", headers=ALICE)
    # the default setting keeps spreading it, and still reports the figure
    client.put(f"/api/usage-pools/{pool['id']}", headers=ALICE, json={
        "name": "Support agents", "match_type": "agent", "values": SUPPORT_AGENTS, "idle": "spread"})
    pool2 = client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"][0]
    assert "(idle capacity)" not in {r["code"] for r in pool2["rows"]} and pool2["idle_cost"] == pool["idle_cost"]
    assert spread.status_code == 200


def test_idle_capacity_needs_cost_so_token_only_records_spread_it_and_say_so(client, monkeypatch):
    use_records(monkeypatch, [{"caller_oid": BOB_OID, "caller_type": "user", "agent": "care-triage",
                               "deployment": "gpt-5.4-mini", "cost": None, "tokens": 500}])
    make_pool(client, idle="hold")
    pool = client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"][0]
    assert "(idle capacity)" not in {r["code"] for r in pool["rows"]}
    assert "tokens" in pool["idle_note"] and pool["idle_cost"] is None


def test_idle_must_be_a_known_setting(client):
    r = make_pool(client, idle="park")
    assert r.status_code == 400 and "idle" in r.json()["detail"]


def test_reconciliation_compares_recorded_tokens_with_azure_monitor(client):
    report = client.get("/api/cost-centres/usage-split/reconciliation", headers=ALICE).json()
    status = {r["name"]: r["status"] for r in report["rows"]}
    assert status["gpt-5.4"] == "ok" and status["gpt-5.4-mini"] == "ok"
    assert status["o4-mini"] == "under" and status["model-router"] == "over"
    assert status["text-embedding-3-large"] == "none_recorded"
    assert report["unrecorded"] > 0 and 0 < report["coverage_percent"] < 100
    assert report["measure"] == "tokens" and "ignoring case" in report["note"]


def test_reconciliation_is_for_administrators_and_says_when_usage_isnt_connected(client, monkeypatch):
    from foundry_finops_portal.backends import DemoBackend

    assert client.get("/api/cost-centres/usage-split/reconciliation", headers=BOB).status_code == 403

    def refuse(self, days):
        raise NotImplementedError("Per-caller usage isn't configured. Set PORTAL_LOGS_TARGET")

    monkeypatch.setattr(DemoBackend, "usage_by_caller", refuse)
    r = client.get("/api/cost-centres/usage-split/reconciliation", headers=ALICE)
    assert r.status_code == 501 and "PORTAL_LOGS_TARGET" in r.json()["detail"]


def test_an_agent_recorded_under_a_client_id_is_found_once_it_is_mapped(client, monkeypatch):
    use_records(monkeypatch, [{"caller_oid": BOB_OID, "caller_type": "user", "agent": "0a1b2c3d-client-id",
                               "deployment": "gpt-5.4-mini", "cost": 10.0}])
    make_pool(client)
    before = client.get("/api/cost-centres/usage-split", headers=ALICE).json()
    assert before["pools"][0]["matched_usage"] is False
    assert before["unmatched_agents"] == [{"agent": "0a1b2c3d-client-id", "usage": 10.0}]   # the hint to map it
    assert client.put("/api/agent-aliases", headers=ALICE,
                      json={"alias": "0A1B2C3D-CLIENT-ID", "agent": "care-triage"}).status_code == 200
    after = client.get("/api/cost-centres/usage-split", headers=ALICE).json()
    assert after["pools"][0]["matched_usage"] is True and after["unmatched_agents"] == []
    assert client.get("/api/agent-aliases", headers=ALICE).json() == [
        {"alias": "0A1B2C3D-CLIENT-ID", "agent": "care-triage"}]
    assert client.delete("/api/agent-aliases?alias=0a1b2c3d-client-id", headers=ALICE).json() == {"deleted": True}
    assert client.get("/api/cost-centres/usage-split", headers=ALICE).json()["pools"][0]["matched_usage"] is False


def test_bad_aliases_are_refused_and_aliases_are_for_administrators(client):
    for body in ({"alias": "", "agent": "a"}, {"alias": "a", "agent": " "}, {"alias": "Care-Triage", "agent": "care-triage"}):
        assert client.put("/api/agent-aliases", headers=ALICE, json=body).status_code == 400, body
    assert client.put("/api/agent-aliases", headers=ALICE, json={"alias": "x1", "agent": "care-triage"}).status_code == 200
    r = client.put("/api/agent-aliases", headers=ALICE, json={"alias": "y1", "agent": "x1"})
    assert r.status_code == 400 and "itself an alias" in r.json()["detail"]
    for who in (BOB, CAROL):
        assert client.get("/api/agent-aliases", headers=who).status_code == 403
        assert client.put("/api/agent-aliases", headers=who, json={"alias": "q", "agent": "r"}).status_code == 403


def test_allocation_rules_are_built_for_review_and_never_sent(client):
    make_pool(client)
    out = client.get("/api/cost-centres/usage-split/allocation-rules", headers=ALICE).json()
    assert len(out["rules"]) == 1 and out["skipped"] == []
    rule = out["rules"][0]
    body = rule["definition"]["properties"]
    assert rule["name"] == "finops-Support-agents" and body["status"] == "NotActive"
    source = body["details"]["sourceResources"][0]
    assert source == {"resourceType": "Tag", "name": "agent.name", "values": sorted(SUPPORT_AGENTS)}
    target = body["details"]["targetResources"][0]
    assert target["name"] == "cost-centre" and target["policyType"] == "FixedProportion"
    assert round(sum(v["percentage"] for v in target["values"]), 2) == 100.0
    assert {v["name"] for v in target["values"]} == {"CC-RISK", "CC-SUP", "CC-PLAT"}   # no unattributed row
    assert rule["excluded_percent"] == 0.0                       # unattributed usage was spread, so nothing is left out
    assert any("nothing here has been sent" in n for n in out["notes"]) and out["api"]["api_version"] == "2024-08-01"
    custom = client.get("/api/cost-centres/usage-split/allocation-rules?cost_centre_tag=cc&source_tag=agent", headers=ALICE)
    assert custom.json()["rules"][0]["definition"]["properties"]["details"]["targetResources"][0]["name"] == "cc"


def test_held_back_usage_is_left_out_of_a_rule_and_the_rest_is_renormalised(client):
    make_pool(client, unattributed="hold")
    rule = client.get("/api/cost-centres/usage-split/allocation-rules", headers=ALICE).json()["rules"][0]
    assert rule["excluded_percent"] == 40.0                      # the usage no cost centre received
    target = rule["definition"]["properties"]["details"]["targetResources"][0]
    assert round(sum(v["percentage"] for v in target["values"]), 2) == 100.0
    assert {v["name"]: v["percentage"] for v in target["values"]} == {"CC-RISK": 50.0, "CC-SUP": 41.67, "CC-PLAT": 8.33}


def test_a_deployment_pool_cannot_be_an_azure_rule(client):
    make_pool(client, "Models", "deployment", ["gpt-5.4-mini"])
    out = client.get("/api/cost-centres/usage-split/allocation-rules", headers=ALICE).json()
    assert out["rules"] == [] and "deployment as a source" in out["skipped"][0]["reason"]


def test_a_pool_with_more_than_25_members_cannot_be_an_azure_rule(client):
    make_pool(client, "Too many", values=["care-triage"] + [f"agent-{i}" for i in range(25)])
    out = client.get("/api/cost-centres/usage-split/allocation-rules", headers=ALICE).json()
    assert out["rules"] == [] and "at most 25" in out["skipped"][0]["reason"]


def test_a_pool_nobody_could_be_charged_for_is_skipped(client, monkeypatch):
    use_records(monkeypatch, [{"caller_oid": "", "caller_type": "unknown", "agent": "care-triage",
                               "deployment": "gpt-5.4-mini", "cost": 5.0}])
    make_pool(client)
    out = client.get("/api/cost-centres/usage-split/allocation-rules", headers=ALICE).json()
    assert out["rules"] == [] and "No cost centre received" in out["skipped"][0]["reason"]


def test_allocation_rules_are_for_administrators(client):
    assert client.get("/api/cost-centres/usage-split/allocation-rules", headers=BOB).status_code == 403
