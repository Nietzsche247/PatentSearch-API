"""X-Data-Version and the per-request file resolution (PatentRef gate 4.3).

Two three-row search databases with different content and different `_lapse_build.data_version`
rows; the configured path is a symlink that moves between requests. Every response must carry
exactly one X-Data-Version, and the body must come from the file the header names.
Reuses the fixtures of conftest.py (temporary Django DB, operator key via the account endpoints).
"""
import os
import sqlite3

import pytest
from django.conf import settings

from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP, _build_search_db


def _stamp(path, version, title_suffix):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS _lapse_build(k TEXT, v TEXT)")
    if version:
        con.execute("INSERT INTO _lapse_build VALUES ('data_version', ?)", (version,))
    con.execute("UPDATE patents SET patent_title = patent_title || ?", (title_suffix,))
    con.commit()
    con.close()


def _repoint(link, target):
    tmp = link + ".tmp"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)


@pytest.fixture
def two_files():
    a, b = str(_TMP / "snapshot_a.db"), str(_TMP / "snapshot_b.db")
    for p in (a, b):
        if os.path.exists(p):
            os.unlink(p)
    _build_search_db(a)
    _build_search_db(b)
    _stamp(a, "20260901.1", " [A]")
    _stamp(b, "20260929.1", " [B]")
    link = str(_TMP / "snapshot_current.db")
    _repoint(link, "snapshot_a.db")
    old = dict(settings.LAPSE_SQLITE)
    settings.LAPSE_SQLITE["path"] = link
    LapseSQLiteSearch._local.__dict__.clear()
    try:
        yield link
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        LapseSQLiteSearch._local.__dict__.clear()


def _get(client, key):
    return client.get("/api/v1/patent/", {"q": '{"patent_id":"10000000"}', "f": '["patent_id","patent_title"]'}, HTTP_X_API_KEY=key)


def test_header_names_the_file_that_produced_the_body(client, user_key, two_files):
    _, key, _ = user_key
    r = _get(client, key)
    assert r.status_code == 200
    assert r["X-Data-Version"] == "20260901.1"
    assert r.json()["patents"][0]["patent_title"].endswith("[A]")

    _repoint(two_files, "snapshot_b.db")      # the swap: a rename over the symlink, no restart
    r = _get(client, key)
    assert r.status_code == 200
    assert r["X-Data-Version"] == "20260929.1"
    assert r.json()["patents"][0]["patent_title"].endswith("[B]")

    _repoint(two_files, "snapshot_a.db")      # the rollback
    r = _get(client, key)
    assert r["X-Data-Version"] == "20260901.1"
    assert r.json()["patents"][0]["patent_title"].endswith("[A]")


def test_every_response_carries_exactly_one_header(client, user_key, two_files):
    _, key, _ = user_key
    unkeyed = client.get("/api/v1/patent/")
    assert unkeyed.status_code == 403
    assert unkeyed["X-Data-Version"] == "20260901.1"
    bad = client.get("/api/v1/patent/", {"q": "not json"}, HTTP_X_API_KEY=key)
    assert bad.status_code == 400
    assert bad["X-Data-Version"] == "20260901.1"
    meta = client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=key)
    assert meta["X-Data-Version"] == "20260901.1"
    for r in (unkeyed, bad, meta):
        assert sum(1 for h in r.headers if h.lower() == "x-data-version") == 1


def test_one_request_never_reopens_mid_request(two_files):
    """The instance resolves the symlink once; a move during the request does not change its file."""
    s = LapseSQLiteSearch.from_django_settings()
    con1 = s.connection()
    _repoint(two_files, "snapshot_b.db")
    con2 = s.connection()
    assert con1 is con2
    assert s.data_version() == "20260901.1"
    fresh = LapseSQLiteSearch.from_django_settings()   # the next request on this thread
    assert fresh.connection() is not con1
    assert fresh.data_version() == "20260929.1"


def test_unstamped_file_falls_back_to_its_name(two_files):
    c = str(_TMP / "full2.db")
    if os.path.exists(c):
        os.unlink(c)
    _build_search_db(c)
    _repoint(two_files, "full2.db")
    assert LapseSQLiteSearch.from_django_settings().data_version() == "full2"
