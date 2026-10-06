"""Test fixtures for the account endpoints (gate 5.10).

Runs without Supabase, Redis, MySQL or Elasticsearch: a temporary Django SQLite DB, a three-row search
database in the Lapse layout, and a locally generated P-256 key pair whose public half is handed to the
verifier as a static JWKS (`SUPABASE_JWKS_STATIC`). Run from the fork root:

  python -m pytest lapse_accounts/tests -q -p no:cacheprovider
"""
import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_TMP = Path(tempfile.mkdtemp(prefix="lapse_accounts_test_"))
os.environ["DJANGO_SETTINGS_MODULE"] = "pvapi.settings.lapse_local"
os.environ["LAPSE_DATA_DIR"] = str(_TMP)
os.environ["LAPSE_DJANGO_DB"] = str(_TMP / "django.sqlite3")
os.environ["LAPSE_SQLITE_PATH"] = str(_TMP / "search.db")
os.environ["LAPSE_USAGE_LOG"] = "none"
os.environ["SUPABASE_URL"] = "https://example-project.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "anon-key-for-tests"
os.environ["LAPSE_FREE_MONTHLY_LIMIT"] = "5"
os.environ["LAPSE_FREE_MINUTE_LIMIT"] = "45"

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402

ISSUER = "https://example-project.supabase.co/auth/v1"


def _build_search_db(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    for field, t, kw in [("patent_id", "keyword", 0), ("patent_zero_prefix", "keyword", 0), ("patent_title", "text", 1),
                         ("patent_date", "date", 0), ("patent_type", "keyword", 0), ("withdrawn", "boolean", 0)]:
        con.execute("INSERT INTO _lapse_fields VALUES ('patents','',?,?,?)", (field, t, kw))
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, patent_type TEXT, withdrawn INT)")
    con.executemany("INSERT INTO patents VALUES (?,?,?,?,?,?)", [
        ("10000000", "10000000", "Coherent LADAR using intra-pixel quadrature detection", "2018-06-19", "utility", 0),
        ("10000001", "10000001", "Injection molding machine and mold thickness control method", "2018-06-19", "utility", 0),
        ("10000002", "10000002", "Method for manufacturing polymer film", "2018-06-19", "utility", 0),
    ])
    con.commit()
    con.close()


@pytest.fixture(scope="session", autouse=True)
def _django_db():
    call_command("migrate", verbosity=0, interactive=False)
    call_command("createcachetable", verbosity=0)
    _build_search_db(os.environ["LAPSE_SQLITE_PATH"])
    yield


@pytest.fixture(scope="session")
def keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from jwt.algorithms import ECAlgorithm

    private = ec.generate_private_key(ec.SECP256R1())
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    jwk = json.loads(ECAlgorithm.to_jwk(private.public_key()))
    jwk.update({"kid": "test-kid-1", "use": "sig", "alg": "ES256"})
    return {"private_pem": pem, "jwk": jwk}


@pytest.fixture(autouse=True)
def static_jwks(keypair, settings_jwks_reset):
    settings.SUPABASE_JWKS_STATIC = {"keys": [keypair["jwk"]]}
    yield
    settings.SUPABASE_JWKS_STATIC = None


@pytest.fixture
def settings_jwks_reset():
    from lapse_accounts import supabase_jwt

    supabase_jwt.reset_cache()
    yield
    supabase_jwt.reset_cache()


def make_token(keypair, sub=None, email=None, aud="authenticated", role="authenticated", exp_in=3600,
               iss=ISSUER, kid="test-kid-1", extra=None, private_pem=None):
    import jwt

    now = int(time.time())
    sub = sub or str(uuid.uuid4())
    claims = {
        "iss": iss, "sub": sub, "aud": aud, "role": role, "email": email or f"{sub[:8]}@example.test",
        "iat": now, "exp": now + exp_in, "session_id": str(uuid.uuid4()), "is_anonymous": False,
        "app_metadata": {"provider": "email", "providers": ["email"]},
        "user_metadata": {"email": email or f"{sub[:8]}@example.test", "email_verified": True},
    }
    if extra:
        claims.update(extra)
    return jwt.encode(claims, private_pem or keypair["private_pem"], algorithm="ES256", headers={"kid": kid})


@pytest.fixture
def token_factory(keypair):
    def _make(**kw):
        return make_token(keypair, **kw)

    return _make


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def user_key(client, token_factory):
    """A fresh Supabase user (token) with a freshly minted Free key: (token, key value, prefix)."""
    sub = str(uuid.uuid4())
    token = token_factory(sub=sub, email=f"user-{sub[:8]}@example.test")
    r = client.post("/api/v1/meta/keys/", data="{}", content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token)
    assert r.status_code == 201, r.content
    body = r.json()
    return token, body["api_key"], body["prefix"]
