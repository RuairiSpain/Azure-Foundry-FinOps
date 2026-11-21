"""Every Microsoft call named in ``foundry_finops.migration.MAP`` exists in the installed Microsoft packages."""

from __future__ import annotations

import importlib
import unittest

from azure.core.credentials import AccessToken

from foundry_finops import _client
from foundry_finops.migration import MAP, table


class Cred:
    def get_token(self, *scopes, **kwargs):
        return AccessToken("t", 9_999_999_999)


def _client_for(path: str):
    module, name = path.rsplit(".", 1)
    cls = getattr(importlib.import_module(module), name)
    for args in ((Cred(), "sub-1"), (Cred(),), ("workspace-ignored",)):
        try:
            return cls(*args)
        except TypeError:
            continue
    raise AssertionError(f"could not construct {path}")


class MigrationMapTests(unittest.TestCase):
    def test_every_official_call_exists(self):
        checked = 0
        for e in MAP:
            if not e.resolved:
                continue
            with self.subTest(ours=e.ours, official=e.official):
                try:
                    client = _client_for(e.client)
                except ImportError:
                    self.skipTest(f"{e.package} is not installed")
                obj = client
                for part in e.official.split("."):
                    obj = getattr(obj, part)
                self.assertTrue(callable(obj), e.official)
                checked += 1
        self.assertGreater(checked, 30)

    def test_our_side_names_exist(self):
        """The ``client.<group>.<method>`` on the left is a real attribute of FinOpsClient."""
        client = _client.FinOpsClient(Cred(), "sub-1")
        for e in MAP:
            if not e.ours.startswith("client."):
                continue
            group, _, methods = e.ours.removeprefix("client.").partition(".")
            for g in (g.strip() for g in group.split("/")):
                self.assertTrue(hasattr(client, g), e.ours)
            if "/" in group or methods in ("", "*"):
                continue
            for m in (m.strip() for m in methods.split("/")):
                self.assertTrue(hasattr(getattr(client, group), m), e.ours)

    def test_table_renders(self):
        self.assertIn("| `client.cost.query` |", table())
