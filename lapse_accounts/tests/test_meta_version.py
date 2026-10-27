"""GET /api/v1/meta/ names the data version the API serves (PatentRef 1.18.0).

On 2026-10-27 a reader saw data_version 20261020.2 in /api/v1/meta/ at 14:05 UTC while the API's X-Data-Version said
20261027.1 (the weekly grants swap of 06:56 UTC). The request log showed that no request for /api/v1/meta/ reached the
API between 11:22 and 15:13 UTC, and every worker had started at 07:36 UTC, after the swap: the copy came from a cache
on the way, which the answer's "public, max-age=60" allowed. Since 1.18.0 the answer says no-cache.

These tests hold the server side to it with two search files and a symlink that moves between requests on one thread,
with no worker reload (the swap of lapse refresh --swap, before its reload): meta's data_version and its X-Data-Version
equal the X-Data-Version of a data answer and of an answer whose view never reads the search file (a 403 without a key),
before and after the swap and after the rollback. Before 1.18.0 that 403 named the file the thread had opened for an
earlier request.
"""
from API.search_sqlite import LapseSQLiteSearch, begin_request, request_used_search
from lapse_accounts import public
from lapse_accounts.tests.test_data_version import _get, _repoint, two_files  # noqa: F401 (the fixture)


def _versions(client, key):
    """(meta body, meta header, data answer header, keyless 403 header), in that order, on this thread."""
    meta = client.get("/api/v1/meta/")
    assert meta.status_code == 200
    data = _get(client, key)
    assert data.status_code == 200
    keyless = client.get("/api/v1/patent/")
    assert keyless.status_code == 403
    return meta.json()["data_version"], meta["X-Data-Version"], data["X-Data-Version"], keyless["X-Data-Version"]


def test_meta_names_the_version_the_api_serves_across_a_swap(client, user_key, two_files):  # noqa: F811
    _, key, _ = user_key
    assert _versions(client, key) == ("20260901.1",) * 4
    _repoint(two_files, "snapshot_b.db")          # the swap: a rename over the symlink, no reload
    assert _versions(client, key) == ("20260929.1",) * 4
    _repoint(two_files, "snapshot_a.db")          # the rollback
    assert _versions(client, key) == ("20260901.1",) * 4


def test_an_answer_that_reads_no_search_file_names_the_file_in_service(client, user_key, two_files):  # noqa: F811
    """The thread holds a connection to file A from a data request; the symlink moves to B; the next answer on that
    thread whose view never reads the search file names B, as meta does, not the A the thread still holds."""
    _, key, _ = user_key
    assert _get(client, key)["X-Data-Version"] == "20260901.1"
    _repoint(two_files, "snapshot_b.db")
    keyless = client.get("/api/v1/patent/")
    assert keyless.status_code == 403 and keyless["X-Data-Version"] == "20260929.1"
    usage = client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=key)
    assert usage["X-Data-Version"] == "20260929.1"
    meta = client.get("/api/v1/meta/")
    assert meta.json()["data_version"] == meta["X-Data-Version"] == "20260929.1"


def test_a_data_answer_still_names_the_file_that_produced_its_body(two_files):  # noqa: F811
    """The gate 4.3 rule stays: a request that read the search file keeps the version of its own connection, even when
    the symlink moved while it ran (the next request reopens)."""
    begin_request()
    assert not request_used_search()
    s = LapseSQLiteSearch.from_django_settings()
    assert s.data_version() == "20260901.1" and request_used_search()
    _repoint(two_files, "snapshot_b.db")
    from API.lapse_errors import DataVersionMiddleware

    assert DataVersionMiddleware._version() == "20260901.1"   # this request read A
    begin_request()
    assert DataVersionMiddleware._version() == "20260929.1"   # the next one reads nothing: the file in service


def test_meta_asks_every_cache_to_check_with_the_api(client, two_files):  # noqa: F811
    r = client.get("/api/v1/meta/")
    assert r["Cache-Control"] == public.META_CACHE == "no-cache"
    assert "max-age" not in r["Cache-Control"] and "public" not in r["Cache-Control"]
    assert r.json()["generated_at"].endswith("Z")


def test_health_names_the_same_version(client, two_files):  # noqa: F811
    _repoint(two_files, "snapshot_b.db")
    h = client.get("/api/v1/meta/health/")
    assert h.json()["data_version"] == h["X-Data-Version"] == client.get("/api/v1/meta/").json()["data_version"] == "20260929.1"
