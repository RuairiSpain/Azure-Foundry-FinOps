"""Demo-mode data: people, scope grants, cost centres and notification rules that match the illustrative Foundry data."""

from __future__ import annotations

from .auth import DEMO_USERS
from .db import Store

ALICE, BOB, CAROL = (
    "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222",
    "33333333-3333-3333-3333-333333333333",
)


def seed_demo(store: Store) -> None:
    if store.list_users():
        return
    for oid, role in ((ALICE, "admin"), (BOB, "owner"), (CAROL, "viewer")):
        p = DEMO_USERS[oid]
        store.upsert_user(oid, p.upn, p.name, role, added_by="seed")
    store.replace_grants(ALICE, [{"scope_type": "all"}])
    store.replace_grants(BOB, [{"scope_type": "project", "scope_value": "fraud-risk", "can_write": True}])
    store.replace_grants(CAROL, [{"scope_type": "cost_centre", "scope_value": "CC-SUP"}])
    plan = [
        ("CC-RISK", "Risk and compliance", BOB, 3000.0, [("project", "fraud-risk", 100)]),
        ("CC-SUP", "Customer support", CAROL, 2500.0, [("project", "customer-care", 100)]),
        ("CC-MKT", "Marketing", "", 1200.0, [("project", "marketing-gen", 100)]),
        ("CC-PLAT", "AI platform", ALICE, 600.0, [("project", "dev-sandbox", 100)]),
    ]
    for code, name, owner, budget, rules in plan:
        cc = store.save_cost_centre(code, name, owner, "", budget)
        for kind, value, share in rules:
            store.add_rule(cc["id"], kind, value, share_percent=share)
    store.save_notification_rule({"name": "Risk: alert events", "scope_type": "project", "scope_value": "fraud-risk",
                                  "trigger": "event", "channel": "log"}, "seed")
    store.save_notification_rule({"name": "Anything over 90 %", "scope_type": "all", "scope_value": "",
                                  "trigger": "percent_used", "threshold": 90, "channel": "log"}, "seed")
