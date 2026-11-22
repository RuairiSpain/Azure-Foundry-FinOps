from __future__ import annotations

import pytest

from foundry_finops_portal.access import Access, ScopeRef
from foundry_finops_portal.db import Store

HOME = ScopeRef("sub", "rg", "acct")


def grant(t, v="", w=False):
    return {"scope_type": t, "scope_value": v, "can_write": w}


def test_users_and_grants_round_trip_and_cascade(store: Store):
    store.upsert_user("o1", "a@x.com", "A", "owner")
    store.replace_grants("o1", [grant("project", "p1", True), grant("cost_centre", "CC")])
    assert [g["scope_value"] for g in store.grants_for("o1")] == ["p1", "CC"]
    assert {g["scope_value"]: g["can_write"] for g in store.list_users()[0]["grants"]} == {"p1": True, "CC": False}
    store.delete_user("o1")
    assert store.grants_for("o1") == []


def test_invalid_values_are_refused(store: Store):
    with pytest.raises(ValueError):
        store.upsert_user("o", "a", "A", "root")
    store.upsert_user("o", "a", "A", "viewer")
    with pytest.raises(ValueError):
        store.replace_grants("o", [grant("galaxy", "x")])
    with pytest.raises(ValueError):
        store.replace_grants("o", [grant("project", " ")])


def test_cost_centres_rules_and_cascade(store: Store):
    cc = store.save_cost_centre("CC-1", "One", monthly_budget=100)
    store.add_rule(cc["id"], "project", "p1", share_percent=60)
    store.add_rule(cc["id"], "tag", "x", match_key="team")
    with pytest.raises(ValueError):
        store.add_rule(cc["id"], "tag", "x")  # a tag rule needs the tag name
    with pytest.raises(ValueError):
        store.save_cost_centre("", "n")
    assert len(store.get_cost_centre(cc["id"])["rules"]) == 2
    store.delete_cost_centre(cc["id"])
    assert store.list_cost_centres() == []


def test_alert_events_are_deduplicated_and_audit_is_newest_first(store: Store):
    ev = {"data": {"projectName": "p", "totalConsumed": "4", "limit": 5}}
    assert store.add_alert_event("k1", ev)["project"] == "p"
    assert store.add_alert_event("k1", ev) is None
    store.audit("o", "u", "a1")
    store.audit("o", "u", "a2", detail={"x": 1}, outcome="denied")
    assert [a["action"] for a in store.list_audit()] == ["a2", "a1"]


def test_the_file_database_persists_and_refuses_a_newer_schema(tmp_path):
    path = str(tmp_path / "p.db")
    Store(path).upsert_user("o", "a", "A", "admin")
    assert Store(path).get_user("o")["role"] == "admin"
    import sqlite3

    c = sqlite3.connect(path)
    c.execute("PRAGMA user_version = 99")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError):
        Store(path)


def test_admin_sees_and_writes_everything():
    a = Access("o", "admin", [], {}, HOME)
    assert a.can_read(ScopeRef(project="x")) and a.can_write(ScopeRef(project="x"))
    assert a.visible_projects(["a", "b"]) is None and a.account_level


def test_project_grant_limits_to_that_project():
    a = Access("o", "owner", [grant("project", "p1", True)], {}, HOME)
    assert a.can_read(ScopeRef(project="p1")) and not a.can_read(ScopeRef(project="p2"))
    assert a.can_write(ScopeRef(project="p1")) and not a.can_write(ScopeRef(project="p2"))
    assert a.visible_projects(["p1", "p2"]) == {"p1"} and not a.account_level


def test_cost_centre_grant_reaches_the_projects_its_rules_name():
    a = Access("o", "viewer", [grant("cost_centre", "CC")], {"CC": {"p1", "p3"}}, HOME)
    assert a.visible_projects(["p1", "p2", "p3"]) == {"p1", "p3"}
    assert a.can_read(ScopeRef(project="p3")) and a.can_read(ScopeRef(cost_centre="CC"))
    assert not a.can_write(ScopeRef(project="p1"))  # a viewer never writes


def test_account_wide_grants_see_every_project_but_other_accounts_do_not_match():
    assert Access("o", "viewer", [grant("account", "acct")], {}, HOME).visible_projects(["a"]) is None
    other = Access("o", "viewer", [grant("account", "other")], {}, HOME)
    assert other.visible_projects(["a"]) == set() and not other.can_read(ScopeRef(project="a"))


def test_an_owner_needs_the_write_flag():
    a = Access("o", "owner", [grant("project", "p1", False)], {}, HOME)
    assert a.can_read(ScopeRef(project="p1")) and not a.can_write(ScopeRef(project="p1"))
