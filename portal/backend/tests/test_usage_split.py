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


def test_live_mode_says_what_is_missing_instead_of_inventing_numbers():
    from foundry_finops_portal.backends import LiveBackend

    try:
        LiveBackend.usage_by_caller(object(), 30)  # type: ignore[arg-type]
    except NotImplementedError as exc:
        assert "gateway" in str(exc)
    else:
        raise AssertionError("live usage_by_caller should not return numbers it does not have")


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
