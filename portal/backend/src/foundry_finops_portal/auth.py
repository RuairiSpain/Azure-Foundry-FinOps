"""Who is calling: Entra ID access-token validation (production) and a pick-a-user sign-in (demo mode only)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import HTTPException, Request


@dataclass(frozen=True)
class Principal:
    """The signed-in person. ``token`` is the access token the browser sent; it is kept in memory for the on-behalf-of
    exchange and never stored or logged."""

    oid: str
    upn: str
    name: str
    tenant_id: str = ""
    token: str = field(default="", repr=False, compare=False)


class TokenError(Exception):
    """The bearer token is missing or not acceptable."""


class EntraTokenValidator:
    """Validates a v1.0 or v2.0 access token issued by one Entra tenant for this API.

    Checks the RS256 signature against the tenant's published keys, the issuer, the audience (the app registration's
    client ID or ``api://<client-id>``), the expiry, the tenant, and that the delegated scope ``scp`` contains the
    scope this API requires. ``key_resolver`` returns the public key for a token; tests give it a fixed key.
    """

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        required_scope: str = "access_as_user",
        key_resolver: Callable[[str], Any] | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.audiences = [client_id, f"api://{client_id}"]
        self.issuers = [f"https://login.microsoftonline.com/{tenant_id}/v2.0", f"https://sts.windows.net/{tenant_id}/"]
        self.required_scope = required_scope
        if key_resolver is None:
            client = jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True)
            key_resolver = lambda token: client.get_signing_key_from_jwt(token).key  # noqa: E731
        self._key = key_resolver

    def validate(self, token: str) -> Principal:
        try:
            key = self._key(token)
            claims = jwt.decode(
                token, key, algorithms=["RS256"], audience=self.audiences, issuer=self.issuers,
                options={"require": ["exp", "iss", "aud"]}, leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise TokenError(f"Invalid access token: {exc}") from exc
        except Exception as exc:  # key download failures and the like: don't accept the token
            raise TokenError(f"Could not verify the access token: {exc}") from exc
        if claims.get("tid") != self.tenant_id:
            raise TokenError("The token is from another tenant")
        scopes = str(claims.get("scp", "")).split()
        if self.required_scope not in scopes:
            raise TokenError(f"The token lacks the {self.required_scope!r} scope")
        oid = claims.get("oid")
        if not oid:
            raise TokenError("The token has no object ID (oid); app-only tokens are not accepted")
        upn = claims.get("preferred_username") or claims.get("upn") or claims.get("email") or ""
        return Principal(oid=str(oid), upn=str(upn), name=str(claims.get("name") or upn), tenant_id=self.tenant_id,
                         token=token)


#: Demo-mode people. Their object IDs are fixed so the demo database can grant them scopes.
DEMO_USERS: dict[str, Principal] = {
    "11111111-1111-1111-1111-111111111111": Principal("11111111-1111-1111-1111-111111111111",
                                                      "alice.admin@contoso.example", "Alice Admin"),
    "22222222-2222-2222-2222-222222222222": Principal("22222222-2222-2222-2222-222222222222",
                                                      "bob.risk@contoso.example", "Bob (owner, Risk)"),
    "33333333-3333-3333-3333-333333333333": Principal("33333333-3333-3333-3333-333333333333",
                                                      "carol.care@contoso.example", "Carol (viewer, Support)"),
    "44444444-4444-4444-4444-444444444444": Principal("44444444-4444-4444-4444-444444444444",
                                                      "dave.nobody@contoso.example", "Dave (not added to the portal)"),
}


class Authenticator:
    """Turns a request into a :class:`Principal`, or raises ``HTTPException(401)``."""

    def __init__(self, validator: EntraTokenValidator | None, demo: bool = False) -> None:
        if validator is None and not demo:
            raise ValueError("Sign-in needs a token validator unless demo mode is on")
        self.validator = validator
        self.demo = demo

    def __call__(self, request: Request) -> Principal:
        if self.demo:
            oid = request.headers.get("x-demo-user", "")
            user = DEMO_USERS.get(oid)
            if user is None:
                raise HTTPException(401, "Pick a demo user to sign in")
            return user
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})
        assert self.validator is not None
        try:
            return self.validator.validate(token.strip())
        except TokenError as exc:
            raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
