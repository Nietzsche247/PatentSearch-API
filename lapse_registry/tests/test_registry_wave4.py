"""Registry 2 routes (PatentRef checklist 5.11, 5.12, 10.3, 10.4, 10.7, 10.9, 10.12) against a local Postgres and a
stand-in JWKS; the corpus receipt against two small snapshot files and a real registry_crypto keyring in a temp dir.

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_registry/tests -q -p no:cacheprovider
"""
import json
import os
import sqlite3
import threading
import uuid

import pytest
from django.conf import settings


def post_json(client, path, body, token=None, **extra):
    if token:
        extra["HTTP_AUTHORIZATION"] = "Bearer " + token
    return client.post(path, data=json.dumps(body), content_type="application/json", **extra)


def auth(t):
    return {"HTTP_AUTHORIZATION": "Bearer " + t}


def publish(client, token, body):
    r = post_json(client, "/registry/disclosures", body, token)
    assert r.status_code == 201, r.content
    return r.json()


# ------------------------------------------------------------------ 5.11

def test_publishing_waits_for_the_terms(client, token, submission):
    from patentref_registry import terms

    user = str(uuid.uuid4())
    t = token(sub=user)
    body = submission()
    body.pop("terms_version")
    st = client.get("/registry/disclosures/terms", **auth(t)).json()
    assert st["accepted"] is False and st["version"] == terms.VERSION
    assert st["links"] == {"terms": "https://patentref.io/terms", "privacy": "https://patentref.io/privacy",
                           "acceptable_use": "https://patentref.io/acceptable-use", "dmca": "https://patentref.io/dmca"}
    r = post_json(client, "/registry/disclosures", body, t)
    assert r.status_code == 403 and r.json()["code"] == "ERR_TERMS" and r["X-Status-Reason-Code"] == "ERR_TERMS"
    assert r.json()["terms"]["links"]["terms"].endswith("/terms")
    # accepted at sign-up: the token's user metadata carries the version
    t2 = token(metadata={"terms_version": terms.VERSION})
    assert client.get("/registry/disclosures/terms", **auth(t2)).json()["accepted"] is True
    assert post_json(client, "/registry/disclosures", body, t2).status_code == 201
    # ticked on the publish form (multipart too)
    form = dict(submission(), terms_version=terms.VERSION, cpc_guess="", inventors_as_entered="")
    r = client.post("/registry/disclosures", data=form, **auth(t))
    assert r.status_code == 201, r.content
    assert client.get("/registry/disclosures/terms", **auth(t)).json()["accepted"] is True


# ------------------------------------------------------------------ 5.12

def test_export_and_erase_over_http(client, token, submission):
    user = str(uuid.uuid4())
    t = token(sub=user)
    rec = publish(client, t, submission())
    r = client.get("/registry/disclosures/account/export", **auth(t))
    assert r.status_code == 200 and "attachment" in r["Content-Disposition"]
    assert [x["id"] for x in r.json()["records"]] == [rec["id"]] and r.json()["terms_acceptances"]
    assert client.get("/registry/disclosures/account/export").status_code == 401
    assert post_json(client, "/registry/disclosures/account/erase", {"confirm": "yes"}, t).status_code == 400
    r = post_json(client, "/registry/disclosures/account/erase", {"confirm": "ERASE"}, t)
    assert r.status_code == 200 and r.json()["erased"] == [rec["id"]]
    tomb = client.get(f"/r/{rec['id']}.json").json()
    assert tomb["erased"] is True and tomb["user_content"] == {} and tomb["bundle_hash"] == rec["bundle_hash"]


# ------------------------------------------------------------------ 10.9

NOTICE = {"signature": "/J. C./", "work": "Heater 7 drawings", "contact": "J. C., 1 Main St; +1 555 0100; jc@example.test",
          "email": "jc@example.test", "good_faith": "good-faith belief stated", "accuracy": "under penalty of perjury, accurate"}
COUNTER = {"signature": "/O. O./", "contact": "O. O., 2 Elm St; +1 555 0101",
           "good_faith": "under penalty of perjury, removed by mistake", "jurisdiction": "I consent to the federal court"}


def test_a_notice_hides_the_content_and_the_owner_counters(client, token, submission, monkeypatch):
    owner = str(uuid.uuid4())
    t = token(sub=owner)
    rec = publish(client, t, submission())
    body = dict(NOTICE, material_url=rec["permalink"])
    assert post_json(client, "/registry/disclosures/notices", body).status_code == 501   # the form is off by default
    monkeypatch.setattr(settings, "REGISTRY_NOTICES_ENABLED", True)
    r = post_json(client, "/registry/disclosures/notices", body)
    assert r.status_code == 202 and r.json()["kind"] == "dmca_notice" and r.json()["status"] == "open"
    cid = r.json()["id"]
    r = client.get(f"/r/{rec['id']}.json")
    assert r.status_code == 451 and r.json()["hidden"] is True and r.json()["bundle_hash"] == rec["bundle_hash"]
    assert "contact" not in json.dumps(client.get(f"/registry/disclosures/notices/{cid}").json())
    assert post_json(client, f"/registry/disclosures/notices/{cid}/counter", dict(COUNTER, material_url=rec["permalink"]),
                     token()).status_code == 403   # not the owner
    r = post_json(client, f"/registry/disclosures/notices/{cid}/counter", dict(COUNTER, material_url=rec["permalink"]), t)
    assert r.status_code == 202 and r.json()["kind"] == "counter_notice" and r.json()["status"] == "waiting"


def test_claims_are_off_until_offered_and_freeze_when_on(client, token, submission, monkeypatch):
    owner, other = str(uuid.uuid4()), str(uuid.uuid4())
    rec = publish(client, token(sub=owner), submission())
    assert post_json(client, f"/registry/disclosures/{rec['id']}/claim", {"claimant_name": "Acme"}, token(sub=other)).status_code == 501
    monkeypatch.setattr(settings, "REGISTRY_CLAIMS_ENABLED", True)
    r = post_json(client, f"/registry/disclosures/{rec['id']}/claim", {"claimant_name": "Acme"}, token(sub=other))
    assert r.status_code == 202 and r.json()["kind"] == "claim_conflict"
    r = post_json(client, "/registry/disclosures", submission(prev_version_id=rec["id"]), token(sub=owner))
    assert r.status_code == 409 and r.json()["code"] == "ERR_FROZEN"


# ------------------------------------------------------------------ 10.7 and 10.4 switches

def test_monitoring_and_contribution_logs_wait_for_the_owner(client, token, submission, monkeypatch):
    import registry_crypto_stub as stub

    from lapse_registry import views

    user = str(uuid.uuid4())
    t = token(sub=user)
    rec = publish(client, t, submission())
    assert post_json(client, f"/registry/disclosures/{rec['id']}/monitor", {"on": True}, t).status_code == 501
    monkeypatch.setattr(settings, "REGISTRY_MONITORING_ENABLED", True)
    r = post_json(client, f"/registry/disclosures/{rec['id']}/monitor", {"on": True}, t)
    assert r.status_code == 200 and r.json()["monitoring"] is True and r.json()["notify_email"].endswith("@example.test")
    assert post_json(client, f"/registry/disclosures/{rec['id']}/monitor", {"on": True}, token()).status_code == 404
    assert post_json(client, f"/registry/disclosures/{rec['id']}/monitor", {"on": False}, t).json()["monitoring"] is False
    log = {"entries": [{"who": "A", "decision": "B", "tool": "C", "when": "2026-10-01T00:00:00Z"}]}
    assert post_json(client, f"/registry/disclosures/{rec['id']}/contribution-log", log, t).status_code == 501
    monkeypatch.setattr(settings, "REGISTRY_CONTRIBUTION_LOGS_ENABLED", True)
    monkeypatch.setattr(views, "_keys", lambda: stub)
    r = post_json(client, f"/registry/disclosures/{rec['id']}/contribution-log", log, t)
    assert r.status_code == 201 and stub.verify_receipt(r.json()["countersignature"])["log_hash"] == r.json()["log_hash"]
    assert client.get(f"/registry/disclosures/{rec['id']}/contribution-log.zip").status_code == 404   # until the worker runs


# ------------------------------------------------------------------ 10.3 and 10.12: the pinned snapshot

def _snapshot(path, version, base_id):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_title TEXT, patent_date TEXT, withdrawn INT, "
                "patent_abstract TEXT, gov_interest_statement TEXT)")
    for i in range(40):
        con.execute("INSERT INTO patents VALUES (?, ?, ?, 0, ?, '')",
                    (str(base_id + i), f"Pool heater bypass fuse {version} variant {i}", "2020-01-01",
                     f"A thermal fuse in the bypass of a pool heater, {version}, number {i}. " + "valve " * (i % 5)))
    con.execute("CREATE VIRTUAL TABLE fts_patents USING fts5(gov_interest_statement, patent_abstract, patent_title, "
                "content='patents', content_rowid='rowid', tokenize=\"unicode61 remove_diacritics 0 tokenchars '_'\")")
    con.execute("INSERT INTO fts_patents(fts_patents) VALUES ('rebuild')")
    con.execute("CREATE TABLE _lapse_build(k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?, ?)", [("data_version", version), ("release", version[:8])])
    con.commit()
    con.close()


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    a, b, link = tmp_path / "snapshot_20261020.1.db", tmp_path / "snapshot_20261027.1.db", tmp_path / "snapshot_current.db"
    _snapshot(a, "20261020.1", 10000000)
    _snapshot(b, "20261027.1", 20000000)
    os.symlink(a, link)
    monkeypatch.setattr(settings, "LAPSE_SQLITE", dict(getattr(settings, "LAPSE_SQLITE", {}) or {}, path=str(link)))

    def swap():   # what the refresh swap does: a new symlink renamed over the old one
        tmp = tmp_path / "snapshot_current.db.new"
        os.symlink(b, tmp)
        os.replace(tmp, link)
    return {"link": link, "swap": swap, "versions": {"20261020.1": "1", "20261027.1": "2"}}


def test_50_concurrent_receipts_through_a_swap_each_name_one_data_version(snapshots):
    from lapse_registry import receipt_search

    lock, entered = threading.Lock(), [0]
    half, swapped = threading.Event(), threading.Event()
    results, errors = [], []

    def hold():
        with lock:
            entered[0] += 1
            n = entered[0]
        if n == 25:
            half.set()
        if n <= 25:
            swapped.wait(20)   # the first 25 hold their connection open across the swap

    def one():
        try:
            results.append(receipt_search.similar("Pool heater bypass with a thermal fuse valve", 20, hold=hold))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    first = [threading.Thread(target=one) for _ in range(25)]
    for th in first:
        th.start()
    assert half.wait(20)
    snapshots["swap"]()
    swapped.set()
    second = [threading.Thread(target=one) for _ in range(25)]
    for th in second:
        th.start()
    for th in first + second:
        th.join(30)
    assert not errors and len(results) == 50
    seen = {}
    for r in results:
        lead = snapshots["versions"][r["data_version"]]
        assert r["neighbors"] and all(nb["patent_id"].startswith(lead) for nb in r["neighbors"])   # one data version each
        assert all(r["data_version"] in nb["patent_title"] for nb in r["neighbors"])
        seen[r["data_version"]] = seen.get(r["data_version"], 0) + 1
    assert seen == {"20261020.1": 25, "20261027.1": 25}


@pytest.fixture
def real_keys(tmp_path, monkeypatch):
    import registry_crypto

    monkeypatch.setenv("REGISTRY_KEYS_DIR", str(tmp_path / "keys"))
    registry_crypto._rings.clear()
    registry_crypto.init()
    yield registry_crypto
    registry_crypto._rings.clear()


def test_a_receipt_signs_one_data_version_and_a_forced_failure_refunds(client, token, submission, snapshots, real_keys,
                                                                    monkeypatch, _env):
    from patentref_registry import payments
    from registry_crypto import jws

    from lapse_registry import db, receipt_search

    monkeypatch.setattr(settings, "REGISTRY_RECEIPTS_ENABLED", True)
    user = str(uuid.uuid4())
    t = token(sub=user)
    rec = publish(client, t, submission(title="Pool heater bypass with a thermal fuse valve"))
    r = client.post(f"/registry/disclosures/{rec['id']}/receipt", **auth(t))
    assert r.status_code == 201, r.content
    out = r.json()
    payload = jws.verify_with_jwks(out["signature"], client.get("/registry/.well-known/jwks.json").json())
    assert payload["data_version"] == "20261020.1" and payload["as_of"] == "2026-10-20" and len(payload["neighbors"]) == 20
    assert all(nb["patent_id"].startswith("1") for nb in payload["neighbors"]) and payload["neighbors"][0]["patent_abstract"]
    import psycopg

    with psycopg.connect(_env.admin_dsn, autocommit=True) as con:
        assert con.execute("select status from registry.receipt_rekor where receipt_id = %s", (payload["id"],)).fetchone() == ("queued",)
    # a paid receipt whose search fails is refunded without a person (no provider here: the refund is due, not lost)
    pay = payments.record(db.connection(), kind="receipt", record_id=rec["id"], owner_user_id=user, provider_name="none",
                          provider_ref="pi_forced", amount_cents=900)

    def broken(text, n):
        raise sqlite3.OperationalError("forced failure")

    monkeypatch.setattr(receipt_search, "similar", broken)
    r = post_json(client, f"/registry/disclosures/{rec['id']}/receipt", {"payment_ref": "pi_forced"}, t)
    assert r.status_code == 502 and r.json()["code"] == "ERR_RECEIPT" and r.json()["refund"] == "refund_due"
    with psycopg.connect(_env.admin_dsn, autocommit=True) as con:
        assert con.execute("select status from registry.payments where id = %s", (pay["id"],)).fetchone() == ("refund_due",)
    assert post_json(client, f"/registry/disclosures/{rec['id']}/receipt", {"payment_ref": "nope"}, t).status_code == 402
