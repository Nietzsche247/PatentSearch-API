"""Contract tests for GET /api/v1/structure/counts/<patent_id>/ (the structure layer, gate S0.6; API/lapse_structure_counts.py
and API/claim_counts.py).

The same two files as the claim tree's tests (a search file in the Lapse layout and a long-text file in build_text.py's
layout, filled with the real claims of claimgraph's 37 fixtures), built under names of their own. Checks: the answer's shape
and version fields; the counts equal to API/claim_counts.py on every fixture and in step with the claim tree endpoint's own
answer; field names that are counts and a definition with its USPTO variable for each; the word, character, cancellation,
dependency depth, multiple dependency and body reference rules on made-up claims; the errors of the claim tree (404, 400, 501,
403, the work cap's 500) in both forms; the LRU; the wording; and that claimgraph is imported through API/lapse_claimgraph.py
only.

  python -m pytest lapse_accounts/tests/test_structure_counts.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import re
from pathlib import Path

import pytest
from django.conf import settings

from API import claim_counts, lapse_claimgraph, lapse_structure, lapse_structure_counts, lapse_wording
from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP
from lapse_accounts.tests.test_structure_claims import DATA_VERSION, MANIFEST, NO_TEXT, TEXT_VERSION, UNKNOWN, build_files, fixture_rows

ROOT = Path(__file__).resolve().parents[2]
PATH = "/api/v1/structure/counts/"
TREE = "/api/v1/structure/claims/"
# the words the coordinator's brief bars from every structure output, field name and page (plan of record 2026-10-29, section 2)
BANNED = re.compile(r"\b(risk\w*|clear\w*|safe\w*|free to use|invalid\w*|valid|validity|scope\w*|breadth|strength\w*|broad\w*|"
                    r"narrow\w*|infring\w*|covers?|covered)\b", re.I)


@pytest.fixture(scope="module")
def files():
    search, text = str(_TMP / "counts_search.db"), str(_TMP / "counts_text.db")
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
    lapse_structure_counts.CACHE.clear()
    return old, old_text


def _restore(old, old_text):
    settings.LAPSE_SQLITE.clear()
    settings.LAPSE_SQLITE.update(old)
    settings.LAPSE_TEXT_PATH = old_text
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    lapse_structure_counts.CACHE.clear()


@pytest.fixture
def env(files):
    old, old_text = _swap(*files)
    try:
        yield files
    finally:
        _restore(old, old_text)


@pytest.fixture
def no_text_env(files):
    old, old_text = _swap(files[0], str(_TMP / "no_such_text_counts.db"))
    try:
        yield
    finally:
        _restore(old, old_text)


@pytest.fixture
def keys(client, token_factory):
    """Fresh Free keys on demand (5 requests a key in the tests, LAPSE_FREE_MONTHLY_LIMIT), each from its own email domain."""
    import uuid

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


def get(client, key, pid, path=PATH, **headers):
    return client.get(path + pid + "/", HTTP_X_API_KEY=key, **headers)


def rows_of(pid, texts):
    """Made-up txt.g_claims rows: (patent_id, claim_sequence, claim_text, claim_dependent, claim_number, exemplary)."""
    return [(pid, str(i), t, "", str(i + 1), "") for i, t in enumerate(texts)]


# ------------------------------------------------------------------ the answer

def test_shape_and_versions(client, keys, env):
    r = get(client, keys(), "10034028")
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "application/json"
    js = r.json()
    assert list(js) == ["error", "patent_id", "patent_unit_id", "counts_version", "structure_version", "generator", "data_version",
                        "text_data_version", "as_of", "counts", "claim_counts"]
    assert js["error"] is False and js["patent_id"] == "10034028" and js["patent_unit_id"] == "US10034028"
    assert js["counts_version"] == claim_counts.COUNTS_VERSION == "1"
    assert js["structure_version"] == lapse_claimgraph.STRUCTURE_VERSION == "2"
    assert js["generator"] == lapse_claimgraph.GENERATOR == "claimgraph " + lapse_claimgraph.__version__
    assert js["data_version"] == DATA_VERSION == r["X-Data-Version"]
    assert js["text_data_version"] == TEXT_VERSION == r["X-Text-Data-Version"]
    assert js["as_of"] == "2026-10-27"
    assert list(js["counts"]) == list(claim_counts.PATENT_FIELD_NAMES)
    assert [list(c) for c in js["claim_counts"]] == [list(claim_counts.CLAIM_FIELD_NAMES)] * 18
    assert r["Cache-Control"] == "public, max-age=300"
    assert re.fullmatch(r'claimcounts;desc="miss";dur=[0-9.]+', r["Server-Timing"])


def test_known_counts_of_one_patent(client, keys, env):
    """US 10034028, 18 claims: 1, 5 and 17 independent; claim 17 also names another claim in its body, so 16 claims name
    another claim (the USPTO dataset's dependent count) and 15 depend on one in the tree; the deepest chain is 5, 10, 11, 12
    and 13 (or 14), depth 4."""
    c = get(client, keys(), "10034028").json()
    k = c["counts"]
    assert (k["claims"], k["independent_claims"], k["dependent_claims"], k["claims_citing_other_claims"]) == (18, 3, 15, 16)
    assert (k["multiple_dependent_claims"], k["cancelled_claims"], k["dependency_depth_max"]) == (0, 0, 4)
    assert k["words_first_claim"] == c["claim_counts"][0]["words"] == 262
    assert (k["words_independent_min"], k["words_independent_max"], k["words_independent_total"]) == (153, 262, 575)
    assert k["words_independent_mean"] == 191.67 and k["elements_independent_mean"] == 5.33
    six = next(x for x in c["claim_counts"] if x["number"] == 6)
    # "6. The method of claim 5 , wherein the caption stream is associated with the media stream.": the comma the long text
    # sets off is not a word, and it is counted as written in the claim, against the word before it
    assert six["words"] == 15
    assert six["characters"] == len("The method of claim 5, wherein the caption stream is associated with the media stream.")
    assert (six["parent_claims"], six["cited_claims"], six["dependency_depth"], six["elements"], six["wherein_clauses"]) == (1, 1, 1, 0, 1)
    by = {x["number"]: x for x in c["claim_counts"]}
    assert [n for n, x in by.items() if x["parent_claims"] == 0] == [1, 5, 17] and by[17]["cited_claims"] == 1
    assert (by[13]["dependency_depth"], by[14]["dependency_depth"], by[16]["dependency_depth"]) == (4, 4, 2)


def test_counts_equal_the_counting_code_and_the_claim_tree_on_every_fixture(client, keys, env):
    for pid in sorted(MANIFEST):
        r = get(client, keys(), pid)
        assert r.status_code == 200, (pid, r.content)
        js = r.json()
        pc, per, problems = claim_counts.counts_from_rows(pid, fixture_rows(pid))
        assert problems == [] and js["counts"] == json.loads(json.dumps(pc)) and js["claim_counts"] == per, pid
        tree = get(client, keys(), pid, path=TREE).json()
        assert len(tree["claims"]) == len(js["claim_counts"]) == MANIFEST[pid]["claims"], pid
        ind = sum(1 for c in tree["claims"] if c["independent"])
        assert js["counts"]["independent_claims"] + sum(1 for c in js["claim_counts"] if c["cancelled"] and c["parent_claims"] == 0) == ind, pid
        assert [c["number"] for c in tree["claims"]] == [c["number"] for c in js["claim_counts"]], pid
        assert [len(c["depends_on"]) for c in tree["claims"]] == [c["parent_claims"] for c in js["claim_counts"]], pid


def test_trailing_slash_optional_and_prefix_read(client, keys, env):
    r = client.get(PATH + "US3995389", HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json()["patent_id"] == "3995389"
    r = client.get(PATH + "D1107373/", HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json()["counts"]["claims"] == 1


# ------------------------------------------------------------------ the field names and their definitions

def test_every_field_is_a_count_with_a_definition_and_a_uspto_note():
    for fields in (claim_counts.PATENT_FIELDS, claim_counts.CLAIM_FIELDS):
        names = [f[0] for f in fields]
        assert len(names) == len(set(names))
        for name, typ, definition, uspto in fields:
            assert re.fullmatch(r"[a-z][a-z_]*", name) and typ in ("integer", "number"), name
            assert definition.endswith(".") and len(definition) > 20, name
            assert uspto is None or len(uspto) > 4, name
            for text in (name.replace("_", " "), definition, uspto or ""):
                assert not BANNED.search(text), (name, BANNED.search(text).group(0))
                assert lapse_wording.check_banned(text) == [], name
                assert chr(0x2014) not in text and chr(0x2013) not in text, name
    means = [n for n, t, _, _ in claim_counts.PATENT_FIELDS if t == "number"]
    assert means == ["words_independent_mean", "words_dependent_mean", "elements_independent_mean"]
    rows = claim_counts.dictionary_rows()
    assert [r[0] for r in rows[:len(claim_counts.TSV_COLUMNS)]] == list(claim_counts.TSV_COLUMNS)
    assert all(len(r) == 5 and r[1] in ("patent", "claim") for r in rows)


def test_values_are_counts_or_nulls(client, keys, env):
    for pid in ("3995389", "RE42441", "D1107373", "4156107"):
        js = get(client, keys(), pid).json()
        for k, v in js["counts"].items():
            assert v is None or (isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0), (pid, k, v)
        for c in js["claim_counts"]:
            assert all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in c.values()), (pid, c)


# ------------------------------------------------------------------ the rules, on made-up claims

def test_word_and_character_rules():
    assert claim_counts.words_of("a , b ( 3 ) c ; - d--e") == 5   # a, b, 3, c, d--e; ",", "(", ")", ";" and "-" are no words
    assert claim_counts.words_of("") == 0 and claim_counts.words_of("  \n ") == 0
    assert claim_counts.characters_of("claim 1 , wherein ( 3 )  x .") == len("claim 1, wherein (3) x.")
    assert claim_counts.characters_of("  a\n\tb  ") == len("a b")


def test_cancelled_claims_are_left_out():
    pc, per, _ = claim_counts.counts_from_rows("10000001", rows_of("10000001", [
        "1. A widget comprising a base and a top.", "2. (canceled)", "3. The widget of claim 1, wherein the top is red.",
        "4. Cancelled."]))
    assert [c["cancelled"] for c in per] == [0, 1, 0, 1]
    assert (pc["claims"], pc["cancelled_claims"], pc["independent_claims"], pc["dependent_claims"]) == (2, 2, 1, 1)
    assert pc["words"] == per[0]["words"] + per[2]["words"]


def test_dependency_depth_chains_cycles_and_missing_parents():
    pc, per, _ = claim_counts.counts_from_rows("10000002", rows_of("10000002", [
        "1. A method comprising heating a part.", "2. The method of claim 1, wherein the part is steel.",
        "3. The method of claim 2, wherein the steel is hard.", "4. The method of claim 3, wherein the part is a gear.",
        "5. The method of claim 6, wherein the gear is small.", "6. The method of claim 5, wherein the gear is round.",
        "7. The method of claim 99, wherein the part is thin."]))
    depth = {c["number"]: c["dependency_depth"] for c in per}
    assert (depth[1], depth[2], depth[3], depth[4]) == (0, 1, 2, 3)
    assert depth[7] == 1                      # a parent the patent does not have adds nothing
    assert {depth[5], depth[6]} == {1, 2}     # a cycle ends where the walk comes back to a claim on it
    assert pc["dependency_depth_max"] == 3


def test_multiple_dependency_and_a_reference_in_the_body():
    pc, per, _ = claim_counts.counts_from_rows("10000003", rows_of("10000003", [
        "1. A method comprising heating a part.", "2. A method comprising cooling a part.",
        "3. The method of claim 1 or 2, wherein the part is steel.",
        "4. A system comprising a processor configured to perform the method of claim 1."]))
    by = {c["number"]: c for c in per}
    assert by[3]["parent_claims"] == 2 and pc["multiple_dependent_claims"] == 1
    assert by[4]["parent_claims"] == 0 and by[4]["cited_claims"] == 1   # independent in the tree, naming claim 1 in its body
    assert (pc["independent_claims"], pc["dependent_claims"], pc["claims_citing_other_claims"]) == (3, 1, 2)


def test_uspto_word_counts():
    _, per, _ = claim_counts.counts_from_rows("10000004", rows_of("10000004", [
        "1. A compound consisting of A or B, the compound selected from the group consisting of X, Y or Z and/or W.",
        "2. The compound of claim 1, wherein ORANGE or ordinary parts are used."]))
    assert (per[0]["consisting_words"], per[0]["or_words"], per[0]["selected_from_phrases"]) == (2, 3, 1)
    assert (per[1]["consisting_words"], per[1]["or_words"], per[1]["selected_from_phrases"]) == (0, 1, 0)


def test_tsv_line_and_nulls():
    pc, _, _ = claim_counts.counts_from_rows("D1107373", fixture_rows("D1107373"))
    line = claim_counts.tsv_line("D1107373", pc)
    cells = line.rstrip("\n").split("\t")
    assert line.endswith("\n") and len(cells) == len(claim_counts.TSV_COLUMNS) and cells[0] == "D1107373"
    row = dict(zip(claim_counts.TSV_COLUMNS, cells))
    assert row["words_dependent_min"] == "" and row["words_dependent_mean"] == "" and row["words_independent_mean"] == "12.00"


# ------------------------------------------------------------------ errors, in the upstream shape and RFC 9457

def test_404_unknown_patent_and_without_claim_text(client, keys, env):
    r = get(client, keys(), UNKNOWN)
    assert r.status_code == 404 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "NOT_FOUND" and r["X-Status-Reason"].startswith("Unknown patent_id")
    pj = get(client, keys(), UNKNOWN, HTTP_ACCEPT="application/problem+json").json()
    assert pj["code"] == "NOT_FOUND" and pj["status"] == 404 and pj["instance"] == PATH + UNKNOWN + "/"
    r = get(client, keys(), NO_TEXT)
    assert r.status_code == 404 and r["X-Status-Reason"].startswith(f"No claim text for patent_id '{NO_TEXT}'")


def test_400_and_403(client, keys, env):
    r = client.get(PATH + "abc-1/", HTTP_X_API_KEY=keys())
    assert r.status_code == 400 and r.json() == {"error": True} and r["X-Status-Reason-Code"] == "ERR_Q"
    r = client.get(PATH + "3995389/")
    assert r.status_code == 403 and r.json() == client.get(TREE + "3995389/").json()


def test_501_without_a_long_text_file(client, keys, no_text_env):
    r = get(client, keys(), "3995389")
    assert r.status_code == 501 and r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED"


# ------------------------------------------------------------------ computed on read: the LRU and the work cap

def test_cache_hit_is_the_same_bytes(client, keys, env):
    a = get(client, keys(), "4156107")
    b = get(client, keys(), "4156107")
    assert a.status_code == b.status_code == 200 and a.content == b.content
    assert 'desc="miss"' in a["Server-Timing"] and 'desc="hit"' in b["Server-Timing"]
    assert lapse_structure_counts.CACHE.stats()["entries"] >= 1
    assert lapse_structure_counts.CACHE is not lapse_structure.CACHE


def test_work_cap_answers_500_err_es(client, keys, env, monkeypatch):
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 3)
    r = get(client, keys(), "10034028")
    assert r.status_code == 500 and r["X-Status-Reason-Code"] == "ERR_ES" and "work cap" in r["X-Status-Reason"]
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 1000)
    monkeypatch.setattr(lapse_structure, "MAX_TEXT_CHARS", 100)
    r = get(client, keys(), "3995389")
    assert r.status_code == 500 and "characters" in r["X-Status-Reason"]


# ------------------------------------------------------------------ wording and the one claimgraph import

def test_wording_of_what_the_endpoint_writes(client, keys, env):
    reasons = [get(client, keys(), x)["X-Status-Reason"] for x in (UNKNOWN, NO_TEXT)]
    for text in reasons + [lapse_structure_counts.__doc__, claim_counts.__doc__, lapse_claimgraph.__doc__]:
        assert lapse_wording.check_banned(text) == [], text
        assert not BANNED.search(text), BANNED.search(text).group(0)


def test_claimgraph_is_imported_through_one_module():
    shim = (ROOT / "API" / "lapse_claimgraph.py").read_text(encoding="utf-8")
    assert "import claimgraph" in shim
    for f in ("API/claim_counts.py", "API/lapse_structure_counts.py", "lapse_tools/claims_counts_snapshot.py"):
        src = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import claimgraph|from claimgraph\b)", src, re.M), f
        assert "lapse_claimgraph" in src or "claim_counts" in src, f
