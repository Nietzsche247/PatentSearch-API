"""Key minting, the usage endpoint, the monthly cap and the sign-up page."""
import json
import re
import uuid

from API.models import APIUserKey
from lapse_accounts import metering
from lapse_accounts.models import AccountKey, MonthlyUsage

PATENT_Q = '/api/v1/patent/?q={"patent_id":"10000000"}'
JSON = "application/json"


def post_keys(client, token, body=None):
    return client.post("/api/v1/meta/keys/", data=json.dumps(body or {}), content_type=JSON, HTTP_AUTHORIZATION="Bearer " + token)


def test_keys_requires_bearer(client):
    r = client.post("/api/v1/meta/keys/", data="{}", content_type=JSON)
    assert r.status_code == 401
    assert r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_AUTH"
    r = client.post("/api/v1/meta/keys/", data="{}", content_type=JSON, HTTP_AUTHORIZATION="Bearer nope")
    assert r.status_code == 401 and r["X-Status-Reason-Code"] == "ERR_AUTH"
    r = client.get("/api/v1/meta/keys/")
    assert r.status_code == 401


def test_mint_once_then_rotate(client, token_factory):
    sub = str(uuid.uuid4())
    token = token_factory(sub=sub, email="mint@example.test")
    r = post_keys(client, token)
    assert r.status_code == 201, r.content
    body = r.json()
    assert body["created"] is True and body["plan"] == "free"
    assert body["monthly_limit"] == 5 and body["per_minute_limit"] == 45
    key1, prefix1 = body["api_key"], body["prefix"]
    assert key1.startswith(prefix1 + ".")
    rec = APIUserKey.objects.get_from_key(key1)
    acct = AccountKey.objects.get(api_key=rec)
    assert acct.supabase_user_id == sub and acct.email == "mint@example.test" and acct.plan == "free"
    assert rec.username == "mint@example.test"

    # one active key per user: a second create returns the record without the secret
    r = post_keys(client, token)
    assert r.status_code == 200
    body = r.json()
    assert body["api_key"] is None and body["prefix"] == prefix1 and body["created"] is False

    # the key works on the data API
    r = client.get(PATENT_Q, HTTP_X_API_KEY=key1)
    assert r.status_code == 200, r.content
    assert r.json()["patents"][0]["patent_id"] == "10000000"

    # rotate: new key, old one revoked at once, count carried over
    r = post_keys(client, token, {"action": "rotate"})
    assert r.status_code == 201
    body = r.json()
    key2, prefix2 = body["api_key"], body["prefix"]
    assert prefix2 != prefix1 and body["rotated_from"] == prefix1
    assert client.get(PATENT_Q, HTTP_X_API_KEY=key1).status_code == 403
    assert client.get(PATENT_Q, HTTP_X_API_KEY=key2).status_code == 200
    usage = client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=key2).json()
    assert usage["count"] == 2 and usage["key_prefix"] == prefix2

    listing = client.get("/api/v1/meta/keys/", HTTP_AUTHORIZATION="Bearer " + token).json()
    assert [k["revoked"] for k in listing["keys"]] == [False, True]
    assert listing["email"] == "mint@example.test"

    r = post_keys(client, token, {"action": "delete"})
    assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q"


def test_usage_endpoint_counts_data_requests(client, user_key):
    token, key, prefix = user_key
    u = client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=key).json()
    assert u == {
        "error": False, "plan": "free", "key_prefix": prefix, "month": metering.current_month(), "count": 0,
        "monthly_limit": 5, "remaining": 5, "per_minute_limit": 45,
        "resets_at": metering.month_reset().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    assert client.get(PATENT_Q, HTTP_X_API_KEY=key).status_code == 200
    assert client.get("/api/v1/patent/?q=notjson", HTTP_X_API_KEY=key).status_code == 400  # counts: the query ran
    assert client.get("/api/v1/patent/10000001/", HTTP_X_API_KEY=key).status_code == 200
    assert client.get(PATENT_Q, HTTP_X_API_KEY="bogus.key").status_code == 403  # does not count
    assert client.get(PATENT_Q).status_code == 403
    u = client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=key).json()
    assert u["count"] == 3 and u["remaining"] == 2
    # the same numbers through the bearer token, and meta calls never count
    u2 = client.get("/api/v1/meta/usage/", HTTP_AUTHORIZATION="Bearer " + token).json()
    assert u2["count"] == 3 and u2["key_prefix"] == prefix
    assert MonthlyUsage.objects.get(subject=AccountKey.objects.get(api_key__prefix=prefix).subject, month=metering.current_month()).count == 3


def test_usage_without_credentials(client, token_factory):
    r = client.get("/api/v1/meta/usage/")
    assert r.status_code == 403 and r.json() == {"error": True} and r["X-Status-Reason-Code"] == "ERR_KEY"
    r = client.get("/api/v1/meta/usage/", HTTP_X_API_KEY="nope.nope")
    assert r.status_code == 403
    r = client.get("/api/v1/meta/usage/", HTTP_AUTHORIZATION="Bearer " + token_factory())
    assert r.status_code == 200 and r.json()["key_prefix"] is None and r.json()["count"] == 0


def test_monthly_cap_returns_upstream_style_429(client, user_key):
    token, key, prefix = user_key
    acct = AccountKey.objects.get(api_key__prefix=prefix)
    MonthlyUsage.objects.update_or_create(subject=acct.subject, month=metering.current_month(), defaults={"count": 4})
    assert client.get(PATENT_Q, HTTP_X_API_KEY=key).status_code == 200  # the 5th
    r = client.get(PATENT_Q, HTTP_X_API_KEY=key)
    assert r.status_code == 429
    body = r.json()
    assert set(body) == {"detail"} and body["detail"].startswith("Request was throttled. Expected available in ")
    assert int(r["Retry-After"]) == metering.seconds_to_reset() or abs(int(r["Retry-After"]) - metering.seconds_to_reset()) <= 2
    u = client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=key).json()
    assert u["count"] == 5 and u["remaining"] == 0  # the refused request was not counted
    # a key with no account (the operator's own) has no monthly cap
    _, opkey = APIUserKey.objects.create_key(name="operator", username="op", email="op@example.test")
    MonthlyUsage.objects.create(subject="key:" + opkey.split(".")[0], month=metering.current_month(), count=10**6)
    assert client.get(PATENT_Q, HTTP_X_API_KEY=opkey).status_code == 200
    assert client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=opkey).json()["monthly_limit"] is None


def test_signup_page(client):
    r = client.get("/api/v1/meta/signup/")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/html")
    html = r.content.decode()
    assert 'class="pr-header"' in html and 'class="pr-footer"' in html
    unescaped = re.sub(r"\\u([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), html)  # undo escapejs
    assert "https://example-project.supabase.co" in unescaped and "anon-key-for-tests" in unescaped
    assert "service_role" not in unescaped.lower()
    assert "5 requests a month" in html and "45 a minute" in html
    assert "—" not in html  # no em-dashes on PatentRef pages
    assert client.post("/api/v1/meta/signup/").status_code == 405
