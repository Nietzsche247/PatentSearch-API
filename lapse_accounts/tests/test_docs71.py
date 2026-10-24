"""What the docs say about requests and answers since PatentRef 1.13.0 (the gate 7.1 notebook run's nine gaps), as this server
answers it. A search database in the layout lapse_tools/build_sample.py writes, with patents, their inventors and other
references:

  1. /api/v1/patent/otherreference/ (upstream's path before its release 2.2.0) is the other_reference view: the same answer,
     GET and POST, with and without the trailing slash
  2. raw JSON in a GET query string works as percent-encoded JSON does, and a "+" in it is read as a space
  3. list routes answer with or without the trailing slash; a detail route without it answers 301 to the slash form
  4. a number field takes a numeric string ("2018"); a decimal on an integer field matches nothing; any other string is 400 ERR_Q
  5. country codes are exact and case-sensitive on a field with a keyword form ("GB" matches, "UK" and "gb" do not)
  6. the inventor link is <base>/api/v1/inventor/<inventor_id>/ and comes back beside inventor_id
  7. inventors.disambiguated comes back only when f names it; the whole group carries the upstream fields
  8. nested rows come back in their source order (_ord), not sorted by a field
  9. the app's own body cap (65,536 bytes) is above the edge's 64,000, so the edge's 413 is what a caller sees; the error page
     says so

  python -m pytest lapse_accounts/tests/test_docs71.py -q -p no:cacheprovider -o addopts=""
"""
import json
import os
import sqlite3

import pytest
from django.conf import settings

from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP

P = "/api/v1/patent/"


def build_db(path):
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_indices VALUES (?,?,?)", [("patents", "patents", "patent_id"),
                                                                 ("other_references", "other_references", "uuid")])
    con.executemany("INSERT INTO _lapse_fields VALUES (?,?,?,?,?)", [
        ("patents", "", "patent_id", "keyword", 0), ("patents", "", "patent_title", "text", 1),
        ("patents", "", "patent_date", "date", 0), ("patents", "", "patent_year", "integer", 0),
        ("patents", "", "withdrawn", "boolean", 0),
        ("patents", "inventors", "inventor_id", "keyword", 0), ("patents", "inventors", "inventor_name_last", "text", 1),
        ("patents", "inventors", "inventor_country", "text", 1), ("patents", "inventors", "inventor_sequence", "integer", 0),
        ("patents", "inventors", "disambiguated", "boolean", 0),
        ("other_references", "", "uuid", "keyword", 0), ("other_references", "", "patent_id", "keyword", 0),
        ("other_references", "", "patent_zero_prefix", "keyword", 0), ("other_references", "", "reference_sequence", "keyword", 0),
        ("other_references", "", "reference_text", "text", 0)])
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_title TEXT, patent_date TEXT, patent_year INTEGER, withdrawn INT)")
    con.executemany("INSERT INTO patents VALUES (?,?,?,?,0)", [
        ("10000000", "Coherent LADAR using intra-pixel quadrature detection", "2018-06-19", 2018),
        ("10000001", "Injection molding machine and mold thickness control method", "2018-06-19", 2018),
        ("12751376", "Modular planter systems", "2026-10-06", 2026)])
    con.execute("CREATE TABLE patents__inventors(_pid TEXT NOT NULL, _ord INTEGER, inventor_id TEXT, inventor_name_last TEXT, "
                "inventor_country TEXT, inventor_sequence INTEGER, disambiguated INTEGER DEFAULT 1)")
    # written out of order on purpose: the answer follows _ord (the source order), never the rowid
    con.executemany("INSERT INTO patents__inventors VALUES (?,?,?,?,?,?,?)", [
        ("10000000", 2, "fl:c_ln:carter-1", "Carter", "US", 2, 1),
        ("10000000", 0, "fl:a_ln:abbott-1", "Abbott", "GB", 0, 1),
        ("10000000", 1, "fl:b_ln:van dyke-1", "Van Dyke", "US", 1, 1),
        ("10000001", 0, "fl:d_ln:dorn-1", "Dorn", "DE", 0, 1),
        # a weekly-grant row: no id, the name as printed, disambiguated 0; its source (the grant XML) lists the second
        # inventor first, and the answer keeps that order
        ("12751376", 0, None, "Zeta", "US", 1, 0),
        ("12751376", 1, None, "Alpha", "US", 0, 0)])
    con.execute("CREATE TABLE other_references(uuid TEXT PRIMARY KEY, patent_id TEXT, patent TEXT, patent_zero_prefix TEXT, "
                "reference_sequence TEXT, reference_text TEXT)")
    con.executemany("INSERT INTO other_references VALUES (?,?,?,?,?,?)", [
        ("10000000-0", "10000000", "10000000", "10000000", "0", "Smith, Coherent detection, 2015."),
        ("10000000-1", "10000000", "10000000", "10000000", "1", "Jones, Quadrature receivers, 2016."),
        ("10000001-0", "10000001", "10000001", "10000001", "0", "Molding handbook, 3rd ed.")])
    con.commit()
    con.close()


@pytest.fixture
def env():
    path = str(_TMP / "docs71.db")
    build_db(path)
    old = dict(settings.LAPSE_SQLITE)
    old_cat = getattr(settings, "LAPSE_CATALOG_PATH", None)
    settings.LAPSE_CATALOG_PATH = str(_TMP / "no_catalog.db")
    settings.LAPSE_SQLITE["path"] = path
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    try:
        yield path
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        settings.LAPSE_CATALOG_PATH = old_cat
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()


def get(client, key, route, q=None, f=None, o=None):
    params = {k: json.dumps(v) for k, v in (("q", q), ("f", f), ("o", o)) if v is not None}
    return client.get(route, params, HTTP_X_API_KEY=key)


def ok(r):
    assert r.status_code == 200, (r.status_code, r.content[:300])
    return r.json()


def test_otherreference_is_the_other_reference_view(client, user_key, env):
    from django.urls import resolve

    from API.endpoints.otherref_endpoint_configuration import OtherReferenceList

    for path in ("/api/v1/patent/otherreference/", "/api/v1/patent/otherreference", "/api/v1/patent/other_reference/"):
        assert resolve(path).func.view_class is OtherReferenceList, path
    _, key, _ = user_key
    q = {"patent_id": "10000000"}
    want = ok(get(client, key, "/api/v1/patent/other_reference/", q))
    assert want["total_hits"] == 2 and [r["reference_sequence"] for r in want["other_references"]] == ["0", "1"]
    assert ok(get(client, key, "/api/v1/patent/otherreference/", q)) == want
    assert ok(get(client, key, "/api/v1/patent/otherreference", q)) == want  # no redirect: the list route takes both forms
    r = client.post("/api/v1/patent/otherreference/", data=json.dumps({"q": q}), content_type="application/json", HTTP_X_API_KEY=key)
    assert ok(r) == want


def test_raw_json_in_a_get_query_string(client, user_key, env):
    _, key, _ = user_key
    raw = ok(client.get(P + '?q={"patent_id":"10000000"}&f=["patent_id","patent_title"]', HTTP_X_API_KEY=key))
    enc = ok(get(client, key, P, {"patent_id": "10000000"}, ["patent_id", "patent_title"]))
    assert raw == enc and raw["total_hits"] == 1
    # a "+" in the query string is a space, as in any form-encoded query string: send a literal plus as %2B
    plus = ok(client.get(P + '?q={"inventors.inventor_name_last":"Van+Dyke"}&f=["patent_id"]', HTTP_X_API_KEY=key))
    assert plus["total_hits"] == 1 and plus["patents"] == [{"patent_id": "10000000"}]


def test_trailing_slash(client, user_key, env):
    _, key, _ = user_key
    with_slash = ok(get(client, key, P, {"patent_id": "10000001"}))
    assert ok(get(client, key, "/api/v1/patent", {"patent_id": "10000001"})) == with_slash  # a list route: both forms, no redirect
    r = client.get("/api/v1/patent/10000001", {"f": json.dumps(["patent_id"])}, HTTP_X_API_KEY=key)
    assert r.status_code == 301 and r["Location"].endswith("/api/v1/patent/10000001/?f=%5B%22patent_id%22%5D"), (r.status_code, dict(r.headers))
    r = client.post("/api/v1/patent/10000001", data="{}", content_type="application/json", HTTP_X_API_KEY=key)
    assert r.status_code == 301 and r["Location"].endswith("/api/v1/patent/10000001/")  # the body does not follow a redirect


def test_number_field_takes_a_numeric_string(client, user_key, env):
    _, key, _ = user_key
    want = ok(get(client, key, P, {"patent_year": 2018}))
    assert want["total_hits"] == 2
    assert ok(get(client, key, P, {"patent_year": "2018"})) == want
    assert ok(get(client, key, P, {"patent_year": " 2018.0 "})) == want
    assert ok(get(client, key, P, {"_gte": {"patent_year": "2019"}}))["total_hits"] == 1


def test_number_field_refuses_other_strings(client, user_key, env):
    _, key, _ = user_key
    assert ok(get(client, key, P, {"patent_year": "2018.5"}))["total_hits"] == 0  # a decimal on an integer field matches nothing
    r = get(client, key, P, {"patent_year": "abc"})
    assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q"
    assert r["X-Status-Reason"] == 'Invalid number supplied For input string: "abc"'


def test_country_codes_are_exact(client, user_key, env):
    _, key, _ = user_key
    assert ok(get(client, key, P, {"inventors.inventor_country": "GB"}, ["patent_id"]))["patents"] == [{"patent_id": "10000000"}]
    assert ok(get(client, key, P, {"inventors.inventor_country": "UK"}))["total_hits"] == 0
    assert ok(get(client, key, P, {"inventors.inventor_country": "gb"}))["total_hits"] == 0


def test_inventor_link_comes_beside_its_id(client, user_key, env):
    _, key, _ = user_key
    rows = ok(get(client, key, P, {"patent_id": "10000001"}, ["patent_id", "inventors.inventor_id"]))["patents"][0]["inventors"]
    assert rows[0]["inventor_id"] == "fl:d_ln:dorn-1"
    assert rows[0]["inventor"].startswith("http") and rows[0]["inventor"].endswith("/api/v1/inventor/fl:d_ln:dorn-1/")
    whole = ok(get(client, key, P, {"patent_id": "10000001"}, ["patent_id", "inventors"]))["patents"][0]["inventors"]
    assert whole[0]["inventor"] == rows[0]["inventor"]
    # the link has no column of its own: named alone it brings back no inventors group
    alone = ok(get(client, key, P, {"patent_id": "10000001"}, ["patent_id", "inventors.inventor"]))["patents"][0]
    assert "inventors" not in alone
    # a weekly-grant row has no inventor_id, so no link
    weekly = ok(get(client, key, P, {"patent_id": "12751376"}, ["patent_id", "inventors.inventor_id"]))["patents"][0]["inventors"]
    assert {r["inventor"] for r in weekly} == {None}


def test_disambiguated_only_when_named(client, user_key, env):
    _, key, _ = user_key
    q = {"_or": [{"patent_id": "10000000"}, {"patent_id": "12751376"}]}
    whole = ok(get(client, key, P, q, ["patent_id", "inventors"]))["patents"]
    assert all("disambiguated" not in row for p in whole for row in p["inventors"])
    named = ok(get(client, key, P, q, ["patent_id", "inventors", "inventors.disambiguated"]))["patents"]
    flags = {p["patent_id"]: [row["disambiguated"] for row in p["inventors"]] for p in named}
    assert flags == {"10000000": [True, True, True], "12751376": [False, False]}


def test_nested_rows_in_source_order(client, user_key, env):
    _, key, _ = user_key
    q = {"_or": [{"patent_id": "10000000"}, {"patent_id": "12751376"}]}
    got = ok(get(client, key, P, q, ["patent_id", "inventors.inventor_name_last", "inventors.inventor_sequence"]))["patents"]
    order = {p["patent_id"]: [(r["inventor_sequence"], r["inventor_name_last"]) for r in p["inventors"]] for p in got}
    # PatentsView rows: the source order is inventor_sequence; the weekly grant keeps its XML's order
    assert order == {"10000000": [(0, "Abbott"), (1, "Van Dyke"), (2, "Carter")], "12751376": [(1, "Zeta"), (0, "Alpha")]}


def test_the_app_body_cap_sits_above_the_edge_cap(client, user_key, env):
    from lapse_accounts import public

    assert int(settings.LAPSE_MAX_BODY) == 65536 > 64000
    _, key, _ = user_key
    head = b'{"q":{"patent_id":"10000000"},"f":["patent_id"],"x":"'
    for n, status in ((64001, 200), (65536, 200), (65537, 400)):
        body = head + b"a" * (n - len(head) - 2) + b'"}'
        assert len(body) == n
        r = client.post(P, data=body, content_type="application/json", HTTP_X_API_KEY=key)
        assert r.status_code == status, (n, r.status_code, r.content[:200])
    assert r["X-Status-Reason-Code"] == "ERR_Q" and "Request body too large" in r["X-Status-Reason"]
    page = public.ERROR_CODES["ERR_Q"]
    assert "q over 16 KB (16,384 bytes) in a POST body" in page and "a body over 64 KB" not in page
    assert "a request body over 64,000 bytes answers 413 with an empty body" in page
    assert "is over 4,094 bytes answers 400 with an HTML page" in page
