"""The long-text file (PatentRef checklist 1.6): built by lapse_tools/build_text.py from tiny PVGPATTXT-shaped
zips, attached beside the search snapshot through LAPSE_TEXT_PATH, served on the four text endpoints.

Checks: the build (orphans dropped, compressed text, contentless FTS, nulls table, counts); the endpoints
answer 200 with plain text, the default f and s, after-cursor paging in the contract example's shape,
_text_any / _text_all / _text_phrase (the phrase through the detail=none prefilter on descriptions), _contains
on a compressed column, 400 on a sort by the text field; and the documented 501 when no text file is attached.
Reuses conftest.py (temporary Django DB, operator key) and the search database of test_lapse_group.

  python -m pytest lapse_accounts/tests/test_long_text.py -q -p no:cacheprovider
"""
import io
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path

import pytest
from django.conf import settings

from API.search_sqlite import LapseSQLiteSearch, plain
from lapse_accounts.tests.conftest import _TMP
from lapse_accounts.tests.test_lapse_group import build_search_db

ROOT = Path(__file__).resolve().parents[2]
BUILD_TEXT = ROOT / "lapse_tools" / "build_text.py"
ESDL = _TMP / "esdl"

PATENTS = ["12050000", "12050006", "12050007", "D345393"]
CLAIMS = [
    # patent_id, claim_sequence, claim_text, dependent, claim_number, exemplary
    ("12050000", 0, "A lithium battery comprising a solid electrolyte.", "", "1", 1),
    ("12050000", 1, "The battery of claim 1 wherein the electrolyte is a polymer.", "1", "2", 0),
    ("12050006", 0, "A pool cleaning head with a rotating brush.", "", "1", 1),
    ("12050006", 1, "The head of claim 1 with a hose.", "1", "2", 0),
    ("12050006", 2, "The head of claim 2 with a second brush.", "2", "3", 0),
    ("12050007", 0, "A solid battery electrolyte of lithium salt.", "", "1", 1),
    ("99999999", 0, "An orphan claim whose patent is not granted.", "", "1", 1),
]
DESCS = [
    ("12050000", "DETAILED DESCRIPTION. The solid electrolyte battery is assembled in a dry room. " * 20),
    ("12050006", "DETAILED DESCRIPTION. The cleaning head rotates the brush against the pool wall. " * 20),
    ("12050007", "DETAILED DESCRIPTION. Lithium salt dissolved in the solid electrolyte. " * 20),
]
SUMS = [("12050000", "A battery with a solid electrolyte."), ("12050006", "A pool cleaner.")]
DRAWS = [("12050000", 0, "FIG. 1 is a section of the battery."), ("12050000", 1, "FIG. 2 shows the electrolyte."),
         ("12050006", 0, "FIG. 1 is a perspective view of the cleaning head.")]


def tsv_zip(path, header, rows):
    buf = io.StringIO()
    buf.write("\t".join(f'"{h}"' for h in header) + "\n")
    for r in rows:
        buf.write("\t".join('"' + str(v).replace('"', '""') + '"' if v is not None else "" for v in r) + "\n")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(path.name.replace(".zip", ""), buf.getvalue())


def make_bulk(d):
    d.mkdir(parents=True, exist_ok=True)
    tsv_zip(d / "g_patent.tsv.zip", ["patent_id", "patent_type", "patent_date", "patent_title", "wipo_kind", "num_claims", "withdrawn", "filename"],
            [(p, "utility", "2024-07-16", "t", "B2", 2, 0, "x") for p in PATENTS])
    tsv_zip(d / "g_claims_2024.tsv.zip", ["patent_id", "claim_sequence", "claim_text", "dependent", "claim_number", "exemplary"], CLAIMS[:4] + CLAIMS[6:])
    tsv_zip(d / "g_claims_2025.tsv.zip", ["patent_id", "claim_sequence", "claim_text", "dependent", "claim_number", "exemplary"], CLAIMS[4:6])
    tsv_zip(d / "g_detail_desc_text_2004.tsv.zip", ["patent_id", "description_text", "description_length"], [("12050000", "OLD YEAR, must be skipped", 25)])
    tsv_zip(d / "g_detail_desc_text_2024.tsv.zip", ["patent_id", "description_text", "description_length"], [(p, t, len(t)) for p, t in DESCS])
    tsv_zip(d / "g_brf_sum_text_2024.tsv.zip", ["patent_id", "summary_text"], SUMS)
    tsv_zip(d / "g_draw_desc_text_2024.tsv.zip", ["patent_id", "draw_desc_sequence", "draw_desc_text"], DRAWS)


def make_esdl(d):
    base = d / "src" / "es_data_load" / "pv" / "schemas" / "granted"
    base.mkdir(parents=True, exist_ok=True)
    (base / "claim.json").write_text(json.dumps({"uuid": {"type": "keyword"}, "patent_id": {"type": "keyword"}, "claim_number": {"type": "keyword"},
                                                 "claim_text": {"type": "text"}, "exemplary": {"type": "integer"}, "claim_sequence": {"type": "integer"},
                                                 "claim_dependent": {"type": "keyword"}, "patent_zero_prefix": {"type": "keyword"}, "document_date": {"type": "date"}}))
    (base / "brf_sum_text.json").write_text(json.dumps({"uuid": {"type": "keyword"}, "patent_id": {"type": "keyword"}, "summary_text": {"type": "text"},
                                                        "patent_zero_prefix": {"type": "keyword"}, "document_date": {"type": "date"}}))
    (base / "detail_desc_text.json").write_text(json.dumps({"uuid": {"type": "keyword"}, "patent_id": {"type": "keyword"}, "description_text": {"type": "text"},
                                                            "description_length": {"type": "integer"}, "patent_zero_prefix": {"type": "keyword"}, "document_date": {"type": "date"}}))
    (base / "draw_desc_text.json").write_text(json.dumps({"uuid": {"type": "keyword"}, "patent_id": {"type": "keyword"}, "draw_desc_text": {"type": "text"},
                                                          "draw_desc_sequence": {"type": "integer"}, "patent_zero_prefix": {"type": "keyword"}, "document_date": {"type": "date"}}))


def _build(name, *extra):
    bulk = _TMP / "text_bulk"
    if not bulk.exists():
        make_bulk(bulk)
        make_esdl(ESDL)
    out = _TMP / name
    r = subprocess.run([sys.executable, str(BUILD_TEXT), "--bulk", str(bulk), "--out", str(out), "--es-data-load", str(ESDL),
                        "--workers", "2", *extra], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "BUILD_DONE" in r.stdout
    return str(out)


@pytest.fixture(scope="module")
def text_db():
    return _build("text_next.db")


@pytest.fixture(scope="module")
def text_db_none():
    """The same text with --desc-fts-detail none: the descriptions' index keeps no positions."""
    return _build("text_none.db", "--desc-fts-detail", "none")


def _env(path):
    search = str(_TMP / "lapse_search.db")
    build_search_db(search)
    old = dict(settings.LAPSE_SQLITE)
    old_text = getattr(settings, "LAPSE_TEXT_PATH", None)
    settings.LAPSE_SQLITE["path"] = search
    settings.LAPSE_TEXT_PATH = path
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    try:
        yield path
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        settings.LAPSE_TEXT_PATH = old_text
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()


@pytest.fixture
def text_env(text_db):
    yield from _env(text_db)


@pytest.fixture
def text_env_none(text_db_none):
    yield from _env(text_db_none)


def get(client, key, path, q=None, f=None, s=None, o=None):
    params = {}
    for k, v in (("q", q), ("f", f), ("s", s), ("o", o)):
        if v is not None:
            params[k] = json.dumps(v)
    return client.get(path, params, HTTP_X_API_KEY=key)


# ------------------------------------------------------------------ the build

def test_build_layout_counts_compression_and_nulls(text_db):
    con = sqlite3.connect(f"file:{text_db}?mode=ro", uri=True)
    counts = json.loads(con.execute("SELECT v FROM _lapse_build WHERE k='row_counts'").fetchone()[0])
    assert counts == {"g_claims": 6, "g_brf_sum_texts": 2, "g_detail_desc_texts": 3, "g_draw_desc_texts": 3}
    assert con.execute("SELECT count(*) FROM g_claims WHERE patent_id='99999999'").fetchone()[0] == 0  # orphan dropped
    assert con.execute("SELECT count(*) FROM g_detail_desc_texts WHERE description_length=25").fetchone()[0] == 0  # 2004 skipped
    raw = con.execute("SELECT claim_text FROM g_claims WHERE uuid='12050000-0'").fetchone()[0]
    assert isinstance(raw, bytes) and zlib.decompress(raw).decode() == CLAIMS[0][2]
    assert plain(raw) == CLAIMS[0][2]
    # contentless FTS with the table's rowids; detail=none on descriptions, full elsewhere
    assert con.execute("SELECT rowid FROM fts_g_claims WHERE fts_g_claims MATCH 'lithium' ORDER BY rowid").fetchall() == \
        con.execute("SELECT rowid FROM g_claims WHERE patent_id IN ('12050000','12050007') AND claim_sequence=0 ORDER BY rowid").fetchall()
    ddl = dict(con.execute("SELECT name, sql FROM sqlite_master WHERE name LIKE 'fts_g_%' AND sql LIKE 'CREATE VIRTUAL%'"))
    assert "detail=full" in ddl["fts_g_detail_desc_texts"] and "detail=full" in ddl["fts_g_claims"]
    assert "content=''" in ddl["fts_g_claims"]
    nulls = dict(((t, c), h) for t, c, h in con.execute("SELECT tbl, col, has_null FROM _lapse_nulls"))
    assert nulls[("g_claims", "claim_sequence")] == 0 and nulls[("g_claims", "patent_id")] == 0
    assert {r[0] for r in con.execute("SELECT idx FROM _lapse_indices")} == {"g_claims", "g_brf_sum_texts", "g_detail_desc_texts", "g_draw_desc_texts"}
    assert con.execute("SELECT count(*) FROM _lapse_fields WHERE idx='g_claims'").fetchone()[0] == 9
    assert dict(con.execute("SELECT k, v FROM _lapse_build"))["kind"] == "text"
    assert con.execute("SELECT description_length FROM g_detail_desc_texts WHERE patent_id='12050000'").fetchone()[0] == len(DESCS[0][1])
    con.close()


# ------------------------------------------------------------------ the endpoints

def test_g_claim_default_shape_and_plain_text(client, user_key, text_env):
    _, key, _ = user_key
    r = get(client, key, "/api/v1/g_claim/", {"patent_id": "12050006"})
    assert r.status_code == 200, r.content
    js = r.json()
    assert js["error"] is False and js["count"] == 3 and js["total_hits"] == 3
    rows = js["g_claims"]
    assert [set(x) for x in rows] == [{"patent_id", "claim_sequence", "claim_text"}] * 3
    assert [x["claim_sequence"] for x in rows] == [0, 1, 2]
    assert rows[0]["claim_text"] == "A pool cleaning head with a rotating brush."
    assert r["X-Data-Version"] == "20261006.2"


def test_g_claim_contract_example_after_cursor(client, user_key, text_env):
    """The ref_after_cursor example of the upstream docs: q _gte patent_id, s patent_id asc + claim_sequence desc,
    o.after [12050006, 2]: the rows after that cursor in that order."""
    _, key, _ = user_key
    q = {"_gte": {"patent_id": "12050000"}}
    s = [{"patent_id": "asc"}, {"claim_sequence": "desc"}]
    r = get(client, key, "/api/v1/g_claim/", q, s=s, o={"size": 100})
    assert r.status_code == 200, r.content
    rows = r.json()["g_claims"]
    assert [(x["patent_id"], x["claim_sequence"]) for x in rows] == [("12050000", 1), ("12050000", 0), ("12050006", 2), ("12050006", 1),
                                                                        ("12050006", 0), ("12050007", 0)]
    r = get(client, key, "/api/v1/g_claim/", q, s=s, o={"size": 100, "after": ["12050006", 2]})
    assert r.status_code == 200, r.content
    rows = r.json()["g_claims"]
    assert [(x["patent_id"], x["claim_sequence"]) for x in rows] == [("12050006", 1), ("12050006", 0), ("12050007", 0)]


def test_text_operators_on_compressed_claims(client, user_key, text_env):
    _, key, _ = user_key
    r = get(client, key, "/api/v1/g_claim/", {"_text_all": {"claim_text": "lithium battery"}}, f=["patent_id", "claim_number"])
    assert r.status_code == 200, r.content
    assert sorted(x["patent_id"] for x in r.json()["g_claims"]) == ["12050000", "12050007"]
    r = get(client, key, "/api/v1/g_claim/", {"_text_phrase": {"claim_text": "solid electrolyte"}})
    assert [x["patent_id"] for x in r.json()["g_claims"]] == ["12050000"]
    r = get(client, key, "/api/v1/g_claim/", {"_text_any": {"claim_text": "hose polymer"}})
    assert sorted(x["patent_id"] for x in r.json()["g_claims"]) == ["12050000", "12050006"]


def test_contains_sort_and_filters_on_claims(client, user_key, text_env):
    _, key, _ = user_key
    r = get(client, key, "/api/v1/g_claim/", {"_contains": {"claim_text": "rotating"}})
    assert [x["patent_id"] for x in r.json()["g_claims"]] == ["12050006"]
    r = get(client, key, "/api/v1/g_claim/", {"_and": [{"patent_id": "12050006"}, {"_gte": {"claim_sequence": 1}}]},
            f=["patent_id", "claim_sequence", "claim_dependent", "exemplary"], s=[{"claim_sequence": "desc"}])
    rows = r.json()["g_claims"]
    assert [(x["claim_sequence"], x["claim_dependent"], x["exemplary"]) for x in rows] == [(2, "2", "0"), (1, "1", "0")]  # exemplary: CharField over an integer, as upstream renders it
    r = get(client, key, "/api/v1/g_claim/", {"patent_id": "12050006"}, s=[{"claim_text": "asc"}])
    assert r.status_code == 400  # a text field is not sortable (upstream's validation)


@pytest.mark.parametrize("variant", ["full", "none"])
def test_descriptions_phrase_on_both_index_variants(client, user_key, variant, request):
    path = request.getfixturevalue("text_env" if variant == "full" else "text_env_none")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE name='fts_g_detail_desc_texts'").fetchone()[0]
    con.close()
    assert f"detail={variant}" in ddl
    _, key, _ = user_key
    r = get(client, key, "/api/v1/g_detail_desc_text/", {"_text_phrase": {"description_text": "solid electrolyte battery"}}, f=["patent_id", "description_length"])
    assert r.status_code == 200, r.content
    assert [x["patent_id"] for x in r.json()["g_detail_desc_texts"]] == ["12050000"]
    # the same tokens in another order are a different phrase: the prefilter keeps both, the exact test one
    r = get(client, key, "/api/v1/g_detail_desc_text/", {"_text_phrase": {"description_text": "electrolyte solid"}})
    assert r.json()["g_detail_desc_texts"] == []
    r = get(client, key, "/api/v1/g_detail_desc_text/", {"_text_all": {"description_text": "lithium electrolyte"}})
    assert [x["patent_id"] for x in r.json()["g_detail_desc_texts"]] == ["12050007"]


def test_descriptions_row_and_length_sort(client, user_key, text_env):
    _, key, _ = user_key
    r = get(client, key, "/api/v1/g_detail_desc_text/", {"patent_id": "12050006"})
    row = r.json()["g_detail_desc_texts"][0]
    assert set(row) == {"patent_id", "description_text"} and row["description_text"].startswith("DETAILED DESCRIPTION. The cleaning head")
    r = get(client, key, "/api/v1/g_detail_desc_text/", {"_gte": {"description_length": 1}}, s=[{"description_length": "desc"}], f=["patent_id"])
    assert len(r.json()["g_detail_desc_texts"]) == 3


def test_brief_summaries_and_drawing_descriptions(client, user_key, text_env):
    _, key, _ = user_key
    r = get(client, key, "/api/v1/g_brf_sum_text/", {"_text_any": {"summary_text": "cleaner"}})
    assert r.status_code == 200, r.content
    assert r.json()["g_brf_sum_texts"] == [{"patent_id": "12050006", "summary_text": "A pool cleaner."}]
    r = get(client, key, "/api/v1/g_draw_desc_text/", {"patent_id": "12050000"})
    rows = r.json()["g_draw_desc_texts"]
    assert [(x["draw_desc_sequence"], x["draw_desc_text"]) for x in rows] == [(0, "FIG. 1 is a section of the battery."), (1, "FIG. 2 shows the electrolyte.")]
    assert set(rows[0]) == {"patent_id", "draw_desc_sequence", "draw_desc_text"}


def test_health_names_the_text_version_and_501_without_the_file(client, user_key, text_env):
    _, key, _ = user_key
    r = client.get("/api/v1/meta/health/")
    assert r.json()["text_data_version"] == "text_next"  # unstamped file: its own name
    settings.LAPSE_TEXT_PATH = str(_TMP / "no_such_text.db")
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    r = get(client, key, "/api/v1/g_claim/", {"patent_id": "12050006"})
    assert r.status_code == 501
    assert r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED"
    assert r["X-Status-Reason"].startswith("Endpoint not implemented: the 'g_claims' data set is not in this snapshot")
    r = client.get("/api/v1/meta/health/")
    assert r.json()["text_data_version"] is None
    # the patents index is untouched by the text file either way
    r = get(client, key, "/api/v1/patent/", {"patent_id": "10000364"})
    assert r.status_code == 200
