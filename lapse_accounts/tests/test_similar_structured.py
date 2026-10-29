"""Contract tests for the structured mode of GET /api/v1/lapse/similar (lane Y; API/lapse_similar_structured.py).

A small search file in the Lapse layout with fts_patents (title and abstract, as on the box) and a long-text file with
txt.g_claims (zlib-compressed claim text, the (patent_id, claim_sequence) index), six made-up patents about pool
cleaners and one hose reel. Checks: without mode, and with mode=text, the answer is the text mode's, byte for byte the
same; another mode value is 400 ERR_Q; the structured answer's shape and versions; the text's term keys and their
weights; candidates from the title and abstract index (the text mode's query and the key phrases); scores, order and the
shared terms with offsets that point into the claim text the claim tree serves; a patent without claim text and one over
the work cap are left out; 501 without a long-text file; 403 without a key; the same answer twice; the per-worker cache;
the wording.

  python -m pytest lapse_accounts/tests/test_similar_structured.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import math
import os
import sqlite3
import uuid
import zlib

import pytest
from django.conf import settings

from API import lapse_similar_structured as ss
from API import lapse_structure, lapse_wording
from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP

DATA_VERSION = "20261027.2"
TEXT_VERSION = "text_test.1"
PATH = "/api/v1/lapse/similar"
QUERY = "A pool cleaner with a drive motor and a filter bag"
PATENTS = [  # patent_id, title, abstract, claims (None: no claim text in the long-text file)
    ("4000001", "Pool cleaner", "A pool cleaner has a drive motor and a filter bag for debris.",
     ["1. A pool cleaner comprising: a housing; a drive motor in the housing; and a filter bag attached to the housing.",
      "2. The pool cleaner of claim 1, wherein the filter bag is removable."]),
    ("4000002", "Pool cleaner with a brush", "A cleaner for a pool with a rotating brush.",
     ["1. A pool cleaner comprising: a brush; and an electric drive motor that turns the brush."]),
    ("4000003", "Filter bag for a pool skimmer", "A filter bag hangs in a skimmer basket.",
     ["1. A skimmer basket comprising: a rim; and a mesh liner."]),
    ("4000004", "Pool cleaner drive", "A drive motor for a pool cleaner.", None),
    ("4000005", "Garden hose reel", "A reel for a hose.", ["1. A hose reel comprising: a drum; and a crank."]),
    ("4000006", "Robotic pool cleaner", "A robotic pool cleaner with a pump.",
     ["1. A robotic pool cleaner comprising: a pump; a drive motor; and a filter bag."]),
]


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
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, "
                "patent_type TEXT, withdrawn INT, patent_abstract TEXT, gov_interest_statement TEXT)")
    for pid, title, abstract, _ in PATENTS:
        con.execute("INSERT INTO patents VALUES (?,?,?,?,?,0,?,NULL)", (pid, pid, title, "1977-01-04", "utility", abstract))
    con.execute("CREATE VIRTUAL TABLE fts_patents USING fts5(gov_interest_statement, patent_abstract, patent_title, content='patents', "
                "content_rowid='rowid', tokenize=\"unicode61 remove_diacritics 0 tokenchars '_'\")")
    con.execute("INSERT INTO fts_patents(fts_patents) VALUES ('rebuild')")
    con.commit()
    con.close()
    con = sqlite3.connect(text)
    con.execute('CREATE TABLE "g_claims" ("uuid" TEXT PRIMARY KEY, "patent_id" TEXT, "patent_zero_prefix" TEXT, "claim_number" TEXT, '
                '"claim_dependent" TEXT, "claim_text" BLOB, "claim_sequence" INTEGER, "exemplary" INTEGER)')
    con.execute('CREATE INDEX "ix_g_claims_patent_id_claim_sequence" ON "g_claims"("patent_id", "claim_sequence")')
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", TEXT_VERSION), ("kind", "text")])
    for pid, _t, _a, claims in PATENTS:
        for seq, c in enumerate(claims or []):
            dep = "claim 1" if "of claim 1" in c else None
            con.execute("INSERT INTO g_claims VALUES (?,?,?,?,?,?,?,?)",
                        (f"{pid}-{seq}", pid, pid, f"{seq + 1:05d}", dep, zlib.compress(c.encode("utf-8"), 6), seq, 1 if seq == 0 else 0))
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def files():
    search, text = str(_TMP / "similar_s_search.db"), str(_TMP / "similar_s_text.db")
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
    ss.CACHE.clear()
    return old, old_text


def _restore(old, old_text):
    settings.LAPSE_SQLITE.clear()
    settings.LAPSE_SQLITE.update(old)
    settings.LAPSE_TEXT_PATH = old_text
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    ss.CACHE.clear()


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
    """Fresh Free keys on demand (5 requests a key in the tests), each from an account of its own email domain."""
    def mint():
        sub = str(uuid.uuid4())
        token = token_factory(sub=sub, email=f"user-{sub[:8]}@s{sub[:8]}.example.test")
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


def get(client, key, **params):
    return client.get(PATH, params, HTTP_X_API_KEY=key)


@pytest.fixture
def phrases_on(monkeypatch):
    """Six patents make every word common: let the key phrases into the candidate query, as on 9.4 million patents."""
    monkeypatch.setattr(ss, "PHRASE_WORD_MAX_DF", 1.0)


# ------------------------------------------------------------------ the text mode is untouched

def test_without_mode_and_with_mode_text_the_answer_is_the_text_modes(client, keys, env):
    a = get(client, keys(), text=QUERY, n=5)
    b = get(client, keys(), text=QUERY, n=5, mode="text")
    assert a.status_code == b.status_code == 200
    assert a.content == b.content                                   # byte for byte
    body = a.json()
    assert body["method"] == "fts5_bm25_title_abstract" and "mode" not in body and "term_keys" not in body
    assert list(body) == ["error", "data_version", "as_of", "method", "n", "terms", "neighbors"]
    assert set(body["neighbors"][0]) == {"rank", "patent_id", "patent_title", "patent_date", "score"}
    assert "X-Text-Data-Version" not in a and "Server-Timing" not in a


def test_another_mode_is_400_and_the_text_rules_hold_in_structured_mode(client, keys, env):
    r = get(client, keys(), text=QUERY, mode="claims")
    assert r.status_code == 400 and r.json() == {"error": True} and r["X-Status-Reason-Code"] == "ERR_Q"
    assert r["X-Status-Reason"] == "'mode' must be text or structured"
    for params, reason in (({"text": "", "mode": "structured"}, "'text' is required"),
                           ({"text": QUERY, "n": 21, "mode": "structured"}, "'n' must be 1 to 20"),
                           ({"text": "x" * 4001, "mode": "structured"}, "'text' over 4000 characters")):
        r = get(client, keys(), **params)
        assert r.status_code == 400 and r["X-Status-Reason"] == reason, params


def test_403_without_a_key(client, env):
    assert client.get(PATH, {"text": QUERY, "mode": "structured"}).status_code == 403


def test_501_without_a_long_text_file(client, keys, no_text_env):
    r = get(client, keys(), text=QUERY, mode="structured")
    assert r.status_code == 501 and r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED" and "'g_claims' data set" in r["X-Status-Reason"]
    assert get(client, keys(), text=QUERY).status_code == 200          # the text mode needs no long text


# ------------------------------------------------------------------ the structured answer

def test_shape_versions_and_the_texts_keys(client, keys, env):
    r = get(client, keys(), text=QUERY, n=5, mode="structured")
    assert r.status_code == 200, r.content
    js = r.json()
    assert list(js) == ["error", "data_version", "text_data_version", "as_of", "method", "mode", "structure_version", "generator",
                        "n", "terms", "term_keys", "key_phrases", "candidates", "neighbors"]
    assert js["error"] is False and js["method"] == "claim_term_keys" and js["mode"] == "structured" and js["n"] == 5
    assert js["data_version"] == DATA_VERSION == r["X-Data-Version"] if "X-Data-Version" in r else js["data_version"] == DATA_VERSION
    assert js["text_data_version"] == TEXT_VERSION == r["X-Text-Data-Version"] and js["as_of"] == "2026-10-27"
    assert js["structure_version"] == "2" and js["generator"].startswith("claimgraph ")
    assert r["Server-Timing"].startswith('similar;desc="structured";dur=')
    # the keys claimgraph's term finder reads in the text, rarest first; weight ln(N / df of the rarest word), N = 6
    w2, w4 = round(math.log(6 / 2), 4), round(math.log(6 / 4), 4)
    assert js["term_keys"] == [{"key": "drive motor", "weight": w2}, {"key": "filter bag", "weight": w2}, {"key": "pool cleaner", "weight": w4}]
    assert js["key_phrases"] == []                                   # six patents make every word too common for a phrase query
    assert js["candidates"]["title_abstract"] >= 1 and js["candidates"]["key_phrases"] == 0


def test_scores_order_and_shared_terms(client, keys, env, phrases_on):
    js = get(client, keys(), text=QUERY, n=20, mode="structured").json()
    assert js["key_phrases"] == ["drive motor", "filter bag", "pool cleaner"] and js["candidates"]["key_phrases"] == 5
    ids = [x["patent_id"] for x in js["neighbors"]]
    # 4000001 has all three keys; 4000006 two, and "robotic pool cleaner" ends with "pool cleaner"; 4000002 "pool cleaner"
    # and "electric drive motor"; 4000003's claims introduce none (only its title has "filter bag"); 4000004 has no claim
    # text; 4000005 is not a candidate
    assert ids == ["4000001", "4000006", "4000002"]
    w2, w4 = math.log(6 / 2), math.log(6 / 4)
    tot = round(w2, 4) * 2 + round(w4, 4)
    want = {"4000001": 1.0, "4000006": round((2 * round(w2, 4) + 0.5 * round(w4, 4)) / tot, 4),
            "4000002": round((round(w4, 4) + 0.5 * round(w2, 4)) / tot, 4)}
    assert {x["patent_id"]: x["score"] for x in js["neighbors"]} == want
    assert [x["rank"] for x in js["neighbors"]] == [1, 2, 3]
    top = js["neighbors"][0]
    assert set(top) == {"rank", "patent_id", "patent_title", "patent_date", "score", "shared_terms"}
    assert [(s["key"], s["match"], s["claim"]) for s in top["shared_terms"]] == [("drive motor", "exact", 1), ("filter bag", "exact", 1),
                                                                                 ("pool cleaner", "exact", 1)]
    p2 = {s["key"]: s for s in js["neighbors"][2]["shared_terms"]}
    assert p2["drive motor"]["match"] == "partial" and p2["drive motor"]["claim_key"] == "electric drive motor"
    assert p2["drive motor"]["text"] == "an electric drive motor"
    assert js["candidates"]["with_claim_text"] == 4                      # 4000004 has no claim text


def test_every_shared_term_points_into_the_claim_text_the_claim_tree_serves(client, keys, env, phrases_on):
    js = get(client, keys(), text=QUERY, n=20, mode="structured").json()
    n = 0
    for nb in js["neighbors"]:
        tree = client.get(f"/api/v1/structure/claims/{nb['patent_id']}/", HTTP_X_API_KEY=keys()).json()
        by_number = {c["number"]: c for c in tree["claims"]}
        for s in nb["shared_terms"]:
            c = by_number[s["claim"]]
            assert c["text"][s["start"]:s["end"]] == s["text"], (nb["patent_id"], s)
            assert any(t["key"] == s["claim_key"] and t["start"] == s["start"] and t["role"] == "introduces" for t in c["terms"])
            n += 1
    assert n == 8                                                        # 3 + 3 + 2


def test_the_same_answer_twice_and_the_cache(client, keys, env, phrases_on):
    a = get(client, keys(), text=QUERY, n=20, mode="structured").json()
    assert len(ss.CACHE) == 5                                          # every candidate's keys (4000004: none) kept
    b = get(client, keys(), text=QUERY, n=20, mode="structured").json()
    assert a["neighbors"] == b["neighbors"] and a["term_keys"] == b["term_keys"]
    c = ss.KeyCache(entries=2)
    for k in "abc":
        c.put(k, {k: 1})
    assert c.get("a") is None and c.get("c") == {"c": 1} and len(c) == 2


def test_a_text_without_known_words_and_the_work_cap(client, keys, env, monkeypatch, phrases_on):
    js = get(client, keys(), text="zzzz qqqq", mode="structured").json()
    assert js["neighbors"] == [] and js["terms"] == [] and [k["key"] for k in js["term_keys"]] == ["zzzz qqqq"]
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 0)              # every claim set over the cap: left out, not an error
    r = get(client, keys(), text=QUERY, mode="structured")
    assert r.status_code == 200 and r.json()["neighbors"] == [] and r.json()["candidates"]["with_claim_text"] == 0


def test_input_keys_and_matching():
    assert ss.input_keys(QUERY) == ["pool cleaner", "drive motor", "filter bag"]
    assert ss.input_keys("Solar pool heaters with a heat exchanger") == ["solar pool heater", "heat exchanger"]
    pk = {"electric drive motor": {}, "motor": {}, "pool cleaner": {}}
    heads = {}
    for k in pk:
        heads.setdefault(k.rsplit(" ", 1)[-1], []).append(k)
    assert ss.match_key("pool cleaner", pk, heads) == ("exact", "pool cleaner")
    assert ss.match_key("drive motor", pk, heads) == ("partial", "electric drive motor")   # the most shared words wins
    assert ss.match_key("brush", pk, heads) is None
    assert ss.match_key("robotic pool cleaner", pk, heads) == ("partial", "pool cleaner")


def test_the_wording_describes_shared_text_only(client, keys, env, phrases_on):
    body = get(client, keys(), text=QUERY, mode="structured").content.decode("utf-8")
    import re

    legal = re.compile(r"\b(valid|invalid|infring\w*|broad\w*|narrow\w*|covers?|anticipat\w*|obvious\w*|prior art|free to use)\b", re.I)
    for text in (ss.__doc__, body):
        assert lapse_wording.check_banned(text) == []
        assert not legal.search(text), legal.search(text).group(0)
        assert "—" not in text
