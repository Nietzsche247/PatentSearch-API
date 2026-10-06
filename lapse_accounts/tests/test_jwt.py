"""Supabase JWT verification against a locally generated key pair standing in for the project's JWKS."""
import pytest

from lapse_accounts import supabase_jwt
from lapse_accounts.tests.conftest import ISSUER, make_token


def test_valid_token_returns_claims(keypair):
    token = make_token(keypair, sub="11111111-2222-3333-4444-555555555555", email="a@example.test")
    claims = supabase_jwt.verify(token)
    assert claims["sub"] == "11111111-2222-3333-4444-555555555555"
    assert claims["email"] == "a@example.test"
    assert claims["aud"] == "authenticated"


def test_expired_token_rejected(keypair):
    token = make_token(keypair, exp_in=-120)
    with pytest.raises(supabase_jwt.TokenError) as exc:
        supabase_jwt.verify(token)
    assert "expired" in exc.value.reason.lower()


def test_wrong_audience_rejected(keypair):
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify(make_token(keypair, aud="anon"))


def test_wrong_issuer_rejected(keypair):
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify(make_token(keypair, iss="https://other.supabase.co/auth/v1"))


def test_anonymous_and_unverified_rejected(keypair):
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify(make_token(keypair, extra={"is_anonymous": True}))
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify(make_token(keypair, extra={"user_metadata": {"email_verified": False}}))
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify(make_token(keypair, role="service_role"))


def test_token_signed_by_another_key_rejected(keypair):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    other = ec.generate_private_key(ec.SECP256R1())
    pem = other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    token = make_token(keypair, private_pem=pem)  # same kid, different key
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify(token)
    unknown = make_token(keypair, kid="no-such-kid")
    with pytest.raises(supabase_jwt.TokenError) as exc:
        supabase_jwt.verify(unknown)
    assert "unknown key" in exc.value.reason


def test_hs256_and_garbage_rejected(keypair):
    import jwt

    hs = jwt.encode({"sub": "x", "aud": "authenticated", "iss": ISSUER, "exp": 9999999999}, "shared-secret", algorithm="HS256")
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify(hs)
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify("not.a.jwt.at.all")
    with pytest.raises(supabase_jwt.TokenError):
        supabase_jwt.verify("")


def test_jwks_url_and_issuer_derived_from_project_url():
    assert supabase_jwt.jwks_url() == "https://example-project.supabase.co/auth/v1/.well-known/jwks.json"
    assert supabase_jwt.issuer() == ISSUER


def test_jwks_served_from_cache_when_fetch_fails(keypair, monkeypatch):
    """A Supabase outage after one successful fetch must not stop verification."""
    from django.conf import settings

    settings.SUPABASE_JWKS_STATIC = None
    supabase_jwt.reset_cache()
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            return {"keys": [keypair["jwk"]]}
        raise OSError("connection refused")

    monkeypatch.setattr(supabase_jwt, "_fetch", fake_fetch)
    assert supabase_jwt.verify(make_token(keypair))["aud"] == "authenticated"
    # force a refresh: the fetch fails, the cached keys still verify
    supabase_jwt._state["fetched_at"] = 0.0
    supabase_jwt._state["attempted_at"] = 0.0
    assert supabase_jwt.verify(make_token(keypair))["aud"] == "authenticated"
    assert calls["n"] == 2
    # a fresh process with an empty memory finds the keys in the Django cache without fetching
    supabase_jwt._state.update({"jwks": None, "fetched_at": 0.0, "attempted_at": 0.0})
    assert supabase_jwt.verify(make_token(keypair))["aud"] == "authenticated"
