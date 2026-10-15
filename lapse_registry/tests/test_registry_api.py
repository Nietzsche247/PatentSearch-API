"""The registry API routes (PatentRef checklist 10.1 Django side, 10.2, 10.10, 10.11; the 10.3 and 10.4 hooks) against a
local Postgres and a stand-in JWKS.

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_registry/tests -q -p no:cacheprovider
"""
import json
import uuid

import pytest
from django.conf import settings

from API import lapse_wording


def post_json(client, path, body, token=None, **extra):
    if token:
        extra["HTTP_AUTHORIZATION"] = "Bearer " + token
    return client.post(path, data=json.dumps(body), content_type="application/json", **extra)


def publish(client, token, body):
    r = post_json(client, "/registry/disclosures", body, token)
    assert r.status_code == 201, r.content
    return r.json()


def ours(rec):
    """The parts of a response we wrote (everything but the user-content block), for the wording check."""
    return json.dumps({k: v for k, v in rec.items() if k != "user_content"})


def test_publish_and_read_back_anonymously(client, token, submission):
    from patentref_registry import bundle

    user = str(uuid.uuid4())
    rec = publish(client, token(sub=user), submission())
    assert rec["permalink"].startswith("https://patentref.io/r/") and rec["owner"]["consent"]["text_id"] == "inventor-consent-1"
    r = client.get(f"/r/{rec['id']}.json")
    assert r.status_code == 200 and r["Cache-Control"].startswith("public") and r["X-Robots-Tag"] == "noindex"
    pub = r.json()
    assert "owner" not in pub and pub["user_content"]["title"] == "Pool heater bypass with a thermal fuse"
    assert all(ok for _, ok, _ in bundle.verify_record(pub))
    assert client.get(f"/registry/disclosures/{rec['id']}").json()["bundle_hash"] == rec["bundle_hash"]
    # the owner's own view needs the owner's token
    own = client.get(f"/registry/disclosures/{rec['id']}", HTTP_AUTHORIZATION="Bearer " + token(sub=user)).json()
    assert "owner" in own
    other = client.get(f"/registry/disclosures/{rec['id']}", HTTP_AUTHORIZATION="Bearer " + token()).json()
    assert "owner" not in other
    assert lapse_wording.check_banned(ours(pub)) == []


def test_auth_is_the_supabase_jwt(client, token, submission):
    r = post_json(client, "/registry/disclosures", submission())
    assert r.status_code == 401 and r["X-Status-Reason-Code"] == "ERR_AUTH"
    r = post_json(client, "/registry/disclosures", submission(), "not.a.token")
    assert r.status_code == 401
    r = post_json(client, "/registry/disclosures", submission(), token(email_verified=False))
    assert r.status_code == 401 and "confirmed" in r.json()["reason"]
    r = post_json(client, "/registry/disclosures", submission(), token(exp_in=-120))
    assert r.status_code == 401
    assert client.get("/registry/disclosures").status_code == 401


def test_pii_and_consent(client, token, submission):
    t = token()
    body = submission(body_text="Made at 1234 W. Example Lane, Springfield, IL 62704. Call (555) 010-0123.")
    pre = post_json(client, "/registry/disclosures/check", body, t)
    assert pre.status_code == 200 and {"street_address", "postal_address", "phone"} <= {f["kind"] for f in pre.json()["pii_findings"]}
    assert pre.json()["consent"]["text_id"] == "inventor-consent-1"
    r = post_json(client, "/registry/disclosures", body, t)
    assert r.status_code == 422 and r.json()["code"] == "ERR_PII" and r.json()["pii_findings"]
    r = post_json(client, "/registry/disclosures", dict(body, pii_acknowledged=True), t)
    assert r.status_code == 201
    r = post_json(client, "/registry/disclosures", submission(inventor_consent=""), t)
    assert r.status_code == 400 and r.json()["code"] == "ERR_CONSENT"
    r = post_json(client, "/registry/disclosures", submission(title=""), t)
    assert r.status_code == 400 and r.json()["field"] == "title"


def test_multipart_with_a_file_and_the_download(client, token, submission):
    from django.core.files.uploadedfile import SimpleUploadedFile

    t = token()
    data = b"<html><script>alert(1)</script></html>"
    form = {k: v for k, v in submission().items() if k not in ("cpc_guess", "inventors_as_entered")}
    form.update(cpc_guess="F24H 9/20\nE04H 4/12", inventors_as_entered="A. Example; B. Sample",
                file=SimpleUploadedFile("page.html", data, content_type="text/html"))
    r = client.post("/registry/disclosures", data=form, HTTP_AUTHORIZATION="Bearer " + t)
    assert r.status_code == 201, r.content
    rec = r.json()
    assert rec["user_content"]["cpc_guess"] == ["F24H 9/20", "E04H 4/12"]
    assert rec["user_content"]["inventors_as_entered"] == ["A. Example", "B. Sample"]
    assert rec["file"]["media_type"] == "text/html" and rec["file"]["url"].endswith(f"/r/{rec['id']}/file")
    d = client.get(f"/r/{rec['id']}/file")
    assert d.status_code == 200 and d.content == data
    assert d["Content-Type"] == "application/octet-stream"  # never served as html from our domain
    assert d["Content-Disposition"].startswith('attachment; filename="page.html"')
    assert d["X-Content-Type-Options"] == "nosniff" and "sandbox" in d["Content-Security-Policy"]
    import hashlib
    assert hashlib.sha256(d.content).hexdigest() == rec["user_content"]["bundle"]["file"]["sha256"]


def test_version_chain_and_mine(client, token, submission):
    user = str(uuid.uuid4())
    t = token(sub=user)
    v1 = publish(client, t, submission())
    v2 = publish(client, t, submission(body_text="Second. " * 30, prev_version_id=v1["id"]))
    assert v2["version"] == 2 and v2["root_id"] == v1["id"]
    r = post_json(client, "/registry/disclosures", submission(prev_version_id=v1["id"]), t)
    assert r.status_code == 409
    r = post_json(client, "/registry/disclosures", submission(prev_version_id=v2["id"]), token())
    assert r.status_code == 404
    mine = client.get("/registry/disclosures", HTTP_AUTHORIZATION="Bearer " + t).json()
    assert [x["id"] for x in mine["records"]] == [v2["id"], v1["id"]]


def test_proof_search_and_hooks(client, token, submission):
    t = token()
    word = "quartzflow" + uuid.uuid4().hex[:5]
    rec = publish(client, t, submission(title=f"Skimmer {word} lid"))
    p = client.get(f"/registry/disclosures/{rec['id']}/proof").json()
    assert p["proof_status"] == "pending" and p["proof"]["hash"] == rec["bundle_hash"]
    s = client.get("/registry/search", {"q": word}).json()
    assert [x["id"] for x in s["records"]] == [rec["id"]]
    for path in (f"/registry/disclosures/{rec['id']}/receipt", f"/registry/disclosures/{rec['id']}/seal"):
        r = client.post(path, HTTP_AUTHORIZATION="Bearer " + t)
        assert r.status_code == 501 and r.json()["code"] == "ERR_NOT_YET", path
    r = client.get(f"/registry/disclosures/{rec['id']}/sealed", HTTP_AUTHORIZATION="Bearer " + t)
    assert r.status_code == 501
    r = post_json(client, "/registry/disclosures", submission(visibility="sealed"), t)
    assert r.status_code == 501


def test_sealed_and_receipt_hooks_when_switched_on(client, token, submission, monkeypatch):
    import registry_crypto_stub as stub

    from lapse_registry import receipt_search, views
    monkeypatch.setattr(settings, "REGISTRY_SEALED_ENABLED", True)
    monkeypatch.setattr(settings, "REGISTRY_RECEIPTS_ENABLED", True)
    monkeypatch.setattr(views, "_keys", lambda: stub)
    monkeypatch.setattr(views, "_keys_or_none", lambda: stub)
    user = str(uuid.uuid4())
    t = token(sub=user)
    sealed = publish(client, t, submission(visibility="sealed", title="Sealed valve"))
    pub = client.get(f"/r/{sealed['id']}.json").json()
    assert pub["user_content"] == {} and "Sealed valve" not in json.dumps(pub)
    out = client.get(f"/registry/disclosures/{sealed['id']}/sealed", HTTP_AUTHORIZATION="Bearer " + t).json()
    from patentref_registry import canonical
    assert canonical.verify_hash(out["bundle_canonical"], pub["bundle_hash"])
    rec = publish(client, t, submission())
    monkeypatch.setattr(receipt_search, "similar", lambda text, n: {
        "data_version": "20261013.2", "as_of": "2026-10-13", "terms": ["fuse"],
        "neighbors": [{"rank": 1, "patent_id": "10000000", "patent_title": "T", "patent_abstract": "A", "patent_date": "2018-06-19",
                       "score": 3.14159}]})
    r = client.post(f"/registry/disclosures/{rec['id']}/receipt", HTTP_AUTHORIZATION="Bearer " + t)
    assert r.status_code == 201, r.content
    assert stub.verify_receipt(r.json()["signature"])["neighbors"][0]["score"] == "3.1416"


def test_limits_body_cap_and_rate(client, token, submission, monkeypatch):
    t = token()
    monkeypatch.setattr(settings, "REGISTRY_MAX_BODY", 1000)
    r = post_json(client, "/registry/disclosures", submission(body_text="x" * 2000), t)
    assert r.status_code == 413 and r.json()["code"] == "ERR_SIZE"
    monkeypatch.setattr(settings, "REGISTRY_MAX_BODY", 25 * 1024 * 1024)
    monkeypatch.setattr(settings, "REGISTRY_READ_PER_MINUTE", 2)
    ip = {"REMOTE_ADDR": "198.51.100." + str(uuid.uuid4().int % 250)}
    codes = [client.get("/registry/search", {"q": "x"}, **ip).status_code for _ in range(3)]
    assert codes[:2] == [200, 200] and codes[2] == 429
    r = client.get("/registry/search", {"q": "x"}, **ip)
    assert r.status_code == 429 and int(r["Retry-After"]) >= 1


def test_database_down_is_503_and_health(client, token, submission, monkeypatch):
    from lapse_registry import db

    assert client.get("/registry/health").json()["database"] == "ok"
    good = settings.REGISTRY_DATABASE_URL
    monkeypatch.setattr(settings, "REGISTRY_DATABASE_URL", "host=127.0.0.1 port=1 dbname=x user=x password=x connect_timeout=2")
    db.reset()
    r = post_json(client, "/registry/disclosures", submission(), token())
    assert r.status_code == 503 and r.json()["code"] == "ERR_UNAVAILABLE" and r["Retry-After"] == "30"
    h = client.get("/registry/health")
    assert h.status_code == 503 and h.json()["database"] == "unreachable"
    monkeypatch.setattr(settings, "REGISTRY_DATABASE_URL", good)
    db.reset()
    assert client.get("/registry/health").json() == {"ok": True, "database": "ok", "keys": "absent",
                                                      "receipts_enabled": False, "sealed_enabled": False}
    # the ping leaves no transaction open, so the next publish commits on its own (read back on a fresh connection)
    assert db.connection().info.transaction_status == 0
    rec = publish(client, token(), submission())
    db.reset()
    assert client.get(f"/r/{rec['id']}.json").status_code == 200


def test_only_registry_routes_are_served(client):
    for path in ("/api/v1/patent/", "/api/v1/meta/signup/", "/admin/", "/registry/", "/r/notanid.json"):
        r = client.get(path)
        assert r.status_code == 404 and r.json()["code"] == "ERR_NOT_FOUND", path
    assert client.get("/r/01K7F2N9Q3A8B4C5D6E7F8G9H0.json").status_code == 404


def test_abuse_rules_over_http(client, token, submission, monkeypatch):
    """Checklist 10.10 through the HTTP layer: content rules, duplicates, disposable domains, the per-address day cap."""
    t = token()
    r = post_json(client, "/registry/disclosures", submission(body_text="Buy now at https://shop.example/x"), t)
    assert r.status_code == 422 and r.json()["code"] == "ERR_SPAM" and r.json()["rule"] == "promotion"
    pre = post_json(client, "/registry/disclosures/check", submission(title="See https://x.example/"), t).json()
    assert pre["content_rules"]["ok"] is False and pre["content_rules"]["rule"] == "title_link"
    first = publish(client, t, submission())
    copy_ = {k: first["user_content"][k] for k in ("title", "abstract", "body_text")}
    r = post_json(client, "/registry/disclosures", submission(**copy_), token())
    assert r.status_code == 409 and r.json()["code"] == "ERR_DUPLICATE" and r.json()["duplicate_of"] == first["id"]
    monkeypatch.setattr(settings, "REGISTRY_PUBLISH_PER_ADDRESS_DAY", 2)
    ip = {"REMOTE_ADDR": "203.0.113." + str(uuid.uuid4().int % 250)}
    codes = [post_json(client, "/registry/disclosures", submission(), token(), **ip).status_code for _ in range(3)]
    assert codes == [201, 201, 429]
    # a refused record does not use the address's allowance
    ip2 = {"REMOTE_ADDR": "203.0.113." + str((uuid.uuid4().int % 250) + 1)}
    for _ in range(3):
        assert post_json(client, "/registry/disclosures", submission(title="http://x.example/"), token(), **ip2).status_code == 422
    assert post_json(client, "/registry/disclosures", submission(), token(), **ip2).status_code == 201


def test_disposable_email_domains_cannot_publish(client, keypair, submission):
    import time

    import jwt

    from lapse_registry.tests.conftest import ISSUER
    now = int(time.time())
    sub = str(uuid.uuid4())
    claims = {"iss": ISSUER, "sub": sub, "aud": "authenticated", "role": "authenticated", "email": "x@mailinator.com",
              "iat": now, "exp": now + 600, "is_anonymous": False, "user_metadata": {"email_verified": True}}
    t = jwt.encode(claims, keypair["private_pem"], algorithm="ES256", headers={"kid": "test-kid-1"})
    r = post_json(client, "/registry/disclosures", submission(), t)
    assert r.status_code == 403 and r.json()["code"] == "ERR_ACCOUNT"


def test_sitemap_lists_no_new_record(client, token, submission):
    rec = publish(client, token(), submission())
    r = client.get("/registry/sitemap.xml")
    assert r.status_code == 200 and r["Content-Type"].startswith("application/xml")
    assert r.content.startswith(b'<?xml version="1.0" encoding="UTF-8"?>') and rec["id"].encode() not in r.content
