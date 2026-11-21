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
    con.executescript("DROP TABLE department_map; DROP TABLE department_snapshot; PRAGMA user_version = 1;")
    con.close()
    store = Store(path)
    assert store.list_department_map() == []
    store.snapshot_departments("2026-11-21", {"o": "Risk"})


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
    "columns": [{"name": n, "type": "string"} for n in ("oid", "kind", "tokens", "calls", "cost")],
    "rows": [["o1", "user", 900, 9, None], ["", "unknown", 100, 1, None]],
}


def test_the_live_reader_runs_the_gateway_query_and_shapes_the_rows():
    live = _Live(table=GATEWAY_TABLE)
    out = live.backend.usage_by_caller(30)
    assert live.calls[0][1] == "P30D" and "ApiManagementGatewayLlmLog" in live.calls[0][0]
    assert out == [
        {"caller_oid": "o1", "caller_type": "user", "cost": None, "tokens": 900, "calls": 9},
        {"caller_oid": "", "caller_type": "unknown", "cost": None, "tokens": 100, "calls": 1},
    ]


def test_the_live_reader_can_read_the_clients_records_instead():
    table = {"columns": [{"name": n, "type": "string"} for n in ("oid", "kind", "cost", "tokens", "calls")],
             "rows": [["o1", "user", 4.5, 300, 3]]}
    live = _Live(logs_target="/subscriptions/s/resourceGroups/rg/providers/microsoft.insights/components/ai",
                 usage_source="client", table=table)
    assert live.backend.usage_by_caller(7) == [
        {"caller_oid": "o1", "caller_type": "user", "cost": 4.5, "tokens": 300, "calls": 3}]
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
