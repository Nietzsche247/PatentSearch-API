"""Contract tests for the S1-D endpoints (release AG, gate S1D.4; API/lapse_structure_s1d.py through API/lapse_s1d.py):
GET /api/v1/structure/units/<patent_id>/, GET /api/v1/structure/relations/<patent_id>/, GET /api/v1/structure/locate/ and
GET or POST /api/v1/structure/units/.

A search file and a long-text file in the box's layouts (claims, brief summaries and detailed descriptions zlib-compressed,
_lapse_build.data_version) hold six made-up patents; the structure file is built from them by s1d's own writer, as the corpus
build made /data/s1d_corpus/structure_20261110.1_s1d-11.db. RE900001 has a claim row without claim_sequence, as RE31177 has;
9000004 has neither claims nor a brief summary, as 8419894 and 9815822 have. Checks: the shapes and version fields
(s1d_version, never structure_version; experimental true), every quote the text at its locator (read back through locate),
the types, relations and f parameters, 400, 404, 501 ERR_NOT_IMPLEMENTED and 501 ERR_NO_STRUCTURE_FILE, the clean answers for
the two kinds of unreadable patent, names_same_part with its ranks, cursor and lookup facts, the units query's grammar, the
key rule, and the wording of what the endpoints write. s1d itself is not part of this repository (test_s1d_package checks the
fork's side of it).

  python -m pytest lapse_accounts/tests/test_structure_s1d.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import re
import sqlite3
import uuid
import zlib
from pathlib import Path

import pytest
from django.conf import settings

from API import lapse_s1d, lapse_structure, lapse_structure_s1d
from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP

DATA_VERSION = "20261110.1"
TEXT_VERSION = "20261110.1"
UNITS, RELS, LOCATE = "/api/v1/structure/units/", "/api/v1/structure/relations/", "/api/v1/structure/locate/"
# the words the coordinator's brief bars from every structure output (plan of record 2026-10-29), and release AG's list
BANNED = re.compile(r"\b(risk\w*|clear\w*|safe\w*|free to use|invalid\w*|valid|validity|scope\w*|breadth|strength\w*|broad\w*|"
                    r"narrow\w*|infring\w*|covers?|covered|no other|first)\b", re.I)
PROBLEM = "It is an object of the present invention to provide a flux capacitor that runs cooler than known capacitors."
PATENTS = {
    "9000001": {"claims": [("0", "1. A time machine comprising a flux capacitor and a drive motor coupled to the flux capacitor."),
                           ("1", "2. The time machine of claim 1, wherein the drive motor is an electric motor.")],
                "summaries": ["The invention relates to vehicles. " + PROBLEM + " The rest of the summary follows."],
                "descriptions": []},
    "9000002": {"claims": [("0", "1. An apparatus comprising a flux capacitor mounted in a housing and a cooling fan.")],
                "summaries": ["A short summary of the apparatus that has no stated aim in it at all."],
                "descriptions": ["BACKGROUND OF THE INVENTION\nThere is a need for a quieter flux capacitor for road vehicles. "
                                 "More text follows here."]},
    "RE900001": {"claims": [("0", "1. A printing plate comprising a base layer and a coating."),
                            (None, "25. The plate of claim 24 wherein the coating is thin.")],
                 "summaries": ["It is an object of the invention to provide a printing plate that wears less in long runs."],
                 "descriptions": []},
    "9000004": {"claims": [], "summaries": [], "descriptions": []},
    "9000005": {"claims": [("0", "1. A drive assembly comprising a gear train and a cooling fan.")], "summaries": [],
                "descriptions": []},
}
UNKNOWN = "9999998"


def build_files(search, text, structure):
    for p in (search, text, structure):
        if Path(p).exists():
            Path(p).unlink()
    con = sqlite3.connect(search)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", DATA_VERSION), ("release", "20261110")])
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, "
                "patent_type TEXT, withdrawn INT)")
    con.executemany("INSERT INTO patents VALUES (?,?,?,?,?,0)", [(p, p, "t", "2020-01-07", "utility") for p in PATENTS])
    con.commit()
    con.close()
    con = sqlite3.connect(text)
    con.execute('CREATE TABLE "g_claims" ("uuid" TEXT PRIMARY KEY, "patent_id" TEXT, "patent_zero_prefix" TEXT, "claim_number" TEXT, '
                '"claim_dependent" TEXT, "claim_text" BLOB, "claim_sequence" INTEGER, "exemplary" INTEGER)')
    con.execute('CREATE INDEX "ix_g_claims_patent_id_claim_sequence" ON "g_claims"("patent_id", "claim_sequence")')
    con.execute("CREATE TABLE g_brf_sum_texts(uuid TEXT, patent_id TEXT, patent_zero_prefix TEXT, summary_text BLOB)")
    con.execute("CREATE INDEX ix_brf ON g_brf_sum_texts(patent_id)")
    con.execute("CREATE TABLE g_detail_desc_texts(uuid TEXT, patent_id TEXT, patent_zero_prefix TEXT, description_text BLOB, "
                "description_length INTEGER)")
    con.execute("CREATE INDEX ix_desc ON g_detail_desc_texts(patent_id)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", TEXT_VERSION), ("kind", "text")])
    for pid, p in PATENTS.items():
        for i, (seq, t) in enumerate(p["claims"]):
            con.execute("INSERT INTO g_claims VALUES (?,?,?,?,?,?,?,?)",
                        (f"{pid}-{seq}", pid, pid, str(i + 1), None, zlib.compress(t.encode("utf-8")),
                         int(seq) if seq is not None else None, None))
        for t in p["summaries"]:
            con.execute("INSERT INTO g_brf_sum_texts VALUES (?,?,?,?)", (pid, pid, pid, zlib.compress(t.encode("utf-8"))))
        for t in p["descriptions"]:
            con.execute("INSERT INTO g_detail_desc_texts VALUES (?,?,?,?,?)", (pid, pid, pid, zlib.compress(t.encode("utf-8")), len(t)))
    con.commit()
    con.close()
    # the structure file, as s1d's batch build writes it (a patent that raises is counted and gets no units)
    from s1d import patent, structdb, textdb
    w = structdb.Writer(structure, text_data_version=TEXT_VERSION)
    tcon = textdb.connect(text)
    for pid in PATENTS:
        rows, sums, descs = textdb.claim_rows(tcon, pid), textdb.summaries(tcon, pid), textdb.descriptions(tcon, pid)
        try:
            units = patent.build(pid, rows, sums, descs)["units"] if rows or sums else []
        except ValueError:
            units = []
        w.add(pid, units)
    tcon.close()
    w.finish()


@pytest.fixture(scope="module")
def files():
    paths = (str(_TMP / "s1d_search.db"), str(_TMP / "s1d_text.db"), str(_TMP / "s1d_structure.db"))
    build_files(*paths)
    return paths


def _swap(search, text, structure):
    old = (dict(settings.LAPSE_SQLITE), getattr(settings, "LAPSE_TEXT_PATH", None), getattr(settings, "LAPSE_STRUCTURE_PATH", None))
    settings.LAPSE_SQLITE["path"] = search
    settings.LAPSE_TEXT_PATH = text
    settings.LAPSE_STRUCTURE_PATH = structure
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    lapse_structure_s1d.reset()
    return old


def _restore(old):
    settings.LAPSE_SQLITE.clear()
    settings.LAPSE_SQLITE.update(old[0])
    settings.LAPSE_TEXT_PATH, settings.LAPSE_STRUCTURE_PATH = old[1], old[2]
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    lapse_structure_s1d.reset()


@pytest.fixture
def env(files):
    old = _swap(*files)
    try:
        yield files
    finally:
        _restore(old)


@pytest.fixture
def no_structure_env(files):
    old = _swap(files[0], files[1], str(_TMP / "no_such_structure.db"))
    try:
        yield
    finally:
        _restore(old)


@pytest.fixture
def no_text_env(files):
    old = _swap(files[0], str(_TMP / "no_such_s1d_text.db"), files[2])
    try:
        yield
    finally:
        _restore(old)


_KEY = {}


def mint_key(client, token_factory):
    """A Free key from the sign-up route, sent from an address of its own: the route's per-minute rate per address is shared
    with every other suite of the run from 127.0.0.1 (test_legal_posted does the same)."""
    sub = str(uuid.uuid4())
    token = token_factory(sub=sub, email=f"user-{sub[:8]}@d{sub[:8]}.example.test")
    r = client.post("/api/v1/meta/keys/", data=json.dumps({"terms_version": settings.LAPSE_TERMS_VERSION}),
                    content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token,
                    REMOTE_ADDR=f"10.{uuid.uuid4().int % 200 + 20}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250 + 1}")
    assert r.status_code == 201, r.content
    return r.json()["api_key"]


@pytest.fixture
def keys(client, token_factory, monkeypatch):
    """One Free key for the module, minted once, with room for this module's requests: these tests send more than the 5 a
    month and the 45 a minute conftest gives a Free key (as test_long_text's roomy fixture)."""
    monkeypatch.setattr(settings, "LAPSE_FREE_MONTHLY_LIMIT", 100000, raising=False)
    monkeypatch.setattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 100000, raising=False)
    monkeypatch.setattr(settings, "LAPSE_IP_PER_MINUTE", 100000, raising=False)
    if "key" not in _KEY:
        _KEY["key"] = mint_key(client, token_factory)
    return lambda: _KEY["key"]


def get(client, key, path, **params):
    return client.get(path, params, HTTP_X_API_KEY=key)


def text_at(loc):
    p = PATENTS[loc["patent_id"]]
    if loc["field"] == "claims":
        t = dict((s, x) for s, x in p["claims"])[str(loc["sequence"])]
    elif loc["field"] == "brief_summary":
        t = p["summaries"][loc["sequence"]]
    else:
        t = p["descriptions"][loc["sequence"]]
    return t[loc["start"]:loc["end"]]


def no_structure_version(obj):
    if isinstance(obj, dict):
        assert "structure_version" not in obj and "patents_naming" not in obj, obj
        for v in obj.values():
            no_structure_version(v)
    elif isinstance(obj, list):
        for v in obj:
            no_structure_version(v)


def own_strings(obj, quoted=("text", "part", "patent_id", "dst", "src", "unit_id", "term_id", "patent_unit_id")):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            if k not in quoted:
                yield from own_strings(v, quoted)
    elif isinstance(obj, list):
        for v in obj:
            yield from own_strings(v, quoted)
    elif isinstance(obj, str):
        yield obj


# ------------------------------------------------------------------ units of one patent

def test_units_shape_versions_and_quotes(client, keys, env):
    r = get(client, keys(), UNITS + "9000001/")
    assert r.status_code == 200, r.content
    js = r.json()
    assert list(js)[:9] == ["error", "patent_id", "patent_unit_id", "s1d_version", "generator", "data_version", "text_data_version",
                            "as_of", "experimental"]
    assert js["error"] is False and js["experimental"] is True and js["patent_unit_id"] == "US9000001"
    assert js["s1d_version"] == "s1d-11" == r["X-S1D-Version"] and js["generator"] == lapse_s1d.GENERATOR
    assert js["data_version"] == DATA_VERSION and js["text_data_version"] == TEXT_VERSION == r["X-Text-Data-Version"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", js["as_of"])
    no_structure_version(js)
    types = {u["type"] for u in js["units"]}
    assert types == {"Component", "StatedProblem"} and js["counts"]["StatedProblem"] == 1 and js["none_found"] == []
    for u in js["units"]:
        assert u["s1d_version"] == "s1d-11" and u["text"] == text_at(u["locator"]), u
    parts = {u["part"] for u in js["units"] if u["type"] == "Component"}
    assert {"flux capacitor", "drive motor"} <= parts
    sp = [u for u in js["units"] if u["type"] == "StatedProblem"][0]
    assert sp["text"] == PROBLEM and sp["locator"]["field"] == "brief_summary" and sp["unit_id"] == "US9000001-p1"
    assert {r_["rel"] for r_ in js["relations"]} == {"recites", "states"} and js["unread"] == [] and js["left_out"] == 0
    assert js["texts_read"] == {"claims": 2, "brief_summary": 1, "detail_description": 0}
    assert r["Cache-Control"] == "public, max-age=300" and 'desc="miss"' in r["Server-Timing"]
    again = get(client, keys(), UNITS + "US9000001")
    assert again.status_code == 200 and again.content == r.content and 'desc="hit"' in again["Server-Timing"]


def test_units_types_relations_and_f(client, keys, env):
    js = get(client, keys(), UNITS + "9000001/", types="StatedProblem", relations="none").json()
    assert [u["type"] for u in js["units"]] == ["StatedProblem"] and js["relations"] == [] and set(js["counts"]) == {"StatedProblem", "states"}
    js = get(client, keys(), UNITS + "9000001/", types="Component", f='["part"]').json()
    assert js["units"] and all(set(u) == {"unit_id", "type", "locator", "part"} for u in js["units"])
    assert {r["rel"] for r in js["relations"]} == {"recites"}
    for bad, why in ((dict(types="StatedPriorArtDrawback"), "'types' is a comma list"), (dict(types="StatedAdvantage"), "'types'"),
                     (dict(relations="some"), "'relations' is all or none"), (dict(f='["nope"]'), "unknown unit fields"),
                     (dict(f="{not json"), "could not be read as JSON")):
        r = get(client, keys(), UNITS + "9000001/", **bad)
        assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q" and why in r["X-Status-Reason"], (bad, r["X-Status-Reason"])


def test_units_errors_and_the_two_kinds_of_unreadable_patent(client, keys, env):
    r = get(client, keys(), UNITS + "not-a-number/")
    assert r.status_code == 400 and r["X-Status-Reason"] == "Not a patent number: 'not-a-number'"
    r = get(client, keys(), UNITS + UNKNOWN + "/")
    assert r.status_code == 404 and r["X-Status-Reason-Code"] == "NOT_FOUND" and "not in the data in service" in r["X-Status-Reason"]
    # neither claims nor a brief summary (8419894, 9815822): a clean 404, as the claim tree answers a patent without claim text
    r = get(client, keys(), UNITS + "9000004/")
    assert r.status_code == 404 and r["X-Status-Reason-Code"] == "NOT_FOUND"
    assert r["X-Status-Reason"].startswith("No claim text and no brief summary for patent_id '9000004'")
    # a claim row without claim_sequence (RE31177): 200, the stated units, no part, and why in unread
    r = get(client, keys(), UNITS + "RE900001/")
    assert r.status_code == 200, r.content
    js = r.json()
    assert [u["type"] for u in js["units"]] == ["StatedProblem"] and js["counts"]["Component"] == 0
    assert js["unread"][0]["field"] == "claims" and js["unread"][0]["claim_rows"] == 2 and "no claim_sequence" in js["unread"][0]["reason"]
    assert "Component" not in js["none_found"] and js["texts_read"]["claims"] == 0
    assert js["units"][0]["text"] == text_at(js["units"][0]["locator"])
    # a patent with claims only: its parts, and none found for the problem
    js = get(client, keys(), UNITS + "9000005/").json()
    assert js["none_found"] == ["StatedProblem"] and js["counts"]["Component"] >= 2


def test_units_without_a_long_text_file_is_501(client, keys, no_text_env):
    r = get(client, keys(), UNITS + "9000001/")
    assert r.status_code == 501 and r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED"


def test_keyless_is_403(client, env):
    for path in (UNITS + "9000001/", RELS + "9000001/", LOCATE + "?patent_id=9000001&field=claims&sequence=0&start=0&end=5",
                 UNITS + '?q={"patent_id":"9000001"}'):
        assert client.get(path).status_code == 403, path


# ------------------------------------------------------------------ names_same_part, recites, states

def test_names_same_part_ranked_with_quotes_and_lookup_facts(client, keys, env):
    r = get(client, keys(), RELS + "9000001/")
    assert r.status_code == 200, r.content
    js = r.json()
    assert js["rel"] == "names_same_part" and js["experimental"] is True and js["s1d_version"] == "s1d-11"
    assert js["structure_data_version"] == f"{TEXT_VERSION}_s1d-11" == r["X-Structure-Data-Version"]
    no_structure_version(js)
    links = js["relations"]
    assert [x["dst"] for x in links] == ["US9000002"] and links[0]["rank"] == 1 and js["next_after"] is None
    sp = links[0]["shared_parts"]
    assert [p["part"] for p in sp] == ["flux capacitor"] and sp[0]["patents_with_key"] == 2
    assert sp[0]["src"]["text"] == text_at(sp[0]["src"]["locator"]) and sp[0]["dst"]["text"] == text_at(sp[0]["dst"]["locator"])
    lk = js["link_lookup"]
    assert lk["df_max"] == 2 and lk["share"] == lapse_s1d.DF_SHARE and lk["budget_reached"] is False and js["left_out"] == 0
    # the key named by three patents is too common at df_max 2 here
    assert lk["parts_too_common"] >= 0 and lk["rows_read"] >= 2


def test_names_same_part_paging_and_the_cursor(client, keys, env):
    js = get(client, keys(), RELS + "9000002/", o='{"size": 1}').json()
    assert len(js["relations"]) == 1 and js["relations"][0]["rank"] == 1
    r = get(client, keys(), RELS + "9000002/", o=json.dumps({"size": 1, "after": [9, 9, "1", 1]}))
    assert r.status_code == 400 and "does not match" in r["X-Status-Reason"]
    r = get(client, keys(), RELS + "9000002/", o='{"size": 1, "after": [1, 2]}')
    assert r.status_code == 400 and "rank" in r["X-Status-Reason"]
    r = get(client, keys(), RELS + "9000002/", o='{"size": 0}')
    assert r.status_code == 400
    js = get(client, keys(), RELS + "9000002/", o='{"size": 500}').json()
    assert js["count"] <= 100


def test_recites_and_states_need_no_structure_file(client, keys, no_structure_env):
    r = get(client, keys(), RELS + "9000001/")
    assert r.status_code == 501 and r["X-Status-Reason-Code"] == "ERR_NO_STRUCTURE_FILE"
    assert r["X-Status-Reason"] == "The structure file is not attached on this server."
    for rel in ("recites", "states"):
        js = get(client, keys(), RELS + "9000001/", rel=rel).json()
        assert js["rel"] == rel and js["relations"] and all(x["rel"] == rel for x in js["relations"])
    assert get(client, keys(), UNITS + "9000001/").status_code == 200
    r = get(client, keys(), RELS + "9000001/", rel="cites")
    assert r.status_code == 400


def test_names_same_part_of_the_unreadable_patents(client, keys, env):
    js = get(client, keys(), RELS + "RE900001/").json()
    assert js["relations"] == [] and js["unread"] and js["link_lookup"]["parts"] == 0
    r = get(client, keys(), RELS + "9000004/")
    assert r.status_code == 404 and r["X-Status-Reason-Code"] == "NOT_FOUND"


# ------------------------------------------------------------------ locate

def test_locate_reads_back_every_quote(client, keys, env):
    js = get(client, keys(), UNITS + "9000002/").json()
    for u in js["units"]:
        r = get(client, keys(), LOCATE, **{k: str(v) for k, v in u["locator"].items()})
        assert r.status_code == 200, (u, r.content)
        got = r.json()
        assert got["text"] == u["text"] and got["locator"] == u["locator"] and got["experimental"] is True


def test_locate_refusals(client, keys, env):
    base = {"patent_id": "9000001", "field": "claims", "sequence": "0", "start": "0", "end": "10"}
    for change, code, why in (({"field": "abstract"}, 400, "'field' is one of"), ({"end": "0"}, 400, "more than 'start'"),
                              ({"end": "2501", "start": "0"}, 400, "at most 2000"), ({"sequence": "7"}, 404, "No text at this locator"),
                              ({"start": "x"}, 400, "whole number"), ({"patent_id": "abc"}, 400, "Not a patent number"),
                              ({"end": "99999", "start": "99990"}, 404, "holds")):
        r = get(client, keys(), LOCATE, **dict(base, **change))
        assert r.status_code == code and why in r["X-Status-Reason"], (change, r.status_code, r.get("X-Status-Reason"))
    r = get(client, keys(), LOCATE, patent_id="9000001")
    assert r.status_code == 400 and "locate needs field, sequence, start, end" in r["X-Status-Reason"]


# ------------------------------------------------------------------ the units query

def test_units_query_by_part_and_by_patent(client, keys, env):
    js = get(client, keys(), UNITS, q='{"part": "flux capacitor"}', f='["patent_id", "unit_id", "text", "locator"]').json()
    assert [u["patent_id"] for u in js["units"]] == ["9000001", "9000002"] and js["total_hits"] == 2 and js["total_hits_exact"] is True
    assert all(set(u) == {"patent_id", "unit_id", "text", "locator"} and u["text"] == text_at(u["locator"]) for u in js["units"])
    assert js["experimental"] is True and js["structure_data_version"] == f"{TEXT_VERSION}_s1d-11"
    js = get(client, keys(), UNITS, q='{"part": "second flux capacitor"}').json()   # compared without leading ordinal words
    assert js["total_hits"] == 2
    q = json.dumps({"_and": [{"type": "StatedProblem"}, {"patent_id": ["9000001", "9000002", "RE900001"]}]})
    js = get(client, keys(), UNITS, q=q).json()
    assert [(u["patent_id"], u["unit_id"]) for u in js["units"]] == [("9000001", "US9000001-p1"), ("9000002", "US9000002-p1")]
    assert all(u["text"] == text_at(u["locator"]) for u in js["units"])
    js = get(client, keys(), UNITS, q='{"patent_id": "9000005"}', o='{"size": 1}').json()
    assert js["count"] == 1 and js["next_after"] is not None
    nxt = get(client, keys(), UNITS, q='{"patent_id": "9000005"}', o=json.dumps({"size": 1, "after": js["next_after"]})).json()
    assert nxt["count"] == 1 and nxt["units"][0]["unit_id"] != js["units"][0]["unit_id"]
    r = client.post(UNITS, data=json.dumps({"q": {"part": "flux capacitor"}, "o": {"size": 1}}), content_type="application/json",
                    HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json()["count"] == 1


def test_units_query_refusals_and_the_501(client, keys, env, files):
    for q, why in (('{"type": "Component"}', "must name patent_id"), ('{"patent_id": {"_gt": "1"}}', "not offered"),
                   ('{"_or": [{"patent_id": "9000001"}, {"part": "gear"}]}', "on one field"), ('{"text": "x"}', "filters by"),
                   ('{"_not": {"patent_id": "1"}}', "not offered"), ('{"patent_id": "1", "part": "x"}', "one key"),
                   ('{"type": "StatedAdvantage", "patent_id": "1"}', "one key")):
        r = get(client, keys(), UNITS, q=q)
        assert r.status_code == 400 and why in r["X-Status-Reason"], (q, r["X-Status-Reason"])
    r = get(client, keys(), UNITS, q='{"patent_id": "9000001"}', s='[{"type": "asc"}]')
    assert r.status_code == 400 and "sorts by patent_id" in r["X-Status-Reason"]
    r = get(client, keys(), UNITS, q='{"patent_id": "9000001"}', s='[{"text": "asc"}]')
    assert r.status_code == 400
    r = get(client, keys(), UNITS, q=json.dumps({"patent_id": [str(9000000 + i) for i in range(101)]}))
    assert r.status_code == 400 and "at most 100" in r["X-Status-Reason"]
    old = _swap(files[0], files[1], str(_TMP / "no_such_structure.db"))
    try:
        r = get(client, keys(), UNITS, q='{"patent_id": "9000001"}')
        assert r.status_code == 501 and r["X-Status-Reason-Code"] == "ERR_NO_STRUCTURE_FILE"
    finally:
        _restore(old)
        _swap(*files)


# ------------------------------------------------------------------ the wording and the file

def test_what_the_endpoints_write_has_no_barred_word(client, keys, env):
    seen = []
    for path, params in ((UNITS + "9000001/", {}), (UNITS + "RE900001/", {}), (RELS + "9000001/", {}),
                         (LOCATE, {"patent_id": "9000001", "field": "claims", "sequence": "0", "start": "0", "end": "4"}),
                         (UNITS, {"q": '{"part": "flux capacitor"}'})):
        js = get(client, keys(), path, **params).json()
        for s in own_strings(js):
            assert not BANNED.search(s), (path, s)
            assert "—" not in s and "–" not in s
        seen.append(path)
    for code in ("ERR_Q", "NOT_FOUND"):
        for path in (UNITS + "zz/", UNITS + UNKNOWN + "/", UNITS + "9000004/", LOCATE + "?patent_id=1"):
            r = get(client, keys(), path)
            assert not BANNED.search(r.get("X-Status-Reason", "")), (path, r.get("X-Status-Reason"))
    assert len(seen) == 5
    src = (Path(lapse_structure_s1d.__file__).read_text(encoding="utf-8") + Path(lapse_s1d.__file__).read_text(encoding="utf-8"))
    assert "—" not in src and "–" not in src


def test_the_structure_file_is_opened_read_only(env):
    con, facts = lapse_structure_s1d.structure()
    assert con is not None and facts["structure_version"] == "s1d-11" and facts["patents"] == len(PATENTS)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("CREATE TABLE x(a)")
    assert facts["df_max"] == lapse_s1d.df_max_for(len(PATENTS), lapse_s1d.DF_SHARE) == 2


def test_the_fallback_share_is_a_setting(env, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_S1D_DF_SHARE", 0.0002, raising=False)
    lapse_structure_s1d.reset()
    assert lapse_structure_s1d.df_share() == lapse_s1d.DF_SHARE_FALLBACK
    monkeypatch.setattr(settings, "LAPSE_S1D_DF_SHARE", 0.5, raising=False)
    assert lapse_structure_s1d.df_share() == lapse_s1d.DF_SHARE
    lapse_structure_s1d.reset()
