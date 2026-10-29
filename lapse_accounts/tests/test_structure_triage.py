"""Contract tests for the triage table (gate S1.8L): POST and GET /api/v1/structure/triage/ (API/lapse_triage.py) and its
page /triage/ (lapse_accounts/triage_page.py).

Files: a search file in the Lapse layout (patents with their application, term-of-grant, related-document and PCT rows,
fts_patents over titles and abstracts), a long-text file with txt.g_claims (zlib-compressed claim text, the (patent_id,
claim_sequence) index) and its contentless FTS5 index fts_g_claims (detail=full, rowids as the rows', as the box's), and a
Lapse catalog for the status engine (lapse.expiry from a patentref checkout: LAPSE_REPO_DIR, else a sibling checkout named
lapse, patentref or pr, else /srv/lapse). Six made-up patents about pool cleaners, a skimmer basket and a device; one
without claim text; one number in no file. Every file has a name of its own, so no test deletes a file a connection holds.

  python -m pytest lapse_accounts/tests/test_structure_triage.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import base64
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import uuid
import zlib
from pathlib import Path

import pytest
from django.conf import settings

from API import lapse_claimgraph, lapse_structure, lapse_triage
from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts import metering, ratelimit, throttling, triage_page
from lapse_accounts.tests.conftest import _TMP

ROOT = Path(__file__).resolve().parents[2]
PATH = "/api/v1/structure/triage/"
DATA_VERSION = "20261027.2"
TEXT_VERSION = "text_test.zc"
TOKENIZE = "unicode61 remove_diacritics 0 tokenchars '_'"

CLAIMS = {   # patent_id: claim texts in order (claim n is CLAIMS[pid][n - 1]); a claim naming "of claim 1" depends on claim 1
    "5000001": ["1. A pool cleaner comprising: a housing; a drive motor in the housing; and a filter bag attached to the housing.",
                "2. The pool cleaner of claim 1, wherein the filter bag is removable.",
                "3. The pool cleaner of claim 1, further comprising a brush turned by the drive motor.",
                "4. A method of cleaning a pool, comprising: moving a pool cleaner having a drive motor across a floor of the pool; "
                "and collecting debris in a filter bag."],
    "5000002": ["1. A pool cleaner comprising: a brush; and an electric drive motor that turns the brush.",
                "2. A pool cleaner comprising: a chassis; and a pump."],
    "5000004": ["1. A skimmer basket comprising: a rim; and a mesh liner."],
    "5000005": ["1. An electronic device comprising: a display.",
                "2. The device of claim 1, wherein the device has a housing."],
    "5000006": ["1. A pool cleaner comprising: a housing; and a brush mounted to the housing.",
                "2. The pool cleaner of claim 1, wherein the brush is a roller brush."],
}
PATENTS = [   # patent_id, title, grant date, filing date
    ("5000001", "Pool cleaner", "2018-06-19", "2015-03-02"),
    ("5000002", "Pool cleaner with an electric drive motor", "1999-06-15", "1997-03-02"),
    ("5000003", "Pool ladder", "2021-06-01", "2020-01-02"),        # no claim text in the long-text file
    ("5000004", "Skimmer basket", "2012-01-03", "2007-05-25"),
    ("5000005", "Electronic device", "2019-02-05", "2016-01-04"),
    ("5000006", "Pool cleaner brush", "2020-03-03", "2017-08-01"),
]
CATALOG = [   # id, filing, grant, fee_status, lapse_date
    ("US5000001", "2015-03-02", "2018-06-19", None, None),
    ("US5000002", "1997-03-02", "1999-06-15", None, None),
    ("US5000004", "2007-05-25", "2012-01-03", "lapsed", "2016-01-03"),
    ("US5000005", "2016-01-04", "2019-02-05", None, None),
    ("US5000006", "2017-08-01", "2020-03-03", None, None),
]
BANNED = re.compile("|".join(re.escape(w) for w in lapse_triage.BANNED_WORDS) + r"|\bcover(?:s|ed|age)?\b|" + chr(0x2014))


def find_repo_dir():
    """The patentref checkout whose lapse/expiry.py the status engine imports (as test_lapse_group.py finds it)."""
    if os.environ.get("LAPSE_REPO_DIR"):
        return os.environ["LAPSE_REPO_DIR"]
    for cand in (ROOT.parent / "lapse", ROOT.parent / "patentref", ROOT.parent / "pr", Path("/srv/lapse")):
        if (cand / "lapse" / "expiry.py").is_file():
            return str(cand)
    return None


def build_search(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", DATA_VERSION), ("release", "20261027")])
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    for field, t, kw in [("patent_id", "keyword", 0), ("patent_zero_prefix", "keyword", 0), ("patent_title", "text", 1),
                         ("patent_date", "date", 0), ("patent_type", "keyword", 0), ("withdrawn", "boolean", 0)]:
        con.execute("INSERT INTO _lapse_fields VALUES ('patents','',?,?,?)", (field, t, kw))
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, "
                "patent_type TEXT, withdrawn INT, patent_abstract TEXT, gov_interest_statement TEXT)")
    con.execute("CREATE TABLE patents__application(_pid TEXT NOT NULL, _ord INTEGER, application_id TEXT, filing_date TEXT)")
    con.execute("CREATE TABLE patents__us_term_of_grant(_pid TEXT NOT NULL, _ord INTEGER, term_grant TEXT, term_extension TEXT, "
                "term_disclaimer TEXT, disclaimer_date TEXT)")
    con.execute("CREATE TABLE patents__us_related_documents(_pid TEXT NOT NULL, _ord INTEGER, related_doc_type TEXT, related_doc_kind TEXT, "
                "related_doc_number TEXT, published_country TEXT, related_doc_published_date TEXT, related_doc_status TEXT, "
                "related_doc_sequence INTEGER, wipo_kind TEXT)")
    con.execute("CREATE TABLE patents__pct_data(_pid TEXT NOT NULL, _ord INTEGER, published_filed_date TEXT, pct_102_date TEXT, "
                "pct_371_date TEXT, application_kind TEXT, pct_doc_number TEXT, pct_doc_type TEXT)")
    for pid, title, granted, filed in PATENTS:
        con.execute("INSERT INTO patents VALUES (?,?,?,?,?,0,?,NULL)", (pid, pid, title, granted, "utility", title + " for a swimming pool."))
        con.execute("INSERT INTO patents__application VALUES (?,0,?,?)", (pid, "a" + pid, filed))
        con.execute("INSERT INTO patents__us_term_of_grant VALUES (?,0,'','0','','')", (pid,))
    con.execute("CREATE VIRTUAL TABLE fts_patents USING fts5(gov_interest_statement, patent_abstract, patent_title, content='patents', "
                f"content_rowid='rowid', tokenize=\"{TOKENIZE}\")")
    con.execute("INSERT INTO fts_patents(fts_patents) VALUES ('rebuild')")
    con.commit()
    con.close()


def build_text(path, with_index=True):
    con = sqlite3.connect(path)
    con.execute('CREATE TABLE "g_claims" ("uuid" TEXT PRIMARY KEY, "patent_id" TEXT, "patent_zero_prefix" TEXT, "claim_number" TEXT, '
                '"claim_dependent" TEXT, "claim_text" BLOB, "claim_sequence" INTEGER, "exemplary" INTEGER)')
    con.execute('CREATE INDEX "ix_g_claims_patent_id_claim_sequence" ON "g_claims"("patent_id", "claim_sequence")')
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", TEXT_VERSION), ("kind", "text")])
    if with_index:
        con.execute(f"CREATE VIRTUAL TABLE fts_g_claims USING fts5(claim_text, content='', detail=full, tokenize=\"{TOKENIZE}\")")
    rid = 0
    for pid in sorted(CLAIMS):   # oldest first, as the year files load: a higher rowid is a newer row
        for seq, text in enumerate(CLAIMS[pid]):
            rid += 1
            dep = "claim 1" if "of claim 1" in text else None
            con.execute("INSERT INTO g_claims(rowid, uuid, patent_id, patent_zero_prefix, claim_number, claim_dependent, claim_text, "
                        "claim_sequence, exemplary) VALUES (?,?,?,?,?,?,?,?,?)",
                        (rid, f"{pid}-{seq}", pid, pid, f"{seq + 1:05d}", dep, zlib.compress(text.encode("utf-8"), 6), seq, 1 if seq == 0 else 0))
            if with_index:
                con.execute("INSERT INTO fts_g_claims(rowid, claim_text) VALUES (?,?)", (rid, text))
    con.commit()
    con.close()


def build_catalog(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE catalog(id TEXT NOT NULL, filing_date TEXT, grant_date TEXT, fee_status TEXT, lapse_date TEXT, prior REAL, "
                "prior_pct REAL, fwd_early INTEGER, fwd_late INTEGER, fwd_cites INTEGER, fwd_recent INTEGER, grant_year INTEGER, "
                "reinstated_date TEXT, pta_days INTEGER, term_disclaimer INTEGER, disclaimer_date TEXT)")
    con.execute("CREATE UNIQUE INDEX catalog_id ON catalog(id)")
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [("data_version", DATA_VERSION), ("release", "20261027"),
                                                     ("fee_rule", "lapse:EXP.;reinstatement:EXPX,PMFG")])
    for cid, filed, granted, fee, lapsed in CATALOG:
        con.execute("INSERT INTO catalog(id, filing_date, grant_date, fee_status, lapse_date, fwd_early, fwd_late, fwd_cites, fwd_recent, "
                    "grant_year) VALUES (?,?,?,?,?,0,0,0,0,?)", (cid, filed, granted, fee, lapsed, int(granted[:4])))
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def files():
    tag = uuid.uuid4().hex[:8]
    out = {k: str(_TMP / f"triage_{k}_{tag}.db") for k in ("search", "text", "plain_text", "catalog")}
    build_search(out["search"])
    build_text(out["text"])
    build_text(out["plain_text"], with_index=False)
    build_catalog(out["catalog"])
    return out


def _reset():
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    lapse_triage.FACTS.clear()
    lapse_triage._DF.clear()
    triage_page._example.update(key=None, body=None)


@pytest.fixture
def env(files):
    repo = find_repo_dir()
    if not repo or not (Path(repo) / "lapse" / "expiry.py").is_file():
        pytest.fail("the status engine lapse.expiry lives in the patentref repo: set LAPSE_REPO_DIR to a patentref checkout",
                    pytrace=False)
    old = dict(settings.LAPSE_SQLITE)
    saved = {k: getattr(settings, k, None) for k in ("LAPSE_TEXT_PATH", "LAPSE_CATALOG_PATH", "LAPSE_REPO_DIR", "LAPSE_TRIAGE_EXAMPLE")}
    settings.LAPSE_SQLITE["path"] = files["search"]
    settings.LAPSE_TEXT_PATH = files["text"]
    settings.LAPSE_CATALOG_PATH = files["catalog"]
    settings.LAPSE_REPO_DIR = repo
    _reset()
    try:
        yield files
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        for k, v in saved.items():
            setattr(settings, k, v)
        _reset()


@pytest.fixture
def keys(client, token_factory):
    """Fresh Free keys on demand (5 requests a key in the tests), each from an account of its own email domain."""
    def mint():
        sub = str(uuid.uuid4())
        token = token_factory(sub=sub, email=f"user-{sub[:8]}@t{sub[:8]}.example.test")
        r = client.post("/api/v1/meta/keys/", data=json.dumps({"terms_version": settings.LAPSE_TERMS_VERSION}),
                        content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token)
        assert r.status_code == 201, r.content
        return r.json()["api_key"]

    state = {"key": None, "used": 0}

    def key(fresh=False):
        if fresh or state["key"] is None or state["used"] >= 5:
            state["key"], state["used"] = mint(), 0
        state["used"] += 1
        return state["key"]
    return key


def post(client, key, body, **extra):
    return client.post(PATH, data=json.dumps(body), content_type="application/json", HTTP_X_API_KEY=key, **extra)


def table(client, keys, **body):
    r = post(client, keys(), body)
    assert r.status_code == 200, (r.status_code, r.get("X-Status-Reason"), r.content[:300])
    return r, r.json()


def row_of(body, pid):
    return next(p for p in body["patents"] if p["patent_id"] == pid)


def cell_of(row, key):
    return next(c for c in row["cells"] if c["key"] == key)


ALL = ["5000001", "5000002", "5000003", "5000004", "5000005", "9999999"]
COMPS = ["drive motors", "a filter bag", "brush"]


# ------------------------------------------------------------------ reading the request (no server needed)

def test_a_pasted_list_is_read_the_way_people_write_numbers():
    ids, unread = lapse_triage.parse_patent_list(
        "US 10,905,426 B2\n10000000, US5418943A; D500,396 S | RE31177E\tpp12345 us-9876543-b1 6000000 6000000\n"
        "U.S. Pat. No. 7,861,317 and foo 2019012345678 D 600,001")
    assert ids == ["10905426", "10000000", "5418943", "D500396", "RE31177", "PP12345", "9876543", "6000000", "6000000", "7861317",
                   "D600001"]
    assert unread == ["FOO", "2019012345678"]
    assert lapse_triage.parse_patent_list("10905426,10000000")[0] == ["10905426", "10000000"]   # no thousands read across numbers
    assert lapse_triage.parse_patent_list("")[0] == []


def test_components_are_read_as_claim_tree_keys():
    assert lapse_triage.component_key("drive motors") == "drive motor"
    assert lapse_triage.component_key("a filter bag") == "filter bag"
    assert lapse_triage.component_key("filter bag") == "filter bag"      # "filter" reads as a verb to the term finder: the words stay
    assert lapse_triage.component_key("Pumps") == "pump"
    assert lapse_triage.component_key("  the   housing ") == "housing"
    assert lapse_triage.component_key("123") is None


def test_the_search_terms():
    assert lapse_triage.search_terms('"pool cleaner" Brush brush') == ["pool cleaner", "brush"]
    assert lapse_triage.search_terms('"drive motor') == ["drive", "motor"]
    assert lapse_triage.search_terms('""  ') == []


def test_positions():
    p = lapse_triage.position
    assert (p(2, 2, False), p(1, 2, True), p(0, 2, True), p(0, 2, False), p(0, 0, True)) == (
        "every_independent", "some_independent", "only_dependent", "absent", "only_dependent")


# ------------------------------------------------------------------ errors

def test_403_without_a_key(client, env):
    assert client.post(PATH, data=json.dumps({"patents": "5000001"}), content_type="application/json").status_code == 403
    assert client.get(PATH, {"patents": "5000001"}).status_code == 403


@pytest.mark.parametrize("body,reason", [
    ({}, "Send 'patents' (up to 500 patent numbers) or 'search' (words to find in the claims), one of the two"),
    ({"patents": "5000001", "search": "pool"}, "Send 'patents' (up to 500 patent numbers) or 'search' (words to find in the claims), one of the two"),
    ({"patents": "x y"}, "No patent number could be read in 'patents'"),
    ({"patents": " ".join(str(6000000 + i) for i in range(501))}, "At most 500 patent numbers in one table; this list has 501"),
    ({"patents": "5000001", "limit": 5}, "'limit' goes with 'search'; a list is read whole"),
    ({"search": "pool", "limit": 0}, "'limit' must be a whole number from 1 to 500"),
    ({"search": "pool", "limit": "many"}, "'limit' must be a whole number from 1 to 500"),
    ({"search": "pool", "limit": True}, "'limit' must be a whole number from 1 to 500"),
    ({"search": " ".join(f"w{i}" for i in range(13))}, "'search' takes at most 12 words and quoted phrases"),
    ({"search": "x" * 501}, "'search' over 500 characters"),
    ({"search": "..."}, "'search' has no words"),
    ({"search": 5}, "'search' must be a string"),
    ({"patents": "5000001", "components": ",".join(f"part{i}" for i in range(13))}, "At most 12 components in one table"),
    ({"patents": "5000001", "components": "x" * 81}, "A component is over 80 characters"),
    ({"patents": "5000001", "components": [{"a": 1}]}, "'components' must be a string or a list of strings"),
    ({"patents": "5000001", "output": "xml"}, "'output' must be json or csv"),
    ({"patents": "5000001", "sort": "x"}, "Unknown field 'sort': the triage table takes patents, search, limit, components, output"),
])
def test_400_err_q(client, keys, env, body, reason):
    r = post(client, keys(), body)
    assert r.status_code == 400 and r.json() == {"error": True}, r.content
    assert r["X-Status-Reason-Code"] == "ERR_Q" and r["X-Status-Reason"] == reason


def test_a_body_that_is_not_an_object_and_problem_details(client, keys, env):
    r = client.post(PATH, data="[1, 2]", content_type="application/json", HTTP_X_API_KEY=keys())
    assert r.status_code == 400 and r["X-Status-Reason"] == "The body must be a JSON object with 'patents' or 'search'"
    r = client.post(PATH, data="{not json", content_type="application/json", HTTP_X_API_KEY=keys())
    assert r.status_code == 400 and r["X-Status-Reason"].startswith("The body must be a JSON object: ")
    r = post(client, keys(), {"search": "..."}, HTTP_ACCEPT="application/problem+json")
    assert r.status_code == 400 and r["Content-Type"] == "application/problem+json"
    pd = r.json()
    assert pd["code"] == "ERR_Q" and pd["detail"] == "'search' has no words" and pd["instance"] == PATH
    assert pd["type"] == "https://patentref.io/docs/errors/ERR_Q"


def test_501_without_a_long_text_file_and_without_its_index(client, keys, env, files):
    settings.LAPSE_TEXT_PATH = str(_TMP / "no_such_text.db")
    _reset()
    r = post(client, keys(), {"patents": "5000001"})
    assert r.status_code == 501 and r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED" and "'g_claims' data set" in r["X-Status-Reason"]
    settings.LAPSE_TEXT_PATH = files["plain_text"]
    _reset()
    r = post(client, keys(), {"search": "pool"})
    assert r.status_code == 501 and "full-text index of the claims" in r["X-Status-Reason"]
    assert post(client, keys(), {"patents": "5000001"}).status_code == 200     # a list needs no index


# ------------------------------------------------------------------ the table

def test_shape_versions_headers_and_rows(client, keys, env):
    r, js = table(client, keys, patents="\n".join(ALL) + "\n5000001 bogus", components=", ".join(COMPS))
    assert list(js) == ["error", "data_version", "text_data_version", "as_of", "structure_version", "generator", "input", "components",
                        "positions", "complete", "time_limit_seconds", "status_available", "counts", "quotes", "export_columns", "patents"]
    assert js["error"] is False and js["data_version"] == DATA_VERSION and js["text_data_version"] == TEXT_VERSION
    assert js["as_of"] == "2026-10-27" and js["structure_version"] == lapse_claimgraph.STRUCTURE_VERSION
    assert js["generator"] == "claimgraph " + lapse_claimgraph.__version__
    assert r["X-Text-Data-Version"] == TEXT_VERSION and r["Cache-Control"] == "private, max-age=60"
    assert r["Server-Timing"].startswith('triage;desc="4 built, 0 from the cache";dur=')
    assert js["input"] == {"kind": "list", "patents_read": 6, "repeated": 1, "unread": ["BOGUS"], "unread_count": 1, "unread_components": []}
    assert js["components"] == [{"key": "drive motor", "from": "request", "asked": "drive motors", "patents": 2},
                                {"key": "filter bag", "from": "request", "asked": "a filter bag", "patents": 1},
                                {"key": "brush", "from": "request", "asked": "brush", "patents": 2}]
    assert js["positions"] == ["every_independent", "some_independent", "only_dependent", "absent"]
    assert js["complete"] is True and js["time_limit_seconds"] == 20.0 and js["status_available"] is True
    assert js["counts"] == {"patents": 6, "found": 5, "built": 4, "not_built": 1}
    assert js["export_columns"] == lapse_triage.EXPORT_COLUMNS
    assert [p["patent_id"] for p in js["patents"]] == ALL and [p["row"] for p in js["patents"]] == [1, 2, 3, 4, 5, 6]
    first = js["patents"][0]
    assert list(first) == ["row", "patent_id", "title", "patent_date", "permalink", "found", "built", "note", "status", "claims", "cells",
                           "components", "claim_parts"]
    assert first["permalink"] == f"http://testserver/p/5000001?v={DATA_VERSION}" and first["title"] == "Pool cleaner"
    assert first["claims"] == {"total": 4, "independent": [1, 4]}
    for pid, note, found in (("5000003", "no_claim_text", True), ("9999999", "not_in_data", False)):
        p = row_of(js, pid)
        assert (p["note"], p["found"], p["built"], p["cells"], p["components"], p["claim_parts"], p["claims"]) == (note, found, False, [], None, [], None)
    assert row_of(js, "9999999")["status"] is None and row_of(js, "9999999")["title"] is None


def test_positions_matches_and_quotes_in_the_claims(client, keys, env):
    _r, js = table(client, keys, patents=ALL, components=COMPS)
    p1 = row_of(js, "5000001")
    assert cell_of(p1, "drive motor") == {
        "key": "drive motor", "position": "every_independent", "match": "exact", "claim_keys": ["drive motor"],
        "independent_claims": [1, 4], "dependent_claims": [3],
        "quote": {"claim": 1, "claim_id": "US5000001-c1", "term_id": cell_of(p1, "drive motor")["quote"]["term_id"],
                  "start": CLAIMS["5000001"][0].index("a drive motor"), "end": CLAIMS["5000001"][0].index("a drive motor") + 13,
                  "text": "a drive motor"}}
    assert (cell_of(p1, "filter bag")["position"], cell_of(p1, "filter bag")["dependent_claims"]) == ("every_independent", [2])
    brush = cell_of(p1, "brush")
    assert (brush["position"], brush["independent_claims"], brush["dependent_claims"]) == ("only_dependent", [], [3])
    assert brush["quote"]["claim"] == 3 and brush["quote"]["text"] == "a brush"
    p2 = row_of(js, "5000002")
    dm = cell_of(p2, "drive motor")
    assert (dm["position"], dm["match"], dm["claim_keys"], dm["independent_claims"]) == ("some_independent", "partial", ["electric drive motor"], [1])
    assert dm["quote"]["text"] == "an electric drive motor"
    assert cell_of(p2, "brush")["position"] == "some_independent" and cell_of(p2, "brush")["match"] == "exact"
    assert cell_of(p2, "filter bag") == {"key": "filter bag", "position": "absent", "match": None, "claim_keys": [], "independent_claims": [],
                                        "dependent_claims": [], "quote": None}
    p4 = row_of(js, "5000004")
    assert [c["position"] for c in p4["cells"]] == ["absent", "absent", "absent"]
    assert p1["components"]["every_independent"][:3] == ["pool cleaner", "drive motor", "filter bag"]
    assert "housing" in p1["components"]["some_independent"] and "brush" in p1["components"]["only_dependent"]


def test_a_reference_back_counts_for_the_term_it_refers_to(client, keys, env):
    """Claim 2 of 5000005 says "the device", which claimgraph matched to claim 1's "an electronic device": the claim mentions
    the electronic device. "housing" is introduced in the dependent claim only."""
    _r, js = table(client, keys, patents=["5000005"], components=["electronic device", "housing", "device"])
    p = row_of(js, "5000005")
    ed, hs, dv = (cell_of(p, k) for k in ("electronic device", "housing", "device"))
    assert (ed["position"], ed["independent_claims"], ed["dependent_claims"]) == ("every_independent", [1], [2])
    assert (hs["position"], hs["dependent_claims"]) == ("only_dependent", [2])
    assert (dv["position"], dv["match"], dv["claim_keys"]) == ("every_independent", "exact", ["device", "electronic device"])
    assert p["components"] == {"every_independent": ["electronic device", "display"], "some_independent": [], "only_dependent": ["housing"]}


def test_the_status_is_the_status_endpoints(client, keys, env):
    _r, js = table(client, keys, patents=ALL[:5])
    want = {"5000001": "active", "5000002": "expired_term", "5000003": "active", "5000004": "expired_fee_nonpayment", "5000005": "active"}
    for p in js["patents"]:
        st = p["status"]
        assert st["status"] == want[p["patent_id"]], p["patent_id"]
        rec = client.get(f"/api/v1/lapse/status/{p['patent_id']}/", HTTP_X_API_KEY=keys()).json()
        assert st == {k: rec[k] for k in ("status", "basis", "reinstatement_possible", "reinstatement_basis", "expiry_date_estimated",
                                          "lapse_date", "reinstated_date", "as_of")}, p["patent_id"]


def test_every_quote_is_the_claim_text_at_its_offsets(client, keys, env):
    _r, js = table(client, keys, patents=ALL, components=COMPS + ["housing", "pump"])
    n = 0
    for p in js["patents"]:
        quotes = [c["quote"] for c in p["cells"] if c["quote"]] + [part for cl in p["claim_parts"] for part in cl["parts"]]
        for q in quotes:
            text = CLAIMS[p["patent_id"]][q["claim"] - 1]
            assert q["claim_id"] == f"US{p['patent_id']}-c{q['claim']}"
            assert text[q["start"]:q["end"]] == q["text"] and q["text"], (p["patent_id"], q)
            n += 1
    assert n == js["quotes"] and n > 30


def test_claim_parts_are_the_independent_claims_parts(client, keys, env):
    _r, js = table(client, keys, patents=["5000001"])
    p = row_of(js, "5000001")
    assert [cl["claim"] for cl in p["claim_parts"]] == [1, 4]
    c1 = p["claim_parts"][0]
    assert c1["claim_id"] == "US5000001-c1"
    assert [(x["part"], x["text"]) for x in c1["parts"]] == [
        ("preamble", "A pool cleaner"), ("transition", "comprising"), ("element", "a housing"),
        ("element", "a drive motor in the housing"), ("element", "a filter bag attached to the housing")]
    tree = client.get("/api/v1/structure/claims/5000001/", HTTP_X_API_KEY=keys()).json()
    c = tree["claims"][0]
    assert c1["category"] == c["category"] and p["claim_parts"][1]["category"] == tree["claims"][3]["category"]
    assert [x["part_id"] for x in c1["parts"]] == [c["preamble"]["unit_id"], c["transition"]["unit_id"]] + [e["element_id"] for e in c["elements"]]
    assert [(x["start"], x["end"]) for x in c1["parts"][2:]] == [(e["start"], e["end"]) for e in c["elements"]]


def test_the_table_picks_its_components_when_none_are_named(client, keys, env):
    _r, js = table(client, keys, patents=["5000001", "5000002", "5000004", "5000005", "5000006"])
    comps = js["components"]
    assert {c["key"] for c in comps} == {"pool cleaner", "brush", "housing"} and all(c["from"] == "table" and c["asked"] is None for c in comps)
    assert {c["key"]: c["patents"] for c in comps} == {"pool cleaner": 3, "brush": 3, "housing": 3}
    _r2, again = table(client, keys, patents=["5000001", "5000002", "5000004", "5000005", "5000006"])
    assert again["components"] == comps                                     # the same pick every time
    facts = [{"keys": {"widget": {"n": True}, "gear": {"n": True}, "x": {"n": True}}},
             {"keys": {"widget": {"n": True}, "gear": {"n": False}}}, {"keys": {"widget": {"n": True}, "gear": {"n": True}}}]

    class NoIndex:
        def execute(self, *a):
            raise sqlite3.OperationalError("no such table")
    assert lapse_triage.table_components(NoIndex(), facts) == [("widget", 3), ("gear", 2)]
    assert lapse_triage.table_components(NoIndex(), facts[:1]) == [("gear", 1), ("widget", 1)]   # one patent: its own keys


def test_a_search_of_the_claims_in_reverse_load_order(client, keys, env):
    _r, js = table(client, keys, search='"drive motor"', limit=5)
    assert js["input"] == {"kind": "search", "search": '"drive motor"', "terms": ["drive motor"], "limit": 5, "found": 2, "more": False,
                           "complete": True, "unread_components": []}
    assert [p["patent_id"] for p in js["patents"]] == ["5000002", "5000001"]   # 5000002's claims were loaded later
    _r, one = table(client, keys, search='"drive motor"', limit=1)
    assert [p["patent_id"] for p in one["patents"]] == ["5000002"] and one["input"]["more"] is True
    _r, brush = table(client, keys, search="brush roller")
    assert [p["patent_id"] for p in brush["patents"]] == ["5000006"] and brush["input"]["limit"] == 100
    _r, none = table(client, keys, search="zzzz")
    assert none["patents"] == [] and none["components"] == [] and none["counts"] == {"patents": 0, "found": 0, "built": 0, "not_built": 0}
    r = client.get(PATH, {"search": "brush", "limit": "2", "components": "brush"}, HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and [p["patent_id"] for p in r.json()["patents"]] == ["5000006", "5000002"]
    from API.search import get_searcher

    plan = " ".join(str(x) for x in get_searcher().connection().execute("EXPLAIN QUERY PLAN " + lapse_triage.SEARCH_SQL, ('"brush"',)))
    assert "fts_g_claims VIRTUAL TABLE INDEX" in plan and "rowid=?" in plan and "TEMP B-TREE" not in plan   # index order, no sort


def test_get_with_parameters_is_the_same_table(client, keys, env):
    a = post(client, keys(), {"patents": "5000001,5000002", "components": "drive motor, brush"}).json()
    r = client.get(PATH, {"patents": "5000001,5000002", "components": "drive motor, brush"}, HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json() == a


def test_the_csv_export_with_quotes_and_locations(client, keys, env):
    r = post(client, keys(), {"patents": ALL, "components": COMPS, "output": "csv"})
    assert r.status_code == 200 and r["Content-Type"] == "text/csv; charset=utf-8"
    assert r["Content-Disposition"] == f'attachment; filename="patentref-triage-{DATA_VERSION}.csv"'
    text = r.content.decode("utf-8")
    assert text.startswith(",".join(lapse_triage.EXPORT_COLUMNS) + "\r\n") and text.endswith("\r\n")
    rows = list(csv.DictReader(io.StringIO(text, newline="")))
    kinds = [x["row_kind"] for x in rows]
    assert kinds.count("patent") == 6 and kinds.count("component") == 12      # 4 trees, 3 components each
    js = post(client, keys(), {"patents": ALL, "components": COMPS}).json()
    assert len(rows) == 6 + 12 + sum(len(cl["parts"]) for p in js["patents"] for cl in p["claim_parts"])
    n = 0
    for x in rows:
        if x["quote"]:
            claim = CLAIMS[x["patent_id"]][int(x["claim"]) - 1]
            assert claim[int(x["start"]):int(x["end"])] == x["quote"], x
            assert x["claim_id"] == f"US{x['patent_id']}-c{x['claim']}" and x["part_id"].startswith(x["claim_id"] + "-")
            n += 1
        assert x["mark"] == ""
    assert n == js["quotes"]
    fee = next(x for x in rows if x["patent_id"] == "5000004" and x["row_kind"] == "patent")
    want = lapse_triage._csv_value(row_of(js, "5000004")["status"]["reinstatement_possible"])
    assert (fee["status"], fee["status_basis"], fee["reinstatement_possible"], fee["note"]) == (
        "expired_fee_nonpayment", ";".join(row_of(js, "5000004")["status"]["basis"]), want, "")
    assert fee["status_basis"] == "fee_lapse:2016-01-03;term_20y"            # the basis codes, joined by semicolons
    gone = next(x for x in rows if x["patent_id"] == "9999999")
    assert (gone["status"], gone["note"]) == ("", "not_in_data")
    element = next(x for x in rows if x["row_kind"] == "element")
    marked = list(csv.DictReader(io.StringIO(lapse_triage.to_csv(js, {element["part_id"]: "have"}), newline="")))
    assert [x["mark"] for x in marked if x["mark"]] == ["have"]               # marks fill the mark column (the page's export)


def test_one_table_at_a_time_per_key_and_a_ceiling_for_the_server(client, keys, env, monkeypatch):
    monkeypatch.setattr(throttling, "timer", lambda: 1_900_000_000.0)       # one clock minute for the whole test
    key = keys(fresh=True)
    subject = metering.subject_for_prefix(key.split(".", 1)[0])["subject"]
    window, _ = ratelimit.minute_window(lapse_triage.SCOPE_SLOTS, 60, throttling.timer())

    def held(subj):
        with sqlite3.connect(settings.DATABASES["default"]["NAME"]) as c:
            row = c.execute(f"SELECT count FROM {ratelimit.WINDOW_TABLE} WHERE subject = ? AND win = ?", (subj, window)).fetchone()
        return row[0] if row else 0

    assert post(client, key, {"patents": "5000001"}).status_code == 200
    assert held("triage:" + subject) == 0 and held("triage:all") == 0         # given back when the answer left
    ratelimit.bump(ratelimit.WINDOW_TABLE, "triage:" + subject, "win", window, 1)   # a table of this key is being built
    r = post(client, key, {"patents": "5000001"})
    assert r.status_code == 429 and r["Retry-After"] == "2"
    assert r.json() == {"detail": "Request was throttled. Expected available in 2 seconds."}
    assert r["X-Status-Reason"] == lapse_triage.busy_reason() and held("triage:all") == 0
    assert post(client, keys(fresh=True), {"patents": "5000001"}).status_code == 200    # another key is not held up
    ratelimit.refund(ratelimit.WINDOW_TABLE, "triage:" + subject, "win", window)
    monkeypatch.setattr(settings, "LAPSE_TRIAGE_TOTAL", 1, raising=False)
    ratelimit.bump(ratelimit.WINDOW_TABLE, "triage:all", "win", window, 1)   # the server's one slot is taken
    other = keys(fresh=True)
    r = post(client, other, {"patents": "5000001"}, HTTP_ACCEPT="application/problem+json")
    assert r.status_code == 429 and r.json()["code"] == "THROTTLED" and "at most 1 at once" in r.json()["detail"]
    ratelimit.refund(ratelimit.WINDOW_TABLE, "triage:all", "win", window)
    assert post(client, other, {"patents": "5000001"}).status_code == 200


def test_the_time_limit_and_the_cache(client, keys, env, monkeypatch):
    a = post(client, keys(), {"patents": ["5000001"]})
    assert a["Server-Timing"].startswith('triage;desc="1 built, 0 from the cache"')
    b = post(client, keys(), {"patents": ["5000001"]})
    assert b["Server-Timing"].startswith('triage;desc="1 built, 1 from the cache"') and a.content == b.content
    monkeypatch.setattr(lapse_triage, "TIME_LIMIT_SECONDS", -1.0)
    r = post(client, keys(), {"patents": ["5000001", "5000002", "9999999"]})
    js = r.json()
    assert js["complete"] is False and r["Cache-Control"] == "no-store"
    assert [(p["patent_id"], p["built"], p["note"]) for p in js["patents"]] == [
        ("5000001", True, None), ("5000002", False, "time_limit"), ("9999999", False, "not_in_data")]
    assert js["counts"]["not_built"] == 1 and row_of(js, "5000002")["status"]["status"] == "expired_term"   # the status is still there


def test_the_work_cap_and_the_offset_check(client, keys, env, monkeypatch):
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 1)
    js = post(client, keys(), {"patents": ["5000001", "5000004"]}).json()
    assert [(p["built"], p["note"]) for p in js["patents"]] == [(False, "work_cap"), (True, None)]
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 1000)
    lapse_triage.FACTS.clear()
    monkeypatch.setattr(lapse_claimgraph, "check_patent", lambda graph: ["a span that does not hold"])
    js = post(client, keys(), {"patents": ["5000004"]}).json()
    assert row_of(js, "5000004")["note"] == "offset_check" and js["quotes"] == 0


def test_the_facts_cache_is_bounded():
    c = lapse_structure.AnswerCache(entries=2, max_bytes=100, item_max=60)
    for k in "abc":
        c.put(k, b"x" * 10)
    assert c.get("a") is None and c.stats() == {"entries": 2, "bytes": 20}
    c.put("big", b"x" * 61)
    assert c.get("big") is None
    assert lapse_triage.FACTS.entries == 2048 and lapse_triage.FACTS.max_bytes == 16 * 1024 * 1024


def test_the_picks_word_counts_are_kept_per_data_version_and_bounded(client, keys, env, monkeypatch):
    from API import lapse_similar_structured

    asked = []
    real = lapse_similar_structured._doc_freqs

    def counting(con, words):
        asked.append(list(words))
        return real(con, words)
    monkeypatch.setattr(lapse_similar_structured, "_doc_freqs", counting)
    pick = ["5000001", "5000002", "5000004", "5000005", "5000006"]
    _r, first = table(client, keys, patents=pick)
    assert asked and lapse_triage._DF and all(v == DATA_VERSION for v, _w in lapse_triage._DF)
    del asked[:]
    lapse_triage.FACTS.clear()
    _r, again = table(client, keys, patents=pick)
    assert asked == [] and again["components"] == first["components"]   # every word's count from the cache
    con = sqlite3.connect(":memory:")   # no vocabulary here: zeros, and nothing kept
    assert lapse_triage.doc_freqs(con, "other", ["pool"]) == {"pool": 0} and ("other", "pool") not in lapse_triage._DF
    monkeypatch.setattr(lapse_triage, "DF_ENTRIES", 2)
    monkeypatch.setattr(lapse_similar_structured, "_doc_freqs", lambda con, words: {w: 7 for w in words})
    lapse_triage.doc_freqs(con, "v2", ["a1", "b1", "c1"])
    assert len(lapse_triage._DF) == 2 and list(lapse_triage._DF)[-1] == ("v2", "c1")


# ------------------------------------------------------------------ wording

def test_the_wording_rule_holds_and_catches(client, keys, env):
    _r, js = table(client, keys, patents=ALL, components=COMPS)
    assert lapse_triage.wording_problems(lapse_triage.own_words(js)) == []
    for word in ("risk", "clear", "safe", "free to use", "invalid", "valid", "scope", "breadth", "strength", "broad", "narrow",
                 "infringed", "covers"):
        assert lapse_triage.wording_problems("this claim " + word) != [], word
    own = " ".join([lapse_triage.__doc__, triage_page.__doc__, " ".join(lapse_triage.NOTES.values()), lapse_triage.busy_reason(),
                    " ".join(lapse_triage.EXPORT_COLUMNS)])
    assert not BANNED.search(own.lower()), BANNED.search(own.lower())
    assert lapse_triage.wording_problems(own) == []


def test_a_wording_failure_never_leaves_the_server(client, keys, env, monkeypatch):
    monkeypatch.setattr(lapse_triage, "POSITIONS", ("every_independent", "some_independent", "only_dependent", "a clear absent"))
    r = post(client, keys(), {"patents": ["5000001"]})
    assert r.status_code == 500 and r["X-Status-Reason-Code"] == "ERR_ES" and "wording check failed" in r["X-Status-Reason"]


# ------------------------------------------------------------------ the page

def _inline(html, tag):
    return re.findall(rf"<{tag}>(.*?)</{tag}>", html, re.S)


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def test_the_page_on_the_site_template_with_its_policy(client, env):
    r = client.get("/triage/")
    assert r.status_code == 200 and r["Content-Type"] == "text/html; charset=utf-8" and r["Cache-Control"] == "public, max-age=300"
    html = r.content.decode("utf-8")
    assert '<html lang="en"' in html and "<title>Triage table | PatentRef</title>" in html and '<main class="pr-main" id="main">' in html
    assert '<form id="tt-form" class="card">' in html and "Data version " + DATA_VERSION in html
    csp = r["Content-Security-Policy"]
    styles, scripts = _inline(html, "style"), _inline(html, "script")
    assert len(styles) == 1 and len(scripts) == 1
    assert csp == ("default-src 'none'; script-src " + _sha(scripts[0]) + "; style-src " + _sha(styles[0]) + "; img-src 'self' data:; "
                   "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
    assert " src=" not in html and "<link rel=\"stylesheet\"" not in html
    for field in ("tt-key", "tt-list", "tt-search", "tt-components", "tt-limit"):   # no field has a name, so none can reach an address
        tag = re.search(rf'<(?:input|textarea|select) id="{field}"[^>]*>', html).group(0)
        assert " name=" not in tag, tag
    assert 'id="tt-key" type="password" autocomplete="off"' in html
    assert client.post("/triage/").status_code == 405


def test_the_page_and_its_script_describe_the_text_only(client, env):
    html = client.get("/triage/").content.decode("utf-8")
    src = (ROOT / "lapse_accounts" / "templates" / "lapse_accounts" / "triage.html").read_text(encoding="utf-8")
    js = (ROOT / "lapse_accounts" / "templates" / "lapse_accounts" / "_triage.js").read_text(encoding="utf-8")
    for label, text in (("page", html), ("template", src), ("script", js)):
        m = BANNED.search(text.lower())
        assert not m, (label, text.lower()[max(0, m.start() - 60):m.end() + 60])
        assert lapse_triage.wording_problems(text) == [], label
        assert "innerHTML" not in text, label


def test_the_example_page_draws_a_table_without_a_key(client, env):
    settings.LAPSE_TRIAGE_EXAMPLE = {"patents": ["5000001", "5000002", "9999999"], "components": ["drive motor", "brush"]}
    r = client.get("/triage/example/")
    assert r.status_code == 200 and r["Cache-Control"] == "public, max-age=300"
    html = r.content.decode("utf-8")
    assert "<title>Triage table: an example | PatentRef</title>" in html
    m = re.search(r'<script id="tt-example" type="application/json">(.*?)</script>', html, re.S)
    body = json.loads(m.group(1))
    assert [p["patent_id"] for p in body["patents"]] == ["5000001", "5000002", "9999999"]
    assert [c["key"] for c in body["components"]] == ["drive motor", "brush"] and body["counts"]["built"] == 2
    assert _sha(_inline(html, "script")[0]) in r["Content-Security-Policy"]
    outside = html.replace(m.group(0), "")
    assert not BANNED.search(outside.lower()), BANNED.search(outside.lower())
    assert client.get("/triage/example/").content == r.content               # kept per worker for the data in service
    settings.LAPSE_TEXT_PATH = str(_TMP / "no_such_text.db")
    _reset()
    r = client.get("/triage/example/")
    assert r.status_code == 503 and r["Cache-Control"] == "no-store" and "cannot be built right now" in r.content.decode("utf-8")


# ------------------------------------------------------------------ the page's own export is the API's, plus the marks

def _js_function(source, name):
    start = source.index(f"  function {name}(")
    depth, i = 0, source.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(source[i], 0)
        i += 1
        if depth == 0:
            return source[start:i]


def test_the_pages_csv_is_the_apis_csv_with_the_marks(client, keys, env, tmp_path):
    node = shutil.which("node") or (r"C:\Program Files\nodejs\node.exe" if os.path.exists(r"C:\Program Files\nodejs\node.exe") else None)
    if not node:
        pytest.skip("no node here to run the page's script (the box has none; the PC and the lane's sandbox run it)")
    js = post(client, keys(), {"patents": ALL, "components": COMPS}).json()
    js["patents"][0]["title"] = 'Pool "cleaner", with\r\na comma'                 # every quoting case of the CSV rule
    part_ids = [part["part_id"] for p in js["patents"] for cl in p["claim_parts"] for part in cl["parts"]]
    marks = {part_ids[0]: "have", part_ids[3]: "lacks"}
    src = (ROOT / "lapse_accounts" / "templates" / "lapse_accounts" / "_triage.js").read_text(encoding="utf-8")
    funcs = "\n".join(_js_function(src, n) for n in ("csvField", "exportRows", "toCsv"))
    (tmp_path / "t.json").write_text(json.dumps({"table": js, "marks": marks}), encoding="utf-8")
    (tmp_path / "run.js").write_text(funcs + "\nvar d = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));\n"
                                     "process.stdout.write(toCsv(d.table, d.marks));\n", encoding="utf-8")
    out = subprocess.run([node, str(tmp_path / "run.js"), str(tmp_path / "t.json")], capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.decode("utf-8") == lapse_triage.to_csv(js, marks)
    rows = list(csv.DictReader(io.StringIO(out.stdout.decode("utf-8"), newline="")))
    assert {x["part_id"]: x["mark"] for x in rows if x["mark"]} == marks
