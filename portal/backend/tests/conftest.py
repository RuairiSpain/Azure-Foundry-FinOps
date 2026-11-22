from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from foundry_finops_portal import PortalSettings, create_app
from foundry_finops_portal.db import Store

ALICE = {"X-Demo-User": "11111111-1111-1111-1111-111111111111"}   # admin
BOB = {"X-Demo-User": "22222222-2222-2222-2222-222222222222"}     # owner, project fraud-risk, can write
CAROL = {"X-Demo-User": "33333333-3333-3333-3333-333333333333"}   # viewer, cost centre CC-SUP
DAVE = {"X-Demo-User": "44444444-4444-4444-4444-444444444444"}    # signed in but not added


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def client(store: Store) -> TestClient:
    return TestClient(create_app(PortalSettings(mode="demo", db_path=":memory:"), store=store))
