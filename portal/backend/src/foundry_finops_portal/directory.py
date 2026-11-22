"""Checking that a person exists in Entra ID before the portal lets an administrator add them."""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Any, Protocol

import requests

_GUID = re.compile(r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")
_UPN = re.compile(r"^[^@\s/\\?#]+@[^@\s/\\?#]+\.[^@\s/\\?#]+$")


@dataclass(frozen=True)
class DirectoryUser:
    oid: str
    upn: str
    display_name: str
    enabled: bool = True
    department: str = ""


class Directory(Protocol):
    def lookup(self, identifier: str) -> DirectoryUser | None:
        """Find a user by object ID or user principal name. ``None`` if Entra has no such user."""


def is_valid_identifier(identifier: str) -> bool:
    return bool(_GUID.match(identifier) or _UPN.match(identifier))


class GraphDirectory:
    """Microsoft Graph lookup with the portal's own identity (it needs ``User.Read.All`` as an application permission)."""

    SCOPE = "https://graph.microsoft.com/.default"

    def __init__(self, credential: Any, session: requests.Session | None = None) -> None:
        self._credential = credential
        self._session = session or requests.Session()

    def lookup(self, identifier: str) -> DirectoryUser | None:
        if not is_valid_identifier(identifier):
            return None  # never put free text into the Graph URL
        token = self._credential.get_token(self.SCOPE).token
        url = f"https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(identifier, safe='')}"
        resp = self._session.get(url, params={"$select": "id,userPrincipalName,displayName,accountEnabled,department"},
                                 headers={"Authorization": f"Bearer {token}"}, timeout=20)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        d = resp.json()
        return DirectoryUser(d["id"], d.get("userPrincipalName", ""), d.get("displayName") or "",
                             bool(d.get("accountEnabled", True)), (d.get("department") or "").strip())


class StaticDirectory:
    """A fixed list of people, for demo mode and tests."""

    def __init__(self, users: list[DirectoryUser]) -> None:
        self._users = users

    def lookup(self, identifier: str) -> DirectoryUser | None:
        if not is_valid_identifier(identifier):
            return None
        needle = identifier.lower()
        return next((u for u in self._users if needle in (u.oid.lower(), u.upn.lower())), None)
