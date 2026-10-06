"""The per-minute limit and the monthly cap under parallel traffic (PatentRef gate 5.10, fix of 2026-10-06).

Before the fix (DRF's cache-backed history on DatabaseCache over SQLite) a 60-request parallel burst from
one Free key got 60 x 200 on this test client and on the live box. The counters now live in rows bumped
with one atomic conditional increment, so the answer is exactly 45 x 200 and 15 x 429, and the month
counts exactly the 45. Threads each get their own Django connection to the temporary file DB, which is
the same contention shape as gunicorn workers on the box.
"""
import threading
import uuid
from collections import Counter

import pytest
from django.db import connection

from API.models import APIUserKey
from lapse_accounts import metering, throttling
from lapse_accounts.models import AccountKey, MonthlyUsage, WindowCount

PATENT = "/api/v1/patent/10000000/"


def _burst(n, key, path=PATENT):
    from django.test import Client

    codes, retry = [], []
    lock = threading.Lock()
    gate = threading.Barrier(n)

    def one():
        c = Client()
        gate.wait()
        r = c.get(path, HTTP_X_API_KEY=key)
        with lock:
            codes.append(r.status_code)
            if r.status_code == 429:
                retry.append((int(r["Retry-After"]), r.json()))
        connection.close()

    threads = [threading.Thread(target=one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return Counter(codes), retry


@pytest.fixture
def free_key():
    rec, key = APIUserKey.objects.create_key(name="burst", username="burst@example.test", email="burst@example.test")
    acct = AccountKey.objects.create(api_key=rec, supabase_user_id=str(uuid.uuid4()), email="burst@example.test")
    metering.forget_prefix(rec.prefix)
    return key, acct


def test_parallel_burst_is_exactly_the_minute_limit(free_key, monkeypatch, settings_jwks_reset):
    from django.conf import settings

    key, acct = free_key
    monkeypatch.setattr(settings, "LAPSE_FREE_MONTHLY_LIMIT", 1000)
    monkeypatch.setattr(throttling, "timer", lambda: 1_800_000_000.0 + 7)  # pinned: the burst sits in one window
    metering.forget_prefix(key.split(".")[0])
    connection.close()

    codes, retry = _burst(60, key)
    assert codes == {200: 45, 429: 15}, codes
    assert metering.usage_count(acct.subject) == 45  # the month counts exactly the 200s
    assert WindowCount.objects.get(subject=acct.subject).count == 45
    for wait, body in retry:  # upstream's 429 shape, Retry-After to the end of the minute
        assert 1 <= wait <= 60
        assert set(body) == {"detail"} and body["detail"] == f"Request was throttled. Expected available in {wait} seconds."

    # the next clock minute is a fresh window, and the old one is dropped
    monkeypatch.setattr(throttling, "timer", lambda: 1_800_000_000.0 + 67)
    codes, _ = _burst(10, key)
    assert codes == {200: 10}
    assert WindowCount.objects.filter(subject=acct.subject).count() == 1
    assert metering.usage_count(acct.subject) == 55


def test_parallel_burst_at_the_monthly_cap_is_exact(free_key, monkeypatch, settings_jwks_reset):
    from django.conf import settings

    key, acct = free_key
    monkeypatch.setattr(settings, "LAPSE_FREE_MONTHLY_LIMIT", 20)
    monkeypatch.setattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 1000)
    monkeypatch.setattr(throttling, "timer", lambda: 1_800_000_000.0 + 7)
    metering.forget_prefix(key.split(".")[0])
    MonthlyUsage.objects.update_or_create(subject=acct.subject, month=metering.current_month(), defaults={"count": 12})
    connection.close()

    codes, retry = _burst(30, key)
    assert codes == {200: 8, 429: 22}, codes
    assert metering.usage_count(acct.subject) == 20  # never over the cap, refused requests not counted
    assert all(wait == metering.seconds_to_reset() or abs(wait - metering.seconds_to_reset()) <= 2 for wait, _ in retry)


def test_rejected_after_admission_is_refunded(free_key, monkeypatch, settings_jwks_reset):
    """A 429 from the per-minute rule never reserves a monthly count; a 5xx after admission is refunded."""
    from django.conf import settings
    from django.test import Client

    key, acct = free_key
    monkeypatch.setattr(settings, "LAPSE_FREE_MONTHLY_LIMIT", 1000)
    monkeypatch.setattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 2)
    monkeypatch.setattr(throttling, "timer", lambda: 1_800_000_000.0 + 7)
    metering.forget_prefix(key.split(".")[0])
    c = Client()
    assert c.get(PATENT, HTTP_X_API_KEY=key).status_code == 200
    assert c.get(PATENT, HTTP_X_API_KEY=key).status_code == 200
    assert c.get(PATENT, HTTP_X_API_KEY=key).status_code == 429
    assert metering.usage_count(acct.subject) == 2

    monkeypatch.setattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 1000)
    metering.forget_prefix(key.split(".")[0])
    from API import PVAPIViews

    def boom(*a, **k):
        raise RuntimeError("search backend down")

    monkeypatch.setattr(PVAPIViews, "get_searcher", boom)
    c = Client(raise_request_exception=False)
    r = c.get(PATENT, HTTP_X_API_KEY=key)
    assert r.status_code == 500
    assert metering.usage_count(acct.subject) == 2  # reserved at admission, refunded on the 500
