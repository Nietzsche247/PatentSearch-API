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

PATENTS = ["11000000", "12050000", "12050006", "12050007", "D345393"]
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
    # two claims without a sequence (like reissue RE31177 in 1983): one key, two texts, both must stay
    tsv_zip(d / "g_claims_2024.tsv.zip", ["patent_id", "claim_sequence", "claim_text", "dependent", "claim_number", "exemplary"],
            CLAIMS[:4] + CLAIMS[6:] + [("11000000", "", "An unnumbered claim to a kettle.", "", "", ""),
                                       ("11000000", "", "An unnumbered claim to a teapot.", "", "", "")])
    # the same patent in two year files (a duplicate uuid) FIRST in its batch, then rows that must still load
    tsv_zip(d / "g_claims_2025.tsv.zip", ["patent_id", "claim_sequence", "claim_text", "dependent", "claim_number", "exemplary"],
            [CLAIMS[0]] + CLAIMS[4:6])  # same text, so which copy stays (parser race) changes no answer
    tsv_zip(d / "g_detail_desc_text_2004.tsv.zip", ["patent_id", "description_text", "description_length"], [("12050000", "OLD YEAR, must be skipped", 25)])
    tsv_zip(d / "g_detail_desc_text_2024.tsv.zip", ["patent_id", "description_text", "description_length"], [(p, t, len(t)) for p, t in DESCS])
    tsv_zip(d / "g_brf_sum_text_2024.tsv.zip", ["patent_id", "summary_text"], SUMS)
    tsv_zip(d / "g_brf_sum_text_2025.tsv.zip", ["patent_id", "summary_text"],
            [("12050000", "zebra duplicate summary"), ("12050007", "A solid lithium cell.")])
    tsv_zip(d / "g_draw_desc_text_2024.tsv.zip", ["patent_id", "draw_desc_sequence", "draw_desc_text"], DRAWS + DRAWS[:1])  # a verbatim repeat


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
    assert counts == {"g_claims": 8, "g_brf_sum_texts": 4, "g_detail_desc_texts": 3, "g_draw_desc_texts": 3}
    dups = json.loads(con.execute("SELECT v FROM _lapse_build WHERE k='duplicates'").fetchone()[0])
    renamed = json.loads(con.execute("SELECT v FROM _lapse_build WHERE k='renamed'").fetchone()[0])
    # identical copies are dropped; a shared key with a different text is kept as key~2
    assert dups == {"g_claims": 1, "g_brf_sum_texts": 0, "g_detail_desc_texts": 0, "g_draw_desc_texts": 1}
    assert renamed == {"g_claims": 1, "g_brf_sum_texts": 1, "g_detail_desc_texts": 0, "g_draw_desc_texts": 0}
    assert sorted(r[0] for r in con.execute("SELECT uuid FROM g_claims WHERE patent_id='11000000'")) == ["11000000-None", "11000000-None~2"]
    assert sorted(r[0] for r in con.execute("SELECT uuid FROM g_brf_sum_texts WHERE patent_id='12050000'")) == ["12050000", "12050000~2"]
    # the rows after the duplicate in the same batch went in (the 2026-10-08 defect dropped them)
    assert con.execute("SELECT count(*) FROM g_claims WHERE patent_id='12050006'").fetchone()[0] == 3
    assert con.execute("SELECT count(*) FROM g_brf_sum_texts WHERE patent_id='12050007'").fetchone()[0] == 1
    # the FTS index holds exactly the rows the table holds: no entry for a dropped duplicate
    for t, col, tok in (("g_claims", "claim_text", "lithium"), ("g_brf_sum_texts", "summary_text", "zebra"),
                        ("g_brf_sum_texts", "summary_text", "battery")):
        fts_ids = {r[0] for r in con.execute(f"SELECT rowid FROM fts_{t} WHERE fts_{t} MATCH ?", (tok,))}
        tbl_ids = {rid for rid, v in con.execute(f"SELECT rowid, {col} FROM {t}") if tok in zlib.decompress(v).decode().lower()}
        assert fts_ids == tbl_ids, (t, tok, fts_ids, tbl_ids)
    srcs = json.loads(con.execute("SELECT v FROM _lapse_build WHERE k='sources'").fetchone()[0])
    assert sum(x["duplicates"] for x in srcs) == 2 and all(x["inserted"] + x["duplicates"] == x["rows"] for x in srcs)
    assert {r[0] for r in con.execute("SELECT rowid FROM fts_g_claims WHERE fts_g_claims MATCH 'teapot OR kettle'")} == \
        {r[0] for r in con.execute("SELECT rowid FROM g_claims WHERE patent_id='11000000'")}
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
    assert nulls[("g_claims", "claim_sequence")] == 1 and nulls[("g_claims", "patent_id")] == 0  # the unnumbered claims
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
    r = get(client, key, "/api/v1/g_brf_sum_text/", {"_text_any": {"summary_text": "lithium"}})
    assert [x["patent_id"] for x in r.json()["g_brf_sum_texts"]] == ["12050007"]
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


def test_after_cursor_over_a_nullable_second_key_walks_the_same_rows(text_env):
    """ES search_after with missing values last, through the searcher: claims sorted patent_id asc, claim_sequence desc,
    where claim_sequence holds NULLs (the unnumbered claims of 11000000). Pages of 1, 2 and 3 joined equal the one-page
    answer, and the cursor SQL leads with a range on patent_id and keeps the OR expansion off the indexes (unary plus),
    which is what took the ref_after_cursor example from 33 s to milliseconds on 102M claims."""
    s = LapseSQLiteSearch.from_django_settings()
    q = {"range": {"patent_id": {"gte": "11000000"}}}
    sort = [{"patent_id": {"order": "asc"}}, {"claim_sequence": {"order": "desc"}}]
    full = s.search("g_claims", q, ["patent_id", "claim_sequence"], 100, None, sort)["hits"]["hits"]
    want = [(h["_source"]["patent_id"], h["_source"]["claim_sequence"]) for h in full]
    assert want == [("11000000", None), ("11000000", None), ("12050000", 1), ("12050000", 0), ("12050006", 2),
                    ("12050006", 1), ("12050006", 0), ("12050007", 0)]
    for size in (2, 3):
        got, after = [], None
        for _ in range(10):
            hits = s.search("g_claims", q, ["patent_id", "claim_sequence"], size, after, sort)["hits"]["hits"]
            if not hits:
                break
            got += [(h["_source"]["patent_id"], h["_source"]["claim_sequence"]) for h in hits]
            after = hits[-1]["sort"]
        assert got == want, (size, got)
    import os
    log = str(_TMP / "sql_after.jsonl")
    import API.search_sqlite as S
    old = S.SQL_LOG
    S.SQL_LOG = log
    try:
        s.search("g_claims", q, ["patent_id"], 2, ["12050006", 2], sort)
    finally:
        S.SQL_LOG = old
    sql = json.loads(open(log).read().splitlines()[-1])["sql"]
    assert 'm."patent_id" >= ? AND (((+m."patent_id" > ? OR +m."patent_id" IS NULL)) OR (+m."patent_id" = ? AND' in sql, sql
    os.remove(log)


# ------------------------------------------------------------------ the shapes that timed out on the full corpus (gate 2.3, section C)

class _SqlLog:
    """Collects the statements the searcher runs (API.search_sqlite.SQL_LOG) while the block runs."""

    def __init__(self, name):
        self.path = str(_TMP / name)

    def __enter__(self):
        import API.search_sqlite as S

        self.S, self.old = S, S.SQL_LOG
        if os.path.exists(self.path):
            os.remove(self.path)
        S.SQL_LOG = self.path
        return self

    def __exit__(self, *exc):
        self.S.SQL_LOG = self.old

    def statements(self):
        if not os.path.exists(self.path):
            return []
        return [json.loads(line)["sql"] for line in open(self.path, encoding="utf-8").read().splitlines()]


def _rows(path, sql, params=()):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


@pytest.fixture
def roomy(monkeypatch):
    """These tests send more requests than the 5 a month conftest gives a Free key."""
    monkeypatch.setattr(settings, "LAPSE_FREE_MONTHLY_LIMIT", 100000)
    monkeypatch.setattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 100000)
    yield monkeypatch


def test_contains_with_a_space_or_punctuation_matches_no_token(client, user_key, text_env, roomy):
    """_contains is a wildcard matched against single tokens (upstream: single indexed terms of Elasticsearch), and
    no token holds a space or punctuation. Upstream answered the R package's {"_contains": {"summary_text":
    "particular depth"}} with total_hits 0 (vignette api-changes); here it read and decompressed every summary and
    answered 500 at the 20 s limit on the full corpus. Now no row is read: the SQL is WHERE 0, and the answer is
    the one the per-row test gives (checked against every text of the fixture)."""
    _, key, _ = user_key
    with _SqlLog("sql_space.jsonl") as log:
        r = get(client, key, "/api/v1/g_brf_sum_text/", {"_contains": {"summary_text": "solid electrolyte"}}, s=[{"patent_id": "asc"}])
        assert r.status_code == 200, r.content
        assert r.json()["total_hits"] == 0 and r.json()["g_brf_sum_texts"] == []
        # a substring of a claim, yet no single token holds it
        r = get(client, key, "/api/v1/g_claim/", {"_contains": {"claim_text": "rotating brush"}})
        assert r.status_code == 200 and r.json()["total_hits"] == 0
        r = get(client, key, "/api/v1/g_claim/", {"_contains": {"claim_text": "claim 1,"}})
        assert r.status_code == 200 and r.json()["total_hits"] == 0
        r = get(client, key, "/api/v1/g_detail_desc_text/", {"_contains": {"description_text": "dry-room"}})
        assert r.status_code == 200 and r.json()["total_hits"] == 0
    stmts = log.statements()
    assert stmts and all("lapse_tok_wild" not in s and "instr(" not in s for s in stmts), stmts
    assert all(" WHERE 0 " in s or s.endswith(" WHERE 0") for s in stmts), stmts
    # the phrase form finds the summary; a needle one token holds still matches (a small table: under the scan limit)
    r = get(client, key, "/api/v1/g_brf_sum_text/", {"_text_phrase": {"summary_text": "solid electrolyte"}})
    assert [x["patent_id"] for x in r.json()["g_brf_sum_texts"]] == ["12050000"]
    r = get(client, key, "/api/v1/g_claim/", {"_contains": {"claim_text": "OTAT"}})
    assert [x["patent_id"] for x in r.json()["g_claims"]] == ["12050006"]
    # the per-row test (lapse_tok_wild) agrees: no text of the fixture has a token holding these needles
    from API.search_sqlite import _tok_wild

    texts = [plain(v) for (v,) in _rows(text_env, "SELECT claim_text FROM g_claims UNION ALL SELECT summary_text FROM g_brf_sum_texts "
                                                  "UNION ALL SELECT description_text FROM g_detail_desc_texts UNION ALL SELECT draw_desc_text FROM g_draw_desc_texts")]
    for needle in ("solid electrolyte", "rotating brush", "claim 1,", "dry-room", "fig. 1", "a.b", "x y"):
        assert not any(_tok_wild(t, f"*{needle}*") for t in texts if t is not None), needle


def test_scan_of_a_large_long_text_table_is_refused_unless_patent_id_narrows_it(client, user_key, text_env, roomy):
    """The cost cap for a test that reads every row of a long-text table (_contains with a needle one token can hold,
    a range, an empty _begins): no index serves it, so on a table above LAPSE_TEXT_SCAN_MAX_ROWS rows (20,000; the
    full corpus holds 5.7 to 136 million) it is refused with the documented 400 before any SQL runs, unless an
    equality on patent_id (a value, a list, or an _or of values) sits in the same _and. The fixture's tables hold
    3 to 8 rows, so the limit is lowered to 2 here."""
    _, key, _ = user_key
    roomy.setattr(settings, "LAPSE_TEXT_SCAN_MAX_ROWS", 2)
    with _SqlLog("sql_refused.jsonl") as log:
        r = get(client, key, "/api/v1/g_claim/", {"_contains": {"claim_text": "rotat"}})
    assert r.status_code == 400, r.content
    assert r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_Q"
    reason = r["X-Status-Reason"]
    assert reason.startswith("Query too expensive: _contains on claim_text reads and decompresses every row of g_claims (8 rows;"), reason
    assert "_text_any, _text_all or _text_phrase on claim_text" in reason and "put patent_id" in reason
    assert chr(0x2014) not in reason
    assert log.statements() == []  # refused before any SQL ran
    for q in ({"_gte": {"claim_text": "a"}},
              {"_begins": {"summary_text": ""}},
              {"_or": [{"patent_id": "12050006"}, {"_contains": {"claim_text": "rotat"}}]},
              {"_not": {"_contains": {"claim_text": "rotat"}}},
              {"_and": [{"_gte": {"patent_id": "12050000"}}, {"_contains": {"claim_text": "rotat"}}]}):
        ep = "/api/v1/g_brf_sum_text/" if "_begins" in q else "/api/v1/g_claim/"
        r = get(client, key, ep, q)
        assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q", (q, r.content)
        assert r["X-Status-Reason"].startswith("Query too expensive: "), q
    # narrowed by patent_id: answered, the same rows as without the limit
    r = get(client, key, "/api/v1/g_claim/", {"_and": [{"patent_id": "12050006"}, {"_contains": {"claim_text": "rotat"}}]})
    assert r.status_code == 200, r.content
    assert [(x["patent_id"], x["claim_sequence"]) for x in r.json()["g_claims"]] == [("12050006", 0)]
    r = get(client, key, "/api/v1/g_claim/", {"_and": [{"patent_id": ["12050000", "12050006"]}, {"_contains": {"claim_text": "brush"}}]})
    assert r.status_code == 200 and [x["claim_sequence"] for x in r.json()["g_claims"]] == [0, 2]
    r = get(client, key, "/api/v1/g_claim/", {"_and": [{"_or": [{"patent_id": "12050000"}, {"patent_id": "12050006"}]},
                                                        {"_contains": {"claim_text": "brush"}}]})
    assert r.status_code == 200 and r.json()["total_hits"] == 2
    r = get(client, key, "/api/v1/g_claim/", {"_and": [{"patent_id": "12050006"}, {"_not": {"_contains": {"claim_text": "brush"}}}]})
    assert r.status_code == 200 and [x["claim_sequence"] for x in r.json()["g_claims"]] == [1]
    # a needle no token holds is answered (no rows), never refused; the full-text operators are untouched
    r = get(client, key, "/api/v1/g_claim/", {"_contains": {"claim_text": "rotating brush"}})
    assert r.status_code == 200 and r.json()["total_hits"] == 0
    r = get(client, key, "/api/v1/g_claim/", {"_text_any": {"claim_text": "brush"}})
    assert r.status_code == 200 and r.json()["total_hits"] == 2
    # the main snapshot is not a long-text file: the limit does not apply there
    r = get(client, key, "/api/v1/patent/", {"_contains": {"patent_title": "pix"}}, f=["patent_id"])
    assert r.status_code == 200, r.content


def test_neq_over_every_row_counts_from_row_counts_and_walks_the_sort_index(client, user_key, text_env, roomy):
    """{"_neq": {"patent_id": ""}} (the R package's api-changes vignette, size 1, sorted asc then desc to find the
    first and last patent) matches every row. On the full corpus the page scanned and sorted the whole table and the
    count read every row: 500 at the 20 s limit. Now total_hits is _lapse_build.row_counts minus the rows the negated
    clause matches (one index lookup), and the page walks the sort key's index (on g_claims the (patent_id,
    claim_sequence) index). Every total is checked against a count over the file."""
    _, key, _ = user_key
    path = text_env
    with _SqlLog("sql_neq.jsonl") as log:
        r = get(client, key, "/api/v1/g_brf_sum_text/", {"_neq": {"patent_id": ""}}, s=[{"patent_id": "asc"}], o={"size": 1})
        assert r.status_code == 200, r.content
        js = r.json()
        assert js["total_hits"] == _rows(path, "SELECT count(*) FROM g_brf_sum_texts")[0][0] == 4
        assert [x["patent_id"] for x in js["g_brf_sum_texts"]] == ["12050000"]
        r = get(client, key, "/api/v1/g_brf_sum_text/", {"_neq": {"patent_id": ""}}, s=[{"patent_id": "desc"}], o={"size": 1})
        assert [x["patent_id"] for x in r.json()["g_brf_sum_texts"]] == ["12050007"] and r.json()["total_hits"] == 4
    stmts = log.statements()
    counts = [s for s in stmts if s.startswith("SELECT count(*)")]
    pages = [s for s in stmts if not s.startswith("SELECT count(*)")]
    assert counts and all("NOT COALESCE" not in s and 'm."patent_id" = ?' in s for s in counts), counts
    assert pages and all('INDEXED BY "ix_g_brf_sum_texts_patent_id"' in s for s in pages), pages
    # claims: the default sort (patent_id, claim_sequence), the _not form, two negations in an _and
    cases = [
        ({"_neq": {"patent_id": "12050006"}}, "patent_id != '12050006'"),
        ({"_not": {"patent_id": "12050006"}}, "patent_id != '12050006'"),
        ({"_and": [{"_neq": {"patent_id": "12050006"}}, {"_neq": {"patent_id": "11000000"}}]}, "patent_id NOT IN ('12050006', '11000000')"),
        ({"_neq": {"claim_sequence": 0}}, "claim_sequence IS NULL OR claim_sequence != 0"),
        ({"_neq": {"patent_id": ["12050000", "12050007"]}}, "patent_id NOT IN ('12050000', '12050007')"),
    ]
    with _SqlLog("sql_neq_claims.jsonl") as log:
        for q, where in cases:
            r = get(client, key, "/api/v1/g_claim/", q, o={"size": 100})
            assert r.status_code == 200, (q, r.content)
            want = _rows(path, f"SELECT patent_id, claim_sequence FROM g_claims WHERE {where} "
                               "ORDER BY patent_id, claim_sequence IS NULL, claim_sequence, rowid")
            got = [(x["patent_id"], x["claim_sequence"]) for x in r.json()["g_claims"]]
            assert got == [tuple(w) for w in want], (q, got, want)
            assert r.json()["total_hits"] == len(want), q
    stmts = log.statements()
    assert all("NOT COALESCE" not in s for s in stmts if s.startswith("SELECT count(*)")), stmts
    assert any('INDEXED BY "ix_g_claims_patent_id_claim_sequence"' in s for s in stmts), stmts
    # a page after a cursor and a positive query are untouched: no hint, the counts as before
    r = get(client, key, "/api/v1/g_claim/", {"_neq": {"patent_id": ""}}, s=[{"patent_id": "asc"}, {"claim_sequence": "asc"}],
            o={"size": 2, "after": ["12050006", 0]})
    assert [(x["patent_id"], x["claim_sequence"]) for x in r.json()["g_claims"]] == [("12050006", 1), ("12050006", 2)]
    assert r.json()["total_hits"] == 8
    r = get(client, key, "/api/v1/g_claim/", {"patent_id": "12050006"})
    assert r.json()["total_hits"] == 3


def test_complement_count_needs_an_exact_row_count(text_env):
    """Without row_counts (a text file from before build_text recorded them) the count is the plain one, same answer."""
    import shutil

    copy = str(_TMP / "text_no_counts.db")
    shutil.copyfile(text_env, copy)
    con = sqlite3.connect(copy)
    con.execute("DELETE FROM _lapse_build WHERE k='row_counts'")
    con.commit()
    con.close()
    old = settings.LAPSE_TEXT_PATH
    settings.LAPSE_TEXT_PATH = copy
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    try:
        s = LapseSQLiteSearch.from_django_settings()
        meta = s.meta("g_claims")
        assert meta.wide and meta.exact_rows is None
        q = {"query": {"bool": {"must_not": [{"match": {"patent_id": "12050006"}}]}}}
        with _SqlLog("sql_nocounts.jsonl") as log:
            assert s.count("g_claims", q)["count"] == 5
        assert any("NOT COALESCE" in st for st in log.statements())
    finally:
        settings.LAPSE_TEXT_PATH = old
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()
        os.remove(copy)
