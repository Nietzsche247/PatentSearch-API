"""Fixtures for the registry API tests (PatentRef checklist phase 10).

Runs without Supabase: a temporary Django SQLite app DB, a locally generated P-256 key pair standing in for the
project's JWKS (SUPABASE_JWKS_STATIC, as lapse_accounts/tests does), and a throwaway local Postgres with the registry
migrations applied, started by patentref_registry.pgtest from the patentref checkout (LAPSE_REPO_DIR, else a sibling
checkout that has registry/py). Never a Supabase project. Run from the fork root, on its own (its settings differ from
the account tests'):

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_registry/tests -q -p no:cacheprovider
"""
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def find_repo_dir():
    if os.environ.get("LAPSE_REPO_DIR"):
        return os.environ["LAPSE_REPO_DIR"]
    for cand in sorted(ROOT.parent.iterdir()) + [Path("/srv/lapse")]:
        if (cand / "registry" / "py" / "patentref_registry" / "service.py").is_file():
            return str(cand)
    return None


REPO_DIR = find_repo_dir()
_TMP = Path(tempfile.mkdtemp(prefix="lapse_registry_test_"))
os.environ["DJANGO_SETTINGS_MODULE"] = "pvapi.settings.registry"
os.environ["LAPSE_DATA_DIR"] = str(_TMP)
os.environ["LAPSE_DJANGO_DB"] = str(_TMP / "django.sqlite3")
os.environ["LAPSE_SQLITE_PATH"] = str(_TMP / "search.db")
os.environ["LAPSE_USAGE_LOG"] = "none"
os.environ["SUPABASE_URL"] = "https://example-project.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "anon-key-for-tests"
os.environ["REGISTRY_PUBLIC_BASE"] = "https://patentref.io"
os.environ["REGISTRY_KEYS_DIR"] = str(_TMP / "keys")   # never the box's keyring; tests that need keys make them here
if REPO_DIR:
    os.environ["LAPSE_REPO_DIR"] = REPO_DIR
    sys.path.insert(0, str(Path(REPO_DIR) / "registry" / "py"))
    sys.path.insert(0, str(Path(REPO_DIR) / "tests" / "registry" / "stubs"))

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.test import Client  # noqa: E402

ISSUER = "https://example-project.supabase.co/auth/v1"


@pytest.fixture(scope="session", autouse=True)
def _env():
    if not REPO_DIR:
        pytest.skip("no patentref checkout with registry/py (set LAPSE_REPO_DIR)")
    pytest.importorskip("psycopg")
    from patentref_registry import pgtest

    call_command("migrate", verbosity=0, interactive=False)
    call_command("createcachetable", verbosity=0)
    c = pgtest.cluster()
    if c is None:
        pytest.skip("no local Postgres for the registry tests")
    settings.REGISTRY_DATABASE_URL = c.api_dsn
    import psycopg
    with psycopg.connect(c.admin_dsn, autocommit=True) as con:   # a new account's lower cap is off for these tests
        con.execute("update registry.limits set value = 0 where name = 'new_account_days'")
    yield c
    from lapse_registry import db
    db.reset()
    c.close()


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
def static_jwks(keypair):
    from lapse_accounts import supabase_jwt

    supabase_jwt.reset_cache()
    settings.SUPABASE_JWKS_STATIC = {"keys": [keypair["jwk"]]}
    yield
    settings.SUPABASE_JWKS_STATIC = None
    supabase_jwt.reset_cache()


def make_token(keypair, sub=None, email_verified=True, exp_in=3600, metadata=None):
    import jwt

    now = int(time.time())
    sub = sub or str(uuid.uuid4())
    claims = {"iss": ISSUER, "sub": sub, "aud": "authenticated", "role": "authenticated", "email": f"{sub[:8]}@example.test",
              "iat": now, "exp": now + exp_in, "session_id": str(uuid.uuid4()), "is_anonymous": False,
              "app_metadata": {"provider": "email", "providers": ["email"]},
              "user_metadata": dict({"email": f"{sub[:8]}@example.test", "email_verified": email_verified}, **(metadata or {}))}
    return jwt.encode(claims, keypair["private_pem"], algorithm="ES256", headers={"kid": "test-kid-1"})


@pytest.fixture
def token(keypair):
    def _make(**kw):
        return make_token(keypair, **kw)
    return _make


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def submission():
    def _make(**kw):
        from lapse_registry.tests.conftest_text import words   # distinct text per record, so the near-duplicate rule (10.10) stays out of the way

        from patentref_registry import terms

        p = {"title": "Pool heater bypass with a thermal fuse", "abstract": words(30), "body_text": words(120),
             "cpc_guess": ["F24H 9/20"],
             "inventors_as_entered": ["A. Example"], "inventor_consent": "inventor-consent-1",
             "terms_version": terms.VERSION}   # the publish form's terms box (checklist 5.11); the gate's tests drop it
        p.update(kw)
        return p
    return _make


@pytest.fixture(autouse=True)
def generous_limits(monkeypatch):
    """Every test client is 127.0.0.1; the rate-limit test sets its own figures."""
    monkeypatch.setattr(settings, "REGISTRY_WRITE_PER_MINUTE", 100000)
    monkeypatch.setattr(settings, "REGISTRY_READ_PER_MINUTE", 100000)
