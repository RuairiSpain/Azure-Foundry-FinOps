from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from foundry_finops_portal.auth import Authenticator, EntraTokenValidator, TokenError

TENANT, CLIENT = "tenant-1", "client-1"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(**over):
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": f"api://{CLIENT}", "tid": TENANT,
              "oid": "oid-1", "preferred_username": "a@contoso.com", "name": "A", "scp": "access_as_user other",
              "exp": int(time.time()) + 600, "iat": int(time.time())}
    claims.update(over)
    key = claims.pop("_key", KEY)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256")


@pytest.fixture
def validator():
    return EntraTokenValidator(TENANT, CLIENT, "access_as_user", key_resolver=lambda t: KEY.public_key())


def test_a_valid_token_gives_a_principal(validator):
    p = validator.validate(token())
    assert (p.oid, p.upn, p.name, p.tenant_id) == ("oid-1", "a@contoso.com", "A", TENANT)
    assert "token" not in repr(p)  # the access token never shows up in logs


def test_v1_issuer_and_plain_client_id_audience_are_accepted(validator):
    assert validator.validate(token(iss=f"https://sts.windows.net/{TENANT}/", aud=CLIENT)).oid == "oid-1"


@pytest.mark.parametrize("over,message", [
    ({"aud": "api://someone-else"}, "Invalid"),
    ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "Invalid"),
    ({"exp": int(time.time()) - 3600}, "Invalid"),
    ({"tid": "other"}, "another tenant"),
    ({"scp": "other"}, "scope"),
    ({"scp": None}, "scope"),
    ({"oid": None}, "object ID"),
    ({"_key": OTHER}, "Invalid"),
])
def test_bad_tokens_are_refused(validator, over, message):
    with pytest.raises(TokenError, match=message):
        validator.validate(token(**over))


def test_an_unsigned_token_is_refused(validator):
    unsigned = jwt.encode({"aud": f"api://{CLIENT}", "oid": "x"}, None, algorithm="none")
    with pytest.raises(TokenError):
        validator.validate(unsigned)


def test_key_download_failure_does_not_accept_the_token():
    def broken(_):
        raise RuntimeError("no network")

    with pytest.raises(TokenError, match="Could not verify"):
        EntraTokenValidator(TENANT, CLIENT, key_resolver=broken).validate(token())


class Req:
    def __init__(self, headers):
        self.headers = headers


def test_bearer_header_is_required(validator):
    auth = Authenticator(validator)
    for headers in ({}, {"authorization": "Basic abc"}, {"authorization": "Bearer "}):
        with pytest.raises(HTTPException) as e:
            auth(Req(headers))
        assert e.value.status_code == 401
    assert auth(Req({"authorization": f"Bearer {token()}"})).oid == "oid-1"


def test_demo_sign_in_needs_a_known_user_and_is_never_on_by_accident():
    with pytest.raises(ValueError):
        Authenticator(None, demo=False)
    demo = Authenticator(None, demo=True)
    with pytest.raises(HTTPException):
        demo(Req({"x-demo-user": "not-a-demo-user"}))
    assert demo(Req({"x-demo-user": "11111111-1111-1111-1111-111111111111"})).name == "Alice Admin"
