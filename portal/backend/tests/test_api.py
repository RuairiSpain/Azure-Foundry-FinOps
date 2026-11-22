from __future__ import annotations

from .conftest import ALICE, BOB, CAROL, DAVE

RG_SCOPE = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-ai-demo"
SPEC = {"name": "t-cap", "project": "fraud-risk", "category": "Cost", "amount": 50, "time_grain": "Monthly",
        "start_date": "2026-12-01", "end_date": "2027-06-01", "block": {"threshold": 100}}


def names(rows, key="name"):
    return {r[key] for r in rows}


def test_everything_needs_a_known_signed_in_person(client):
    assert client.get("/api/me").status_code == 401
    r = client.get("/api/me", headers=DAVE)
    assert r.status_code == 403 and "hasn't given you access" in r.json()["detail"]
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/config").json()["demo_users"]


def test_admin_sees_the_whole_fleet(client):
    fleet = client.get("/api/fleet", headers=ALICE).json()
    assert names(fleet["projects"]) == {"fraud-risk", "customer-care", "marketing-gen", "dev-sandbox"}
    assert fleet["kpis"]["deployments"] == 5 and fleet["kpis"]["policy_violations"] == 3
    assert fleet["kpis"]["cost"] > 0
    me = client.get("/api/me", headers=ALICE).json()
    assert me["role"] == "admin" and me["visible_projects"] is None


def test_an_owner_with_a_project_grant_sees_only_that_project_everywhere(client):
    fleet = client.get("/api/fleet", headers=BOB).json()
    assert names(fleet["projects"]) == {"fraud-risk"}
    assert fleet["kpis"]["deployments"] is None            # a whole-resource number: not shown
    assert fleet["policy"] == [] and fleet["kpis"]["policy_violations"] == 0   # the violations are in other projects
    overview = client.get("/api/cost/overview", headers=BOB).json()
    assert names(overview["series"]["series"]) == {"fraud-risk"}
    assert overview["forecast"]["value"] is None and "limited to projects" in overview["forecast"]["note"]
    full = client.get("/api/cost/overview", headers=ALICE).json()
    assert overview["estimated_cost"]["value"] < full["estimated_cost"]["value"]
    controls = client.get("/api/cost-controls", headers=BOB).json()["cost_controls"]
    assert controls and all(c["project"] == "fraud-risk" or c["filters"].get("project") == ["fraud-risk"] for c in controls)


def test_whole_resource_views_are_refused_for_project_access(client):
    for path in ("/api/operations", "/api/budgets", "/api/tags", "/api/cost/series?group_by=model"):
        r = client.get(path, headers=BOB)
        assert r.status_code == 403 and "limited to projects" in r.json()["detail"], path
    assert client.get("/api/operations", headers=ALICE).status_code == 200


def test_a_cost_centre_grant_gives_that_centres_projects(client):
    fleet = client.get("/api/fleet", headers=CAROL).json()
    assert names(fleet["projects"]) == {"customer-care"}
    assert client.get("/api/projects/fraud-risk/monitor", headers=CAROL).status_code == 404
    mon = client.get("/api/projects/customer-care/monitor", headers=CAROL).json()
    assert mon["latest_billed_cost"]["value"] > 0 and mon["agents"]


def test_cost_table_and_series_by_dimension(client):
    t = client.get("/api/cost/table?group_by=project&days=30", headers=ALICE).json()
    assert abs(sum(r["cost"] for r in t["rows"]) - t["total"]) < 0.05
    agents = client.get("/api/cost/table?group_by=agent", headers=BOB).json()
    assert {r["name"] for r in agents["rows"]} == {"fraud-case-review", "aml-screening"}
    assert client.get("/api/cost/series?group_by=cost_centre", headers=ALICE).json()["series"]


def test_writes_need_a_grant_and_are_audited(client):
    assert client.post("/api/cost-controls", json=SPEC, headers=BOB).status_code == 200
    denied = client.post("/api/cost-controls", json={**SPEC, "name": "x", "project": "customer-care"}, headers=BOB)
    assert denied.status_code == 403 and "write access" in denied.json()["detail"]
    assert client.post("/api/cost-controls", json=SPEC, headers=CAROL).status_code == 403   # a viewer never writes
    rows = client.get("/api/cost-controls?project=fraud-risk", headers=BOB).json()["cost_controls"]
    assert "t-cap" in names(rows) and next(r for r in rows if r["name"] == "t-cap")["can_write"]
    assert client.delete("/api/cost-controls/t-cap?project=fraud-risk", headers=BOB).status_code == 200
    log = client.get("/api/admin/audit", headers=ALICE).json()
    outcomes = [(a["action"], a["actor_upn"], a["outcome"]) for a in log if a["action"].startswith("cost_control")]
    assert ("cost_control.delete", "bob.risk@contoso.example", "ok") in outcomes
    assert ("cost_control.save", "bob.risk@contoso.example", "denied") in outcomes
    assert ("cost_control.save", "carol.care@contoso.example", "denied") in outcomes


def test_an_invalid_cost_control_is_a_400_not_a_crash(client):
    r = client.post("/api/cost-controls", json={**SPEC, "amount": -1}, headers=BOB)
    assert r.status_code == 400
    r = client.post("/api/cost-controls", json={**{k: v for k, v in SPEC.items() if k != "end_date"}}, headers=BOB)
    assert r.status_code == 400 and "end_date" in r.json()["detail"]


def test_budgets_and_tags_are_admin_writes_in_demo(client):
    assert client.put("/api/tags", json={"scope": RG_SCOPE, "tags": {"a": "b"}}, headers=BOB).status_code == 403
    assert client.put("/api/tags", json={"scope": RG_SCOPE, "tags": {"a": "b"}}, headers=ALICE).status_code == 200


def test_read_only_mode_blocks_every_write(store):
    from fastapi.testclient import TestClient

    from foundry_finops_portal import PortalSettings, create_app

    c = TestClient(create_app(PortalSettings(mode="demo", db_path=":memory:", read_only=True), store=store))
    r = c.post("/api/cost-controls", json=SPEC, headers=ALICE)
    assert r.status_code == 403 and "read-only" in r.json()["detail"]


def test_cost_centre_report_allocates_without_double_counting(client):
    rep = client.get("/api/cost-centres/report?basis=project&days=30", headers=ALICE).json()
    by_code = {c["code"]: c for c in rep["centres"]}
    assert set(by_code) == {"CC-RISK", "CC-SUP", "CC-MKT", "CC-PLAT"}
    assert abs(sum(c["cost"] for c in rep["centres"]) - rep["total"]) < 0.05 and rep["unallocated"] == []
    assert by_code["CC-RISK"]["percent_of_budget"] is not None
    # Carol's view holds only the cost centre of her projects
    mine = client.get("/api/cost-centres/report?basis=project", headers=CAROL).json()
    assert [c["code"] for c in mine["centres"]] == ["CC-SUP"]
    assert client.get("/api/cost-centres/report?basis=agent", headers=CAROL).status_code in (200, 403)
    assert client.get("/api/cost-centres/report?basis=galaxy", headers=ALICE).status_code == 400


def test_shared_allocation_and_unallocated(client):
    cid = {c["code"]: c["id"] for c in client.get("/api/cost-centres", headers=ALICE).json()}
    # fraud-risk is 100 % CC-RISK, so any more is refused
    r = client.post(f"/api/cost-centres/{cid['CC-MKT']}/rules", json={"match_type": "project", "match_value": "fraud-risk", "share_percent": 10}, headers=ALICE)
    assert r.status_code == 400 and "100" in r.json()["detail"]
    rule = next(x for x in client.get("/api/cost-centres", headers=ALICE).json() if x["code"] == "CC-RISK")["rules"][0]
    assert client.delete(f"/api/cost-centres/rules/{rule['id']}", headers=ALICE).json() == {"deleted": True}
    rep = client.get("/api/cost-centres/report", headers=ALICE).json()
    assert [u["value"] for u in rep["unallocated"]] == ["fraud-risk"]
    assert client.post(f"/api/cost-centres/{cid['CC-MKT']}/rules", json={"match_type": "project", "match_value": "fraud-risk", "share_percent": 40}, headers=ALICE).status_code == 200
    rep = client.get("/api/cost-centres/report", headers=ALICE).json()
    assert round(rep["unallocated"][0]["unallocated_pct"]) == 60


def test_cost_centres_are_managed_by_admins_only(client):
    body = {"code": "CC-NEW", "name": "New", "monthly_budget": 10}
    assert client.post("/api/cost-centres", json=body, headers=BOB).status_code == 403
    assert client.post("/api/cost-centres", json=body, headers=ALICE).status_code == 200
    assert client.post("/api/cost-centres", json=body, headers=ALICE).status_code == 400   # duplicate code


def test_access_help_page(client):
    pages = {p["page"]: p for p in client.get("/api/access/pages", headers=BOB).json()}
    assert pages["Operations"]["portal_access"] is False and pages["Cost"]["portal_access"] is True
