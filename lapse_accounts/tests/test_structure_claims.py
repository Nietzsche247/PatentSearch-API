"""Contract tests for GET /api/v1/structure/claims/<patent_id>/ (the structure layer, gate S0.2; API/lapse_structure.py).

A search file in the Lapse layout and a long-text file in build_text.py's layout (txt.g_claims with the claim text
zlib-compressed, the (patent_id, claim_sequence) index, _lapse_build.data_version), filled with the real claims of
claimgraph's fixtures (lapse_accounts/tests/claim_fixtures, 37 patents from 1976 to 2025). Checks: the answer's shape and its
version fields (structure_version, data_version as X-Data-Version, text_data_version as X-Text-Data-Version, as_of);
every span an offset into the claim text on every fixture, and the tree equal to claimgraph run on the fixture file;
404 NOT_FOUND for an unknown patent and for a patent without claim text, 400 ERR_Q, 501 without a long-text file, the
RFC 9457 form of each; the key rule (403 without a key); the LRU (hit, bounds) and the work cap (500 ERR_ES); the
wording of what the endpoint writes. claimgraph's own suite runs with the package, which is not part of this repository
(test_claimgraph_package.py checks the fork's side of it).

  python -m pytest lapse_accounts/tests/test_structure_claims.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import csv
import json
import os
import re
import sqlite3
import uuid
import zlib
from pathlib import Path

import pytest
from django.conf import settings

from API import lapse_structure, lapse_wording
from API.lapse_claimgraph import claimgraph
from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "lapse_accounts" / "tests" / "claim_fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
DATA_VERSION = "20261027.2"
TEXT_VERSION = "text_test.1"
NO_TEXT = "12999999"     # in the search file, no claims in the long-text file (a grant after its last grant date)
UNKNOWN = "9999998"      # in neither file
PATH = "/api/v1/structure/claims/"
csv.field_size_limit(2 ** 31 - 1)


def fixture_rows(pid):
    with (FIXTURES / MANIFEST[pid]["file"]).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    assert rows[0] == ["patent_id", "claim_sequence", "claim_text", "dependent", "claim_number", "exemplary"]
    return rows[1:]


def build_files(search, text):
    for p in (search, text):
        if os.path.exists(p):
            os.unlink(p)
    con = sqlite3.connect(search)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", DATA_VERSION), ("release", "20261027")])
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, patent_type TEXT, withdrawn INT)")
    con.executemany("INSERT INTO patents VALUES (?,?,?,?,?,0)", [(p, p, "t", "2020-01-07", "utility") for p in list(MANIFEST) + [NO_TEXT]])
    con.commit()
    con.close()
    con = sqlite3.connect(text)
    con.execute('CREATE TABLE "g_claims" ("uuid" TEXT PRIMARY KEY, "patent_id" TEXT, "patent_zero_prefix" TEXT, "claim_number" TEXT, '
                '"claim_dependent" TEXT, "claim_text" BLOB, "claim_sequence" INTEGER, "exemplary" INTEGER)')
    con.execute('CREATE INDEX "ix_g_claims_patent_id_claim_sequence" ON "g_claims"("patent_id", "claim_sequence")')
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", TEXT_VERSION), ("kind", "text")])
    for pid in MANIFEST:
        for p, seq, text_, dep, num, ex in fixture_rows(pid):
            con.execute("INSERT INTO g_claims VALUES (?,?,?,?,?,?,?,?)",
                        (f"{p}-{seq}", p, p, num, dep or None, zlib.compress(text_.encode("utf-8"), 6), int(seq) if seq.isdigit() else None,
                         int(ex) if ex.isdigit() else None))
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def files():
    search, text = str(_TMP / "structure_search.db"), str(_TMP / "structure_text.db")
    build_files(search, text)
    return search, text


def _swap(search, text):
    old = dict(settings.LAPSE_SQLITE)
    old_text = getattr(settings, "LAPSE_TEXT_PATH", None)
    settings.LAPSE_SQLITE["path"] = search
    settings.LAPSE_TEXT_PATH = text
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    return old, old_text


def _restore(old, old_text):
    settings.LAPSE_SQLITE.clear()
    settings.LAPSE_SQLITE.update(old)
    settings.LAPSE_TEXT_PATH = old_text
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()


@pytest.fixture
def env(files):
    old, old_text = _swap(*files)
    try:
        yield files
    finally:
        _restore(old, old_text)


@pytest.fixture
def no_text_env(files):
    old, old_text = _swap(files[0], str(_TMP / "no_such_text.db"))
    try:
        yield
    finally:
        _restore(old, old_text)


@pytest.fixture
def keys(client, token_factory):
    """Fresh Free keys on demand (the tests' monthly allowance is 5 requests a key, LAPSE_FREE_MONTHLY_LIMIT), each from an
    account of its own email domain (new accounts per domain and day are limited, LAPSE_SIGNUP_DOMAIN_DAILY)."""
    def mint():
        sub = str(uuid.uuid4())
        token = token_factory(sub=sub, email=f"user-{sub[:8]}@d{sub[:8]}.example.test")
        r = client.post("/api/v1/meta/keys/", data=json.dumps({"terms_version": settings.LAPSE_TERMS_VERSION}),
                        content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token)
        assert r.status_code == 201, r.content
        return r.json()["api_key"]

    state = {"key": None, "used": 0}

    def key():
        if state["key"] is None or state["used"] >= 5:
            state["key"], state["used"] = mint(), 0
        state["used"] += 1
        return state["key"]
    return key


def get(client, key, pid, **headers):
    return client.get(PATH + pid + "/", HTTP_X_API_KEY=key, **headers)


def spans(claim):
    for k in ("preamble", "transition"):
        if claim[k]:
            yield claim[k]
    yield from claim["elements"]
    yield from claim["terms"]
    yield from claim["claim_refs"]


# ------------------------------------------------------------------ the answer

def test_shape_and_versions(client, keys, env):
    r = get(client, keys(), "3995389")
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "application/json"
    js = r.json()
    assert list(js)[:8] == ["error", "patent_id", "patent_unit_id", "structure_version", "generator", "data_version",
                            "text_data_version", "as_of"]
    assert js["error"] is False and js["patent_id"] == "3995389" and js["patent_unit_id"] == "US3995389"
    assert js["structure_version"] == claimgraph.STRUCTURE_VERSION == "2"
    assert js["generator"] == "claimgraph " + claimgraph.__version__
    assert js["data_version"] == DATA_VERSION == r["X-Data-Version"]
    assert js["text_data_version"] == TEXT_VERSION == r["X-Text-Data-Version"]
    assert js["as_of"] == "2026-10-27"
    assert {"kind", "utility_like", "claims", "relations", "flags"} <= set(js)
    assert len(js["claims"]) == MANIFEST["3995389"]["claims"]
    c1 = js["claims"][0]
    assert c1["claim_id"] == "US3995389-c1" and c1["independent"] is True and c1["depends_on"] == []
    assert c1["preamble"]["text"] == c1["text"][c1["preamble"]["start"]:c1["preamble"]["end"]]
    assert {"src": "US3995389-c1", "rel": "part_of", "dst": "US3995389"} in js["relations"]
    assert r["Cache-Control"] == "public, max-age=300"
    assert re.fullmatch(r'claimtree;desc="miss";dur=[0-9.]+', r["Server-Timing"])


def test_offsets_valid_and_tree_equal_to_claimgraph_on_every_fixture(client, keys, env):
    for pid in sorted(MANIFEST):
        r = get(client, keys(), pid)
        assert r.status_code == 200, (pid, r.content)
        js = r.json()
        assert claimgraph.validate_patent(js) == [], pid
        for c in js["claims"]:
            for u in spans(c):
                assert c["text"][u["start"]:u["end"]] == u["text"], (pid, u)
        direct = json.loads(json.dumps(claimgraph.build_patent(pid, [claimgraph.ClaimRow(*row) for row in fixture_rows(pid)])))
        assert {k: js[k] for k in direct} == direct, pid
        assert len(js["claims"]) == MANIFEST[pid]["claims"], pid


def test_trailing_slash_optional_and_prefix_read(client, keys, env):
    r = client.get(PATH + "US3995389", HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json()["patent_id"] == "3995389"
    r = client.get(PATH + "RE42441/", HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json()["kind"] == "reissue"
    r = client.get(PATH + "D1107373/", HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json()["utility_like"] is False


# ------------------------------------------------------------------ errors, in the upstream shape and RFC 9457

def test_404_unknown_patent(client, keys, env):
    r = get(client, keys(), UNKNOWN)
    assert r.status_code == 404 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "NOT_FOUND" and r["X-Status-Reason"].startswith("Unknown patent_id")
    p = get(client, keys(), UNKNOWN, HTTP_ACCEPT="application/problem+json")
    assert p.status_code == 404 and p["Content-Type"] == "application/problem+json"
    pj = p.json()
    assert pj["type"] == "https://patentref.io/docs/errors/NOT_FOUND" and pj["status"] == 404 and pj["code"] == "NOT_FOUND"
    assert pj["instance"] == PATH + UNKNOWN + "/" and pj["detail"].startswith("Unknown patent_id") and pj["data_version"] == DATA_VERSION


def test_404_patent_without_claim_text(client, keys, env):
    r = get(client, keys(), NO_TEXT)
    assert r.status_code == 404 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "NOT_FOUND"
    assert r["X-Status-Reason"].startswith(f"No claim text for patent_id '{NO_TEXT}'") and TEXT_VERSION in r["X-Status-Reason"]
    pj = get(client, keys(), NO_TEXT, HTTP_ACCEPT="application/problem+json").json()
    assert pj["code"] == "NOT_FOUND" and pj["detail"].startswith("No claim text")


def test_400_not_a_patent_number(client, keys, env):
    r = client.get(PATH + "abc-1/", HTTP_X_API_KEY=keys())
    assert r.status_code == 400 and r.json() == {"error": True} and r["X-Status-Reason-Code"] == "ERR_Q"
    pj = client.get(PATH + "abc-1/", HTTP_X_API_KEY=keys(), HTTP_ACCEPT="application/problem+json").json()
    assert pj["code"] == "ERR_Q" and pj["status"] == 400 and pj["type"].endswith("/ERR_Q")


def test_501_without_a_long_text_file(client, keys, no_text_env):
    r = get(client, keys(), "3995389")
    assert r.status_code == 501 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED" and "'g_claims' data set" in r["X-Status-Reason"]


def test_403_without_a_key_as_every_data_path(client, env):
    r = client.get(PATH + "3995389/")
    assert r.status_code == 403
    assert r.json() == client.get("/api/v1/lapse/status/3995389/").json()   # upstream's body, the same on every data path


# ------------------------------------------------------------------ computed on read: the LRU and the work cap

def test_cache_hit_is_the_same_bytes(client, keys, env):
    a = get(client, keys(), "10034028")
    b = get(client, keys(), "10034028")
    assert a.status_code == b.status_code == 200 and a.content == b.content
    assert 'desc="miss"' in a["Server-Timing"] and 'desc="hit"' in b["Server-Timing"]
    assert lapse_structure.CACHE.stats()["entries"] >= 1


def test_answer_cache_bounds():
    c = lapse_structure.AnswerCache(entries=3, max_bytes=10, item_max=6)
    for k in "abc":
        c.put(k, b"xx")
    assert c.stats() == {"entries": 3, "bytes": 6}
    c.get("a")              # a is now the newest
    c.put("d", b"xx")       # over 3 entries: b, the oldest, goes
    assert c.get("b") is None and c.get("a") == b"xx" and c.stats()["entries"] == 3
    c.put("e", b"xxxxxx")   # 12 bytes would pass 10: the oldest go until it fits
    assert c.stats()["bytes"] <= 10 and c.get("e") == b"xxxxxx"
    c.put("f", b"xxxxxxx")  # over item_max: not kept
    assert c.get("f") is None
    assert lapse_structure.CACHE_ENTRIES == 256 and lapse_structure.CACHE_BYTES == 32 * 1024 * 1024


def test_work_cap_answers_500_err_es(client, keys, env, monkeypatch):
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 3)
    r = get(client, keys(), "10034028")   # 18 claims
    assert r.status_code == 500 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_ES" and "work cap" in r["X-Status-Reason"]
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 1000)
    monkeypatch.setattr(lapse_structure, "MAX_TEXT_CHARS", 100)
    r = get(client, keys(), "3995389")
    assert r.status_code == 500 and "characters" in r["X-Status-Reason"]
    assert lapse_structure.MAX_CLAIMS == 1000


def test_caps_cover_the_largest_claim_sets():
    # the corpus's largest claim sets (the long-text file 20261006.4, read on the box 2026-10-29): 887 claims of 6684189,
    # 868 of 5095054, 803 of 7096160, 760 of 7970722, at most 227,477 characters
    assert lapse_structure.MAX_CLAIMS >= 887 and lapse_structure.MAX_TEXT_CHARS >= 227_477
    assert lapse_structure.READ_SECONDS <= 5


# ------------------------------------------------------------------ wording and the vendored package

def test_wording_of_what_the_endpoint_writes(client, keys, env):
    reasons = [get(client, keys(), x)["X-Status-Reason"] for x in (UNKNOWN, NO_TEXT)]
    codes = set()
    for pid in ("3995389", "RE42441", "10034028", "4156107"):
        js = get(client, keys(), pid).json()
        for c in js["claims"]:
            codes |= {f["code"] for f in c["flags"]} | {c["category"]}
        codes |= {f["code"] for f in js["flags"]}
    for text in reasons + sorted(codes) + [lapse_structure.__doc__]:
        assert lapse_wording.check_banned(text) == [], text
    legal = re.compile(r"\b(valid|invalid|infring\w*|broad\w*|narrow\w*|covers?|defect\w*|improper|lacks?)\b", re.I)
    assert not [c for c in codes if legal.search(c.replace("_", " "))]

