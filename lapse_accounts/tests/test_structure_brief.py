"""Contract tests for GET /api/v1/structure/brief/<patent_id>/?max_tokens= (the structure layer, gate S0.5; API/lapse_brief.py).

The files of structure_files.py: claimgraph's 37 real claim sets and one made-up patent, a status catalog, the status
engine from a patentref checkout. Checks: the answer's shape, its fixed order and its version fields; on every patent at
every budget the answer is never over max_tokens by the stated proxy, names its own size, and is a prefix of the answer
for a larger budget, with truncated and omitted saying what was left out; every quote is the claim text at its offsets
(read from the fixture files, not through the brief's code) and every anchor is an id on the record page; the identity
and status sections; the dependent lines and the component keys follow their rules; 400, 404, 500, 501 and 503 in the
upstream shape and RFC 9457, 403 without a key; the cache; the wording of everything PatentRef writes; no model or
network client in the code; claimgraph reached through API/lapse_claimgraph.py only. The bulk checks call the views in
process (no key, no metering; the account routes' per-address rate stays clear); the contract checks go through the
client with a key.

  python -m pytest lapse_accounts/tests/test_structure_brief.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import math
import re
import types
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.test import RequestFactory

from API import lapse_brief, lapse_claimgraph, lapse_group, lapse_permalinks, lapse_structure
from API.lapse_views import RecordPage, StatusView
from API.search import get_searcher
from lapse_accounts.tests import structure_files as sf
from lapse_accounts.tests.conftest import _TMP

ROOT = Path(__file__).resolve().parents[2]
PATH = "/api/v1/structure/brief/"
BUDGETS = (300, 450, 700, 1000, 1500, 2000, 4000, 8000, 32000)
ORDER = ["error", "patent_id", "structure_version", "generator", "data_version", "text_data_version", "as_of", "max_tokens",
         "tokens", "tokenizer", "truncated", "omitted", "page", "identity", "status", "independent_claims", "dependent_claims",
         "component_keys"]
RF = RequestFactory()


@pytest.fixture(scope="module")
def files():
    paths = tuple(str(_TMP / n) for n in ("brief_search.db", "brief_catalog.db", "brief_text.db"))
    sf.build_files(*paths)
    return paths


@pytest.fixture
def env(files):
    with sf.in_service(*files):
        yield files


@pytest.fixture
def no_text_env(files):
    with sf.in_service(files[0], files[1], str(_TMP / "no_such_text.db")):
        yield


@pytest.fixture
def keys(client, token_factory):
    return sf.key_minter(client, token_factory)


def get(client, key, pid, params=None, **headers):
    return client.get(PATH + pid + "/", params or {}, HTTP_X_API_KEY=key, **headers)


def brief(pid, max_tokens=None):
    """The view called in process (no key, no metering): (the response, the decoded answer or None)."""
    q = {} if max_tokens is None else {"max_tokens": str(max_tokens)}
    r = lapse_brief.BriefView().get(RF.get(PATH + pid + "/", q), pk=pid)
    return r, (json.loads(r.content) if r.status_code == 200 else None)


def items(js):
    """The answer's items in the brief's order, as the cut takes them."""
    out = [(s, js[s]) for s in ("identity", "status") if js[s] is not None]
    for c in js["independent_claims"]:
        out.append(("claim", {k: v for k, v in c.items() if k != "elements"}))
        out += [("element", e) for e in c["elements"]]
    out += [("dependent", d) for d in js["dependent_claims"]]
    out += [("key", k) for k in js["component_keys"]]
    return out


def raw_texts(pid):
    """{claim_sequence: claim text} straight from the fixture file."""
    return {int(seq): text for _p, seq, text, _d, _n, _e in sf.fixture_rows(pid)}


def tree(pid):
    t = lapse_permalinks.graph_for(get_searcher(), pid)
    assert t.state == lapse_permalinks.OK, (pid, t.state)
    return t.graph


# ------------------------------------------------------------------ the answer

def test_shape_order_versions_and_headers(client, keys, env):
    r = get(client, keys(), "10034028")
    assert r.status_code == 200, r.content
    assert r["Content-Type"] == "application/json"
    js = r.json()
    assert list(js) == ORDER and list(js)[-5:] == list(lapse_brief.SECTIONS)
    assert js["error"] is False and js["patent_id"] == "10034028" and js["max_tokens"] == lapse_brief.DEFAULT_TOKENS == 2000
    assert js["structure_version"] == lapse_claimgraph.STRUCTURE_VERSION == "2"
    assert js["generator"] == lapse_claimgraph.GENERATOR == lapse_structure.GENERATOR
    assert js["data_version"] == sf.DATA_VERSION == r["X-Data-Version"]
    assert js["text_data_version"] == sf.TEXT_VERSION == r["X-Text-Data-Version"]
    assert js["as_of"] == "2026-10-27" and js["tokenizer"] == lapse_brief.TOKENIZER == "ceil(utf8_bytes / 3)"
    assert js["tokens"] == math.ceil(len(r.content) / 3) <= 2000
    assert js["page"] == "http://testserver/p/10034028?v=20261027.2"
    assert set(js["omitted"]) == {"independent_claims", "elements", "dependent_claims", "component_keys"}
    assert r["Cache-Control"] == "public, max-age=300"
    assert re.fullmatch(r'brief;desc="(hit|miss)";dur=[0-9.]+', r["Server-Timing"])
    c1 = js["independent_claims"][0]
    assert list(c1) == ["number", "anchor", "category", "preamble", "transition", "element_count", "elements"]
    assert c1["number"] == 1 and c1["anchor"] == "c1" and c1["category"] == "method"
    assert list(c1["elements"][0]) == ["anchor", "start", "end", "text"]
    assert c1["elements"][0]["anchor"].startswith("c1-e1-")


def test_trailing_slash_optional_and_prefix_read(client, keys, env):
    r = client.get(PATH + "US10034028", {"max_tokens": "500"}, HTTP_X_API_KEY=keys())
    assert r.status_code == 200 and r.json()["patent_id"] == "10034028" and r.json()["max_tokens"] == 500


def test_never_over_budget_and_always_a_prefix_of_a_larger_budget(env):
    for pid in sf.patent_ids():
        prev = None
        for b in BUDGETS:
            r, js = brief(pid, b)
            assert r.status_code == 200, (pid, b)
            assert list(js) == ORDER and js["max_tokens"] == b
            assert js["tokens"] == lapse_brief.tokens_of(len(r.content)) == math.ceil(len(r.content) / 3), (pid, b)
            assert js["tokens"] <= b, (pid, b, js["tokens"])
            if prev is not None:
                small = items(prev)
                assert items(js)[:len(small)] == small, (pid, b)
            prev = js


def test_truncated_and_omitted_say_what_was_left_out(env):
    for pid in sf.patent_ids():
        _r, whole = brief(pid, 32000)
        assert whole["truncated"] is False and not any(whole["omitted"].values()), pid
        totals = {"independent_claims": len(whole["independent_claims"]),
                  "elements": sum(len(c["elements"]) for c in whole["independent_claims"]),
                  "dependent_claims": len(whole["dependent_claims"]), "component_keys": len(whole["component_keys"])}
        assert totals["elements"] == sum(c["element_count"] for c in whole["independent_claims"])
        for b in BUDGETS:
            _r, js = brief(pid, b)
            got = {"independent_claims": len(js["independent_claims"]), "elements": sum(len(c["elements"]) for c in js["independent_claims"]),
                   "dependent_claims": len(js["dependent_claims"]), "component_keys": len(js["component_keys"])}
            assert js["omitted"] == {k: totals[k] - got[k] for k in totals}, (pid, b)
            assert js["truncated"] == (len(items(js)) < len(items(whole))), (pid, b)
            if not js["truncated"]:
                assert {s: js[s] for s in lapse_brief.SECTIONS} == {s: whole[s] for s in lapse_brief.SECTIONS}


def test_the_smallest_budget_still_names_the_patent_and_the_cut_needs_its_fixed_part(env):
    _r, js = brief("10034028", lapse_brief.MIN_TOKENS)
    assert js["identity"]["patent_id"] == "10034028" and js["truncated"] is True
    prep, _state, err = lapse_brief.prepared_for(get_searcher(), "10034028", RF.get("/"))
    assert err is None
    body, need = lapse_brief.cut(prep, {"patent_id": "10034028", "data_version": "v", "text_data_version": "t", "page": "p"}, 100)
    assert body is None and 100 < need < lapse_brief.MIN_TOKENS


def test_every_quote_is_the_claim_text_at_its_offsets(env):
    n = 0
    for pid in sf.patent_ids():
        _r, js = brief(pid, 32000)
        raw = raw_texts(pid)
        seq = {lapse_permalinks.claim_anchor(c): c["sequence"] for c in tree(pid)["claims"]}

        def holds(anchor, q):
            text = raw[seq[anchor]]
            assert text[q["start"]:q["end"]] == q["text"], (pid, anchor, q)

        for c in js["independent_claims"]:
            for part in (c["preamble"], c["transition"]):
                if part:
                    holds(c["anchor"], part)
                    n += 1
            for e in c["elements"]:
                holds(c["anchor"], e)
                m = lapse_permalinks.ANCHOR_RE.match(e["anchor"])
                assert m and "c" + m.group(1) + ("-s" + m.group(2) if m.group(2) else "") == c["anchor"]
                assert (int(m.group(4)), int(m.group(5))) == (e["start"], e["end"])
                n += 1
        for d in js["dependent_claims"]:
            holds(d["anchor"], d)
            n += 1
    assert n > 500


class _Ids(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        i = dict(attrs).get("id")
        if i:
            self.ids.add(i)


def test_every_anchor_is_an_id_on_the_record_page(env):
    for pid in sf.patent_ids():
        _r, js = brief(pid, 32000)
        page = RecordPage().get(RF.get(f"/p/{pid}"), pk=pid)
        assert page.status_code == 200, pid
        p = _Ids()
        p.feed(page.content.decode("utf-8"))
        anchors = [c["anchor"] for c in js["independent_claims"]] + [e["anchor"] for c in js["independent_claims"] for e in c["elements"]]
        anchors += [d["anchor"] for d in js["dependent_claims"]]
        assert anchors and not [a for a in anchors if a not in p.ids], pid
        assert js["page"].startswith("http://testserver/p/" + pid + "?v=")


def test_identity_and_status_sections(env):
    for pid in ("10034028", "RE42441", "D1107373", sf.MARKUP):
        _r, js = brief(pid, 4000)
        filing, grant = sf.dates(pid)
        g = tree(pid)
        assert js["identity"] == {"patent_id": pid, "patent_title": sf.title(pid), "patent_type": sf.ptype(pid), "patent_date": grant,
                                  "filing_date": filing, "claim_count": len(g["claims"]),
                                  "independent_count": sum(1 for c in g["claims"] if c["independent"])}
        rec = StatusView().get(RF.get(f"/api/v1/lapse/status/{pid}/"), pk=pid).data
        assert js["status"] == {k: rec[k] for k in lapse_brief.STATUS_FIELDS}, pid
        assert "permalink" not in js["status"] and "next_steps" not in js["status"]


def test_dependent_lines(env):
    for pid in sf.patent_ids():
        _r, js = brief(pid, 32000)
        deps = [c for c in tree(pid)["claims"] if not c["independent"]]
        assert [d["anchor"] for d in js["dependent_claims"]] == [lapse_permalinks.claim_anchor(c) for c in deps]
        for line, c in zip(js["dependent_claims"], deps):
            text = c["text"]
            start = c["transition"]["start"] if c["transition"] else c["body_start"]
            while start < len(text) and text[start].isspace():
                start += 1
            assert line["number"] == c["number"] and line["depends_on"] == c["depends_on"] and line["start"] == start
            assert line["text"] == text[line["start"]:line["end"]] and line["text"] == line["text"].strip()
            if line.get("cut"):
                window = text[line["start"]:line["start"] + lapse_brief.LINE_MAX + 1]
                assert line["end"] - line["start"] <= lapse_brief.LINE_MAX and line["end"] < len(text.rstrip())
                assert text[line["end"]].isspace() or " " not in window.strip()
            else:
                assert "cut" not in line and line["end"] == len(text.rstrip())


def test_dependent_line_cut_rule():
    claim = {"text": "2. The tool of claim 1, wherein " + "word " * 80, "transition": {"start": 24, "end": 31, "text": "wherein"},
             "body_start": 3, "number": 2, "depends_on": [1], "claim_id": "US1-c2"}
    line = lapse_brief.dependent_line(claim)
    assert line["cut"] is True and line["start"] == 24 and line["end"] - line["start"] <= lapse_brief.LINE_MAX
    assert line["text"].startswith("wherein word") and line["text"].endswith("word") and claim["text"][line["end"]] == " "


def test_component_keys(env):
    for pid in sf.patent_ids():
        _r, js = brief(pid, 32000)
        g = tree(pid)
        intro = {}
        for c in g["claims"]:
            for t in c["terms"]:
                if t["role"] == "introduces" and t["key"]:
                    intro.setdefault(t["key"], []).append((0 if c["independent"] else 1, c["number"], t["start"]))
        keys = js["component_keys"]
        assert [k["key"] for k in keys] == sorted(intro, key=lambda k: (min(intro[k]), k)), pid
        for k in keys:
            assert k["claims"] == sorted({r[1] for r in intro[k["key"]]}) and list(k) == ["key", "claims"]


def test_the_same_answer_after_a_rebuild(env):
    first = {(pid, b): brief(pid, b)[0].content for pid in ("10034028", "4156107", "RE42441") for b in (700, 32000)}
    sf.clear_caches()
    again = {(pid, b): brief(pid, b)[0].content for pid in ("10034028", "4156107", "RE42441") for b in (700, 32000)}
    assert first == again


# ------------------------------------------------------------------ errors, in the upstream shape and RFC 9457

def test_400_for_a_string_that_is_not_a_patent_number_and_for_max_tokens(client, keys, env):
    r = client.get(PATH + "abc-1/", HTTP_X_API_KEY=keys())
    assert r.status_code == 400 and r.json() == {"error": True} and r["X-Status-Reason-Code"] == "ERR_Q"
    for value, reason in (("x", "'max_tokens' must be an integer"), ("299", "'max_tokens' must be 300 to 32000"),
                          ("32001", "'max_tokens' must be 300 to 32000")):
        r = get(client, keys(), "10034028", {"max_tokens": value})
        assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q" and r["X-Status-Reason"] == reason, value
    pj = get(client, keys(), "10034028", {"max_tokens": "x"}, HTTP_ACCEPT="application/problem+json").json()
    assert pj["code"] == "ERR_Q" and pj["status"] == 400 and pj["type"].endswith("/ERR_Q")
    assert lapse_brief.parse_budget("") == (2000, None) and lapse_brief.parse_budget(None) == (2000, None)
    assert lapse_brief.parse_budget(" 300 ") == (300, None) and lapse_brief.parse_budget("32000") == (32000, None)


def test_404_unknown_patent_and_patent_without_claim_text(client, keys, env):
    r = get(client, keys(), sf.UNKNOWN)
    assert r.status_code == 404 and r.json() == {"error": True} and r["X-Status-Reason-Code"] == "NOT_FOUND"
    assert r["X-Status-Reason"].startswith("Unknown patent_id")
    pj = get(client, keys(), sf.UNKNOWN, HTTP_ACCEPT="application/problem+json").json()
    assert pj["type"] == "https://patentref.io/docs/errors/NOT_FOUND" and pj["status"] == 404 and pj["instance"] == PATH + sf.UNKNOWN + "/"
    r = get(client, keys(), sf.NO_TEXT)
    assert r.status_code == 404 and r["X-Status-Reason"].startswith(f"No claim text for patent_id '{sf.NO_TEXT}'")
    assert sf.TEXT_VERSION in r["X-Status-Reason"]


def test_501_without_a_long_text_file(client, keys, no_text_env):
    r = get(client, keys(), "10034028")
    assert r.status_code == 501 and r.json() == {"error": True} and r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED"


def test_403_without_a_key_as_every_data_path(client, env):
    r = client.get(PATH + "10034028/")
    assert r.status_code == 403
    assert r.json() == client.get("/api/v1/structure/claims/10034028/").json()


def test_work_cap_answers_500_err_es(env, monkeypatch):
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 3)
    r, _js = brief("10034028")
    assert r.status_code == 500 and r["X-Status-Reason-Code"] == "ERR_ES" and "work cap" in r["X-Status-Reason"]


def test_503_when_the_status_engine_is_not_available(env, monkeypatch):
    def down(*a, **k):
        raise RuntimeError("status engine unavailable")

    monkeypatch.setattr(lapse_group, "verdict_with_engine", down)
    r, _js = brief("10034028")
    assert r.status_code == 503 and r["X-Status-Reason-Code"] == "ERR_ES"


def test_a_quote_that_does_not_hold_is_never_served(env, monkeypatch):
    real = lapse_brief.independent_item

    def shifted(claim):
        head, els = real(claim)
        if els:
            els[0] = dict(els[0], start=els[0]["start"] + 1)
        return head, els

    monkeypatch.setattr(lapse_brief, "independent_item", shifted)
    r, _js = brief("10034028")
    assert r.status_code == 500 and "offset check" in r["X-Status-Reason"]


# ------------------------------------------------------------------ computed on read: the cache

def test_prepared_cache_serves_every_budget(client, keys, env):
    a = get(client, keys(), "10073891", {"max_tokens": "700"})
    b = get(client, keys(), "10073891", {"max_tokens": "8000"})
    c = get(client, keys(), "10073891", {"max_tokens": "700"})
    assert a.status_code == b.status_code == c.status_code == 200 and a.content == c.content
    assert 'desc="miss"' in a["Server-Timing"] and 'desc="hit"' in b["Server-Timing"] and 'desc="hit"' in c["Server-Timing"]
    assert lapse_brief.CACHE.stats()["entries"] >= 1


def test_prepared_cache_bounds():
    c = lapse_brief.PreparedCache(entries=3, max_bytes=10, item_max=6)
    for k in "abc":
        c.put(k, types.SimpleNamespace(size=2))
    assert c.stats() == {"entries": 3, "bytes": 6}
    c.get("a")
    c.put("d", types.SimpleNamespace(size=2))
    assert c.get("b") is None and c.get("a") is not None and c.stats()["entries"] == 3
    c.put("e", types.SimpleNamespace(size=6))
    assert c.stats()["bytes"] <= 10 and c.get("e") is not None
    c.put("f", types.SimpleNamespace(size=7))
    assert c.get("f") is None
    assert lapse_brief.CACHE_ENTRIES == 256 and lapse_brief.CACHE_BYTES == 16 * 1024 * 1024


# ------------------------------------------------------------------ wording, the code's reach, the one claimgraph import

def test_wording_of_everything_patentref_writes(client, keys, env):
    for pid in sf.patent_ids():
        _r, js = brief(pid, 32000)
        assert lapse_brief.wording_problems(js) == [], pid
        assert not [k for k in lapse_brief.own_strings(js) if any(b in k.lower() for b in lapse_brief.BANNED)]
    reasons = [get(client, keys(), x)["X-Status-Reason"] for x in (sf.UNKNOWN, sf.NO_TEXT)]
    for text in reasons + [lapse_brief.__doc__, lapse_permalinks.__doc__, lapse_claimgraph.__doc__]:
        low = text.lower()
        assert not [b for b in lapse_brief.BANNED if b in low], text[:80]
    # the patent's own words are left as printed: a quote may hold any word, and the check does not look at it
    assert lapse_brief.wording_problems({"text": "a clear and safe valid scope", "key": "broad risk", "patent_title": "x"}) == []
    assert lapse_brief.wording_problems({"plain": "a clear statement"}) == ["clear"]


def _src(name):
    return (ROOT / "API" / name).read_text(encoding="utf-8")


def test_no_model_or_network_client_in_the_code():
    net = re.compile(r"^\s*(?:import|from)\s+(?:requests|httpx|urllib|http\.client|socket|aiohttp|openai|anthropic)\b", re.M)
    for name in ("lapse_brief.py", "lapse_permalinks.py", "lapse_claimgraph.py"):
        assert not net.search(_src(name)), name


def test_claimgraph_is_reached_through_one_module():
    imp = re.compile(r"^\s*(?:import\s+claimgraph|from\s+claimgraph\b)", re.M)
    assert len(imp.findall(_src("lapse_claimgraph.py"))) == 1
    for name in ("lapse_brief.py", "lapse_permalinks.py"):
        assert not imp.search(_src(name)), name
        assert "from API import lapse_claimgraph as cg" in _src(name)
