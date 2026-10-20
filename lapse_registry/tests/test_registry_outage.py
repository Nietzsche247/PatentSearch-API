"""Checklist 5.15 on the registry API: during a registry database (Supabase) outage the public reads are served from the
local read cache and writes answer 503 with Retry-After, failing fast; and 5.14's public JWKS route. Against the local
test Postgres; the outage is a connection string to a port nothing listens on.

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_registry/tests -q -p no:cacheprovider
"""
import json
import time
import uuid

import pytest
from django.conf import settings

from lapse_registry.tests.test_registry_api import post_json, publish

DOWN = "host=127.0.0.1 port=1 dbname=x user=x password=x connect_timeout=2"


@pytest.fixture
def outage(monkeypatch):
    """Call outage() to cut the registry database off; the fixture restores it."""
    from lapse_registry import db

    good = settings.REGISTRY_DATABASE_URL

    def cut():
        monkeypatch.setattr(settings, "REGISTRY_DATABASE_URL", DOWN)
        db.reset()
    yield cut
    monkeypatch.setattr(settings, "REGISTRY_DATABASE_URL", good)
    db.reset()
    db.clear_down()


def test_reads_come_from_the_cache_and_writes_are_503(client, token, submission, outage):
    from lapse_registry import readcache

    user = str(uuid.uuid4())
    t = token(sub=user)
    rec = publish(client, t, submission(title="Outage pump impeller"))
    unread = publish(client, t, submission(title="Never read before the outage"))
    live = client.get(f"/r/{rec['id']}.json")
    assert live.status_code == 200 and "X-Registry-Source" not in live
    assert client.get(f"/registry/disclosures/{rec['id']}/proof").status_code == 200
    assert client.get("/registry/sitemap.xml").status_code == 200

    outage()
    t0 = time.monotonic()
    r = client.get(f"/r/{rec['id']}.json")
    assert r.status_code == 200 and r["X-Registry-Source"] == "cache" and int(r["Age"]) >= 0
    assert json.loads(r.content) == json.loads(live.content) | {"noindex": json.loads(r.content)["noindex"]}
    assert r["X-Robots-Tag"] == "noindex"
    p = client.get(f"/registry/disclosures/{rec['id']}/proof")
    assert p.status_code == 200 and p["X-Registry-Source"] == "cache" and p.json()["bundle_hash"] == rec["bundle_hash"]
    s = client.get("/registry/search", {"q": "outage impeller"})
    assert s.status_code == 200 and s["X-Registry-Source"] == "cache" and [x["id"] for x in s.json()["records"]] == [rec["id"]]
    assert client.get("/registry/sitemap.xml")["X-Registry-Source"] == "cache"
    # what the copy does not hold is 503, never a 404 it cannot know
    r = client.get(f"/r/{unread['id']}.json")
    assert r.status_code == 503 and r["Retry-After"] == "30" and r.json()["code"] == "ERR_UNAVAILABLE"
    # writes: 503 with Retry-After, and nothing stored
    w = post_json(client, "/registry/disclosures", submission(title="Written during the outage"), token())
    assert w.status_code == 503 and w["Retry-After"] == "30"
    assert client.get("/registry/disclosures", HTTP_AUTHORIZATION="Bearer " + t).status_code == 503   # an account's list is not cached
    h = client.get("/registry/health")
    assert h.status_code == 503 and h.json()["database"] == "unreachable" and h.json()["read_cache"]["records"] >= 1
    # fail fast: after the first failed connect, requests do not wait on the database again
    t1 = time.monotonic()
    for _ in range(20):
        assert client.get(f"/r/{rec['id']}.json").status_code == 200
    assert (time.monotonic() - t1) / 20 < 0.1, "cached reads during the outage must not wait on the database"
    assert time.monotonic() - t0 < 30
    assert readcache.figures()["records"] >= 1


def test_the_warm_job_copies_records_nobody_read_and_forgets_hidden_ones(client, token, submission, outage, _env):
    import io

    from django.core.management import call_command

    from lapse_registry import readcache

    rec = publish(client, token(), submission(title="Warm copy target"))
    hidden = publish(client, token(), submission(title="Taken down later"))
    out = io.StringIO()
    call_command("registry_cache_warm", stdout=out)
    got = json.loads(out.getvalue())
    assert got["ok"] and got["records"] >= 2
    assert readcache.record(hidden["id"]) is not None
    import psycopg

    with psycopg.connect(_env.admin_dsn, autocommit=True) as con:
        con.execute("update registry.disclosures set hidden = true, hidden_reason = 'test takedown' where id = %s", (hidden["id"],))
    call_command("registry_cache_warm", stdout=io.StringIO())
    assert readcache.record(hidden["id"]) is None
    outage()
    assert client.get(f"/r/{rec['id']}.json").status_code == 200
    assert client.get(f"/r/{hidden['id']}.json").status_code == 503   # a takedown is not served from the copy
    out = io.StringIO()
    with pytest.raises(SystemExit):
        call_command("registry_cache_warm", stdout=out)
    assert json.loads(out.getvalue())["ok"] is False


def test_a_hidden_record_forgets_the_copy(client, token, submission, _env):
    from lapse_registry import readcache

    rec = publish(client, token(), submission(title="Read once then hidden"))
    assert client.get(f"/r/{rec['id']}.json").status_code == 200 and readcache.record(rec["id"])
    import psycopg
    with psycopg.connect(_env.admin_dsn, autocommit=True) as con:
        con.execute("update registry.disclosures set hidden = true, hidden_reason = 'test takedown' where id = %s", (rec["id"],))
    # registry 2 (checklist 10.9): a hidden record answers its stub (the hash, never the content), 410, and leaves the copy
    r = client.get(f"/r/{rec['id']}.json")
    assert r.status_code == 410 and r.json()["hidden"] is True and r.json()["user_content"] == {}
    assert r.json()["bundle_hash"] == rec["bundle_hash"] and readcache.record(rec["id"]) is None
    assert client.get(f"/r/{'0' * 26}.json").status_code == 404


def test_the_jwks_route(client, monkeypatch, tmp_path):
    import registry_crypto
    from registry_crypto import jws

    r = client.get("/registry/.well-known/jwks.json")
    assert r.status_code == 501 and b"registry_crypto" not in r.content     # no keyring yet: not available
    monkeypatch.setenv("REGISTRY_KEYS_DIR", str(tmp_path / "keys"))
    registry_crypto.init()
    first = registry_crypto.sign_receipt({"n": 1})
    registry_crypto.rotate()
    r = client.get("/registry/.well-known/jwks.json")
    assert r.status_code == 200 and r["Content-Type"] == "application/jwk-set+json"
    assert r["Cache-Control"] == "public, max-age=300" and r["Access-Control-Allow-Origin"] == "*"
    keyset = json.loads(r.content)
    assert [k["status"] for k in keyset["keys"]] == ["active", "retired"] and all("d" not in k for k in keyset["keys"])
    assert jws.verify_with_jwks(first, keyset) == {"n": 1}
    assert client.get("/registry/health").json()["keys"] == "present"
