"""Abuse limits (PatentRef checklist gate 5.6): per-IP keyless limit, per-IP ceiling across keys, in-flight
caps, request size caps, query cost cap, per-request SQLite budget, sign-up limits per email domain.

Every limit is additive: a valid request's status and body are unchanged (checked in the first test).
"""
import json
import threading
import time
import uuid
from collections import Counter

import pytest
from django.conf import settings
from django.db import connection

from API import lapse_cost
from API.models import APIUserKey
from lapse_accounts import abuse, metering, throttling
from lapse_accounts.models import AccountKey, WindowCount

DETAIL = "/api/v1/patent/10000000/"
LIST = "/api/v1/patent/"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, settings_jwks_reset):
    abuse.reset_caches()
    WindowCount.objects.all().delete()
    monkeypatch.setattr(settings, "LAPSE_FREE_MONTHLY_LIMIT", 100000)
    monkeypatch.setattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 1000)
    monkeypatch.setattr(throttling, "timer", lambda: 1_900_000_000.0 + 5)  # one clock minute for the whole test
    monkeypatch.setattr(abuse, "timer", lambda: 1_900_000_000.0 + 5)
    yield
    abuse.reset_caches()


@pytest.fixture
def free_key():
    rec, key = APIUserKey.objects.create_key(name="abuse", username="abuse@example.test", email="abuse@example.test")
    AccountKey.objects.create(api_key=rec, supabase_user_id=str(uuid.uuid4()), email="abuse@example.test")
    metering.forget_prefix(rec.prefix)
    return key


def _throttled(r):
    assert r.status_code == 429
    body = r.json()
    wait = int(r["Retry-After"])
    assert body == {"detail": f"Request was throttled. Expected available in {wait} seconds."}
    return wait


def _bad_request(r, fragment):
    assert r.status_code == 400
    assert r.content == b'{"error":true}'
    assert r["X-Status-Reason-Code"] == "ERR_Q"
    assert fragment in r["X-Status-Reason"], r["X-Status-Reason"]


# ---- 0. nothing changes for a valid request
def test_valid_request_unchanged(client, free_key):
    r = client.get(DETAIL, HTTP_X_API_KEY=free_key)
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is False and body["count"] == 1 and body["patents"][0]["patent_id"] == "10000000"
    # the in-flight reservation was released on the way out
    rows = {w.win.split(":")[0]: w.count for w in WindowCount.objects.filter(win__startswith="inflight:")}
    assert all(c == 0 for c in rows.values()), rows


# ---- 1. client address behind the proxy
def test_client_ip_trusts_only_the_proxy(monkeypatch):
    from django.test import RequestFactory

    rf = RequestFactory()
    monkeypatch.setattr(settings, "LAPSE_BEHIND_PROXY", True)
    abuse.reset_caches()
    # from the docker bridge (Caddy): the last X-Forwarded-For entry, whatever the client put in front of it
    req = rf.get(LIST, REMOTE_ADDR="172.17.0.2", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9")
    assert abuse.client_ip(req) == "203.0.113.9"
    req = rf.get(LIST, REMOTE_ADDR="127.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.9")
    assert abuse.client_ip(req) == "203.0.113.9"
    # from anywhere else the header is ignored
    req = rf.get(LIST, REMOTE_ADDR="198.51.100.7", HTTP_X_FORWARDED_FOR="203.0.113.9")
    assert abuse.client_ip(req) == "198.51.100.7"
    # not behind a proxy: never trusted
    monkeypatch.setattr(settings, "LAPSE_BEHIND_PROXY", False)
    req = rf.get(LIST, REMOTE_ADDR="172.17.0.2", HTTP_X_FORWARDED_FOR="203.0.113.9")
    assert abuse.client_ip(req) == "172.17.0.2"


# ---- 2. per-IP keyless limit, invalid keys counted too, honest keys on the same address untouched
def test_keyless_flood_per_ip(client, free_key, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_NOKEY_PER_MINUTE", 5)
    codes = Counter(client.get(DETAIL, REMOTE_ADDR="203.0.113.5").status_code for _ in range(8))
    assert codes == {403: 5, 429: 3}
    wait = _throttled(client.get(DETAIL, REMOTE_ADDR="203.0.113.5"))
    assert 1 <= wait <= 61
    # another address is not affected, and a valid key from the flooded address still works
    assert client.get(DETAIL, REMOTE_ADDR="203.0.113.6").status_code == 403
    assert client.get(DETAIL, REMOTE_ADDR="203.0.113.5", HTTP_X_API_KEY=free_key).status_code == 200


def test_invalid_key_flood_per_ip(client, free_key, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_NOKEY_PER_MINUTE", 4)
    bad = "notaprefix.notasecretvalue"
    first = client.get(DETAIL, REMOTE_ADDR="203.0.113.7", HTTP_X_API_KEY=bad)
    assert first.status_code == 403  # upstream's answer, counted after the fact
    codes = Counter(client.get(DETAIL, REMOTE_ADDR="203.0.113.7", HTTP_X_API_KEY=bad).status_code for _ in range(6))
    assert codes == {403: 3, 429: 3}, codes
    assert client.get(DETAIL, REMOTE_ADDR="203.0.113.7", HTTP_X_API_KEY=free_key).status_code == 200


# ---- 3. per-IP ceiling across keys (only admitted requests count)
def test_ip_ceiling_across_keys(client, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_IP_PER_MINUTE", 6)
    keys = []
    for i in range(3):
        rec, key = APIUserKey.objects.create_key(name=f"ceil{i}", username=f"c{i}@example.test", email=f"c{i}@example.test")
        AccountKey.objects.create(api_key=rec, supabase_user_id=str(uuid.uuid4()), email=f"c{i}@example.test")
        metering.forget_prefix(rec.prefix)
        keys.append(key)
    codes = Counter(client.get(DETAIL, REMOTE_ADDR="203.0.113.8", HTTP_X_API_KEY=keys[i % 3]).status_code for i in range(9))
    assert codes == {200: 6, 429: 3}, codes
    assert client.get(DETAIL, REMOTE_ADDR="203.0.113.9", HTTP_X_API_KEY=keys[0]).status_code == 200  # another address
    # requests a key's own minute rule already denied do not use the address's ceiling
    WindowCount.objects.all().delete()
    monkeypatch.setattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 2)
    for k in keys[0], keys[1]:
        metering.forget_prefix(k.split(".")[0])
    codes = Counter(client.get(DETAIL, REMOTE_ADDR="203.0.113.10", HTTP_X_API_KEY=keys[0]).status_code for _ in range(10))
    assert codes == {200: 2, 429: 8}
    assert client.get(DETAIL, REMOTE_ADDR="203.0.113.10", HTTP_X_API_KEY=keys[1]).status_code == 200
    row = WindowCount.objects.get(subject="ip:203.0.113.10", win__startswith="ip:")
    assert row.count == 3  # 2 + 1 admitted, the 8 denied never counted


# ---- 4. in-flight cap per key
def test_inflight_cap_per_key(free_key, monkeypatch):
    from django.test import Client

    monkeypatch.setattr(settings, "LAPSE_INFLIGHT_PER_KEY", 3)
    monkeypatch.setattr(settings, "LAPSE_INFLIGHT_PER_IP", 100)
    # hold the view for a moment so the burst really overlaps
    import API.PVAPIViews as views

    orig = views.PVAPIView.query_handler

    def slow(self, request):
        time.sleep(0.4)
        return orig(self, request)

    monkeypatch.setattr(views.PVAPIView, "query_handler", slow)
    codes, lock, gate = [], threading.Lock(), threading.Barrier(8)

    def one():
        c = Client()
        gate.wait()
        r = c.get(DETAIL, HTTP_X_API_KEY=free_key)
        with lock:
            codes.append(r.status_code)
        connection.close()

    ts = [threading.Thread(target=one) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert Counter(codes) == {200: 3, 429: 5}, codes
    # every reservation released
    assert all(w.count == 0 for w in WindowCount.objects.filter(win__startswith="inflight:"))
    # and the key is admitted again at once
    monkeypatch.setattr(views.PVAPIView, "query_handler", orig)
    assert Client().get(DETAIL, HTTP_X_API_KEY=free_key).status_code == 200


# ---- 5. request size caps
def test_size_caps(client, free_key, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_MAX_QUERY_STRING", 300)
    monkeypatch.setattr(settings, "LAPSE_MAX_Q_BYTES", 200)
    monkeypatch.setattr(settings, "LAPSE_MAX_BODY", 500)
    ok = client.get(LIST, {"q": json.dumps({"patent_id": "10000000"})}, HTTP_X_API_KEY=free_key)
    assert ok.status_code == 200
    long_q = json.dumps({"patent_id": ["10000000"] * 30})
    r = client.get(LIST, {"q": long_q, "f": json.dumps(["patent_id"] * 40)}, HTTP_X_API_KEY=free_key)
    _bad_request(r, "Query string too long")
    monkeypatch.setattr(settings, "LAPSE_MAX_QUERY_STRING", 1000)
    r = client.get(LIST, {"q": json.dumps({"patent_id": ["10000000"] * 18})}, HTTP_X_API_KEY=free_key)
    _bad_request(r, "'q' parameter too large")
    body = json.dumps({"q": {"patent_id": "10000000"}, "f": ["patent_id"] * 60})
    r = client.post(LIST, data=body, content_type="application/json", HTTP_X_API_KEY=free_key)
    _bad_request(r, "Request body too large")
    # a POST under the body cap but with a q over the q cap is caught after parsing
    monkeypatch.setattr(settings, "LAPSE_MAX_BODY", 5000)
    body = json.dumps({"q": {"patent_id": ["10000000"] * 18}})
    r = client.post(LIST, data=body, content_type="application/json", HTTP_X_API_KEY=free_key)
    _bad_request(r, "'q' parameter too large")
    # the caps do not apply to the account endpoints
    r = client.get("/api/v1/meta/usage/", {"pad": "x" * 400}, HTTP_X_API_KEY=free_key)
    assert r.status_code == 200


# ---- 6. query cost cap
def test_cost_estimate_weights():
    est = lapse_cost.estimate({"patent_id": "1"})
    assert est.cost == 1 and est.criteria == 1 and est.depth == 1
    est = lapse_cost.estimate({"_text_any": {"patent_title": "laser"}}, {"size": 1000}, [{"patent_id": "asc"}])
    assert est.cost == 4 + 10 + 2
    est = lapse_cost.estimate({"_and": [{"_contains": {"inventors.inventor_name_last": "mit"}}, {"_neq": {"patent_type": "design"}}]})
    assert est.cost == 6 + 3 + 1 + 2 and est.criteria == 2 and est.depth == 2 and est.nested == {"inventors"}
    est = lapse_cost.estimate({"_contains": {"patent_title": "d?ta"}})
    assert est.cost == 20  # wildcard characters: a full scan
    est = lapse_cost.estimate({"_or": [{"patent_id": "1"}, {"patent_id": "2"}, {"patent_id": "3"}]})
    assert est.cost == 3 + 2


def test_contract_examples_and_matrix_shapes_are_under_the_limit():
    import os

    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "..", "..", "lapse_tools", "compat", "contract_examples.json")
    with open(path, encoding="utf-8") as fh:
        examples = json.load(fh)["examples"]
    limit = settings.LAPSE_QUERY_COST_LIMIT
    worst = max(lapse_cost.estimate(e.get("q"), {"size": e.get("effective_size", 100)}, e.get("effective_s")).cost for e in examples)
    assert worst <= 30 < limit, worst
    # the heaviest operator-matrix cells (tests/compat/test_operator_matrix.py in the patentref repo), 1000-row pages
    cells = [
        {"_contains": {"inventors.inventor_sequence": "1"}},
        {"_or": [{"_text_phrase": {"patent_title": "surgical instrument"}}, {"_text_phrase": {"patent_title": "data transfer"}}]},
        {"_and": [{"_eq": {"inventors.inventor_name_last": "Smith"}}, {"_begins": {"inventors.inventor_name_last": "jo"}}]},
        {"_contains": {"patent_title": "d?ta"}},
    ]
    for q in cells:
        assert lapse_cost.estimate(q, {"size": 1000}, [{"patent_id": "asc"}]).cost <= 40 < limit


def test_cost_cap_refuses_with_the_upstream_400(client, free_key, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_QUERY_COST_LIMIT", 100)
    heavy = {"_or": [{"_contains": {"patent_title": f"x?{i}"}} for i in range(6)]}  # 6 scans 120, _or 5, default sort 2
    r = client.get(LIST, {"q": json.dumps(heavy)}, HTTP_X_API_KEY=free_key)
    _bad_request(r, "Query too expensive: estimated cost 127 units, the limit is 100")
    deep = {"patent_id": "10000000"}
    for _ in range(9):
        deep = {"_and": [deep]}
    r = client.get(LIST, {"q": json.dumps(deep)}, HTTP_X_API_KEY=free_key)
    _bad_request(r, "Query too deeply nested")
    r = client.get(LIST, {"q": json.dumps({"patent_id": [str(i) for i in range(65)]})}, HTTP_X_API_KEY=free_key)
    _bad_request(r, "Query has too many criteria: 65")
    r = client.get(LIST, {"q": json.dumps({"patent_id": [str(i) for i in range(64)]})}, HTTP_X_API_KEY=free_key)
    assert r.status_code == 200
    # a bad query the parser rejects still gets the parser's own message (the estimate never raises on shape)
    r = client.get(LIST, {"q": json.dumps({"patent_id": "1", "patent_type": "x"})}, HTTP_X_API_KEY=free_key)
    _bad_request(r, "Query string should have only one 'key-value' pair")


# ---- 7. per-request SQLite budget
def test_request_budget_interrupts(client, free_key, monkeypatch):
    from API import search_sqlite

    monkeypatch.setattr(settings, "LAPSE_REQUEST_BUDGET", 0.001)
    orig = search_sqlite.LapseSQLiteSearch._run

    def slow(self, sql, params):
        time.sleep(0.01)
        return orig(self, sql, params)

    monkeypatch.setattr(search_sqlite.LapseSQLiteSearch, "_run", slow)
    r = client.get(LIST, {"q": json.dumps({"patent_id": "10000000"})}, HTTP_X_API_KEY=free_key)
    assert r.status_code == 500 and r["X-Status-Reason-Code"] == "ERR_ES"  # the upstream ES-timeout answer
    assert r.content == b'{"error":true}' and r["X-Status-Reason"].startswith("Search timed out: one statement may run")
    monkeypatch.setattr(settings, "LAPSE_REQUEST_BUDGET", 30)
    r = client.get(LIST, {"q": json.dumps({"patent_id": "10000000"})}, HTTP_X_API_KEY=free_key)
    assert r.status_code == 200


# ---- 8. sign-ups per email domain
def _mint(client, token):
    return client.post("/api/v1/meta/keys/", data="{}", content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token)


def test_disposable_domains_refused(client, token_factory):
    for email in ("someone@mailinator.com", "x@sub.guerrillamail.com", "y@YOPMAIL.COM"):
        r = _mint(client, token_factory(sub=str(uuid.uuid4()), email=email))
        assert r.status_code == 403, email
        assert r.json() == {"error": True} and r["X-Status-Reason-Code"] == "ERR_AUTH"
        assert "disposable" in r["X-Status-Reason"]
    assert AccountKey.objects.filter(email__endswith="mailinator.com").count() == 0


def test_domain_daily_limit(client, token_factory, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_SIGNUP_DOMAIN_DAILY", 3)
    tokens = [token_factory(sub=str(uuid.uuid4()), email=f"u{i}@limited.example") for i in range(5)]
    codes = [_mint(client, t).status_code for t in tokens]
    assert codes == [201, 201, 201, 429, 429]
    r = _mint(client, tokens[4])
    wait = _throttled(r)
    assert 1 <= wait <= 86401 and r["X-Status-Reason-Code"] == "ERR_SIGNUP_LIMIT"
    # an existing account is never counted again: its create is idempotent and its rotate is not a sign-up
    assert _mint(client, tokens[0]).status_code == 200
    r = client.post("/api/v1/meta/keys/", data=json.dumps({"action": "rotate"}), content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer " + tokens[0])
    assert r.status_code == 201
    assert WindowCount.objects.get(subject="domain:limited.example").count == 3
    # another domain has its own allowance
    assert _mint(client, token_factory(sub=str(uuid.uuid4()), email="a@other.example")).status_code == 201
