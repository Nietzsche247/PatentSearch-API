"""The structure layer's wording guard (release Z; the plan of record of 2026-10-29, section 2; independent audit 13,
finding F): no structure answer, field name, error text or page the fork serves uses one of the structure layer's barred
words (API/lapse_wording.STRUCTURE_BANNED: risk, clear, safe, free to use, invalid, valid, scope, breadth, strength, broad,
narrow, infringe and the words that say what a claim covers) in PatentRef's own words. The patent's own words are left out:
the claim text and every span of it, titles, term keys, transition phrases, labels and the other quotes, the words of the
request, and on the pages the parts marked data-user-content.

What it reads: every route under /api/v1/structure/ (the claim tree, the brief, the claim counts, the triage table as JSON
and as CSV, the extraction trial's routes while it is dark), mode=structured on /api/v1/lapse/similar, each with its 400,
404 and 501 in both error forms and its 403 without a key; the record page with its claims, the extraction page while it is
dark, /triage/ and /triage/example/. A test also reads the URLconf, so a new structure route without a guard fails here.

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_accounts/tests/test_structure_wording.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import csv
import io
import json
import re
from html.parser import HTMLParser

import pytest
from django.conf import settings

from API import lapse_extract, lapse_triage, lapse_wording
from lapse_accounts.tests import structure_files as sf
from lapse_accounts.tests import test_similar_structured as tss
from lapse_accounts.tests.conftest import _TMP

PID = "10034028"
# keys whose values are the patent's words, the request's words or claimgraph's keys (quotes: left out of the check)
QUOTED = frozenset(lapse_triage.QUOTED_FIELDS) | {"aliases", "phrase", "literal", "dependent_field", "depends_on_text", "determiner",
                                                  "patent_title", "key_phrases", "claim_key", "term", "claim_tree_term"}
CSV_QUOTED = {"title", "component", "label", "quote"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
JS_STRING = re.compile(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`")


def barred(where, text, found):
    for w in lapse_wording.structure_words(text):
        found.append((where, w, (text or "")[:120]))


def walk(obj, where, found, quoted=False):
    if isinstance(obj, dict):
        for k, v in obj.items():
            barred(f"{where} field name", k, found)
            walk(v, f"{where}.{k}", found, quoted or k in QUOTED)
    elif isinstance(obj, list):
        for v in obj:
            walk(v, where, found, quoted)
    elif isinstance(obj, str) and not quoted:
        barred(where, obj, found)


class Page(HTMLParser):
    """A page's own words: its text outside data-user-content, outside script and style; its titles, labels and the
    description; the string literals of its own script; and the JSON it embeds, read with the quoted keys left out."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.skip_at, self.text, self.blocks, self.json_blocks = [], None, [], [], []
        self.in_script = self.in_style = False
        self.script_json = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag not in VOID:
            self.stack.append(tag)
        if a.get("data-user-content") == "true" and self.skip_at is None and tag not in VOID:
            self.skip_at = len(self.stack)
        for k in ("title", "aria-label", "placeholder", "alt"):
            if a.get(k) and self.skip_at is None:
                self.text.append(a[k])
        if tag == "meta" and a.get("name") == "description":
            self.text.append(a.get("content") or "")
        if tag == "script":
            self.in_script, self.script_json = True, (a.get("type") == "application/json")
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.skip_at is not None and len(self.stack) == self.skip_at:
            self.skip_at = None
        if self.stack:
            self.stack.pop()
        if tag == "script":
            self.in_script = False
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            return
        if self.in_script:
            (self.json_blocks if self.script_json else self.blocks).append(data)
            return
        if self.skip_at is None:
            self.text.append(data)


def page_words(where, html, found):
    p = Page()
    p.feed(html)
    barred(where, " ".join(p.text), found)
    for js in p.blocks:
        for s in JS_STRING.findall(js):
            barred(f"{where} script string", s, found)
    for block in p.json_blocks:
        walk(json.loads(block), f"{where} embedded JSON", found)


def error_words(where, r, found):
    barred(f"{where} X-Status-Reason", r.get("X-Status-Reason", ""), found)
    if r.get("Content-Type", "").startswith("application/problem+json"):
        walk(r.json(), f"{where} problem details", found)
    elif r.get("Content-Type", "").startswith("application/json"):
        walk(r.json(), f"{where} body", found)


@pytest.fixture(scope="module")
def files():
    paths = tuple(str(_TMP / n) for n in ("wording_search.db", "wording_catalog.db", "wording_text.db"))
    sf.build_files(*paths)
    sim = (str(_TMP / "wording_sim_search.db"), str(_TMP / "wording_sim_text.db"))
    tss.build_files(*sim)
    return paths, sim


@pytest.fixture
def env(files, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_EXTRACT_DIR", str(tmp_path / "extract"), raising=False)       # dark: nothing there
    monkeypatch.setattr(settings, "LAPSE_EXTRACT_KEY_FILE", str(tmp_path / "extract.env"), raising=False)
    lapse_extract._local.__dict__.clear()
    lapse_triage.FACTS.clear()
    with sf.in_service(*files[0]):
        yield files


@pytest.fixture
def keys(client, token_factory):
    return sf.key_minter(client, token_factory)


def ask(client, key, method, path, data=None, **headers):
    if key:
        headers["HTTP_X_API_KEY"] = key
    if method == "POST":
        return client.post(path, data=json.dumps(data or {}), content_type="application/json", **headers)
    return client.get(path, data or {}, **headers)


ROUTES = {"structure-claims", "structure-brief", "structure-counts", "structure-triage", "structure-extract-job", "structure-extract"}


def test_every_structure_route_has_its_guard():
    from lapse_accounts import root_urls

    names = {p.name for p in root_urls.urlpatterns if str(getattr(p, "pattern", "")).startswith("^api/v1/structure/")}
    assert names == ROUTES, names


def test_the_answers_and_their_field_names(client, keys, env):
    found = []
    for path, params in ((f"/api/v1/structure/claims/{PID}/", None), (f"/api/v1/structure/brief/{PID}/", {"max_tokens": "32000"}),
                         (f"/api/v1/structure/brief/{PID}/", {"max_tokens": "300"}), (f"/api/v1/structure/counts/{PID}/", None),
                         ("/api/v1/structure/triage/", {"patents": f"{PID}, 3995389, {sf.NO_TEXT}, {sf.UNKNOWN}",
                                                        "components": "caption stream, guide means"})):
        r = ask(client, keys(), "GET", path, params)
        assert r.status_code == 200, (path, r.status_code, r.get("X-Status-Reason"))
        walk(r.json(), path, found)
    r = ask(client, keys(), "POST", "/api/v1/structure/triage/", {"patents": [PID, "3995389"]})
    assert r.status_code == 200
    walk(r.json(), "POST /api/v1/structure/triage/", found)
    js = r.json()
    assert js["structure_version"] == "2" and js["generator"] == "claimgraph 0.2.0"
    r = ask(client, keys(), "POST", "/api/v1/structure/triage/", {"patents": [PID, "3995389"], "output": "csv"})
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8"))))
    assert rows
    barred("triage CSV header", " ".join(rows[0].keys()), found)
    for row in rows:
        barred("triage CSV", " ".join(v or "" for k, v in row.items() if k not in CSV_QUOTED), found)
    # no structure answer carries the old field name of the term mentions, and the trees name structure version 2
    tree = ask(client, keys(), "GET", f"/api/v1/structure/claims/{PID}/").json()
    terms = [t for c in tree["claims"] for t in c["terms"]]
    assert all("scope" not in t for t in terms) and {t["found_in"] for t in terms if t.get("antecedent")} <= {
        "same_claim", "dependency_chain", "cited_claim"}
    assert tree["structure_version"] == "2" and tree["generator"] == "claimgraph 0.2.0"
    assert found == [], found[:5]


def test_the_structured_mode_of_lapse_similar(client, keys, files):
    found = []
    old, old_text = tss._swap(*files[1])
    try:
        r = ask(client, keys(), "GET", "/api/v1/lapse/similar", {"text": tss.QUERY, "n": "5", "mode": "structured"})
        assert r.status_code == 200, r.get("X-Status-Reason")
        walk(r.json(), "lapse/similar mode=structured", found)
        assert r.json()["structure_version"] == "2"
        for params in ({"text": tss.QUERY, "mode": "claims"},):
            for accept in ("application/json", "application/problem+json"):
                e = ask(client, keys(), "GET", "/api/v1/lapse/similar", params, HTTP_ACCEPT=accept)
                assert e.status_code == 400
                error_words(f"lapse/similar 400 {accept}", e, found)
    finally:
        tss._restore(old, old_text)
    assert found == [], found


def test_the_error_texts(client, keys, env):
    found, seen = [], set()
    cases = []
    for base in ("claims", "brief", "counts"):
        for pid, want in (("abc", 400), (sf.UNKNOWN, 404), (sf.NO_TEXT, 404)):
            cases.append(("GET", f"/api/v1/structure/{base}/{pid}/", None, want))
    cases += [("GET", f"/api/v1/structure/brief/{PID}/", {"max_tokens": "12"}, 400),
              ("GET", "/api/v1/structure/triage/", None, 400),
              ("POST", "/api/v1/structure/triage/", {"patents": [PID], "components": [f"part{i}" for i in range(13)]}, 400),
              ("POST", f"/api/v1/structure/extract/{PID}/", None, 501),
              ("POST", "/api/v1/structure/extract/abc/", None, 400),
              ("GET", f"/api/v1/structure/extract/{PID}/", None, 404),
              ("GET", "/api/v1/structure/extract/jobs/nope/", None, 400),
              ("GET", "/api/v1/structure/extract/jobs/j00000000000000000000/", None, 404)]
    for method, path, data, want in cases:
        for accept in ("application/json", "application/problem+json"):
            r = ask(client, keys(), method, path, data, HTTP_ACCEPT=accept)
            assert r.status_code == want, (method, path, accept, r.status_code, r.get("X-Status-Reason"))
            error_words(f"{method} {path} {accept}", r, found)
            seen.add(r.get("X-Status-Reason", ""))
    for base in ("claims", "brief", "counts"):
        r = ask(client, None, "GET", f"/api/v1/structure/{base}/{PID}/", HTTP_ACCEPT="application/problem+json")
        assert r.status_code == 403
        error_words(f"keyless {base}", r, found)
    assert "Not a patent number: 'abc'" in seen and not [s for s in seen if s.startswith("Invalid")]
    assert "The on-demand extraction trial is not enabled on this server." in seen
    assert found == [], found[:5]


def test_the_errors_without_a_long_text_file(client, keys, files, monkeypatch):
    found = []
    with sf.in_service(files[0][0], files[0][1], str(_TMP / "wording_no_text.db")):
        for path in (f"/api/v1/structure/claims/{PID}/", f"/api/v1/structure/brief/{PID}/", f"/api/v1/structure/counts/{PID}/"):
            for accept in ("application/json", "application/problem+json"):
                r = ask(client, keys(), "GET", path, HTTP_ACCEPT=accept)
                assert r.status_code == 501, (path, r.status_code)
                error_words(f"{path} 501", r, found)
    assert found == [], found


def test_the_pages(client, env):
    found = []
    for path, want in ((f"/p/{PID}", 200), (f"/p/{sf.MARKUP}", 200), (f"/p/{PID}/extracted/", 404), ("/triage/", 200),
                       ("/triage/example/", (200, 503))):
        r = client.get(path, HTTP_ACCEPT="text/html")
        assert r.status_code == want or (isinstance(want, tuple) and r.status_code in want), (path, r.status_code)
        page_words(path, r.content.decode("utf-8"), found)
    rec = client.get(f"/p/{PID}", HTTP_ACCEPT="text/html").content.decode("utf-8")
    assert '<h2 id="claims">Claims</h2>' in rec and "(structure version 2)" in rec
    assert found == [], found[:5]


def test_the_guard_reads_its_words():
    for w in ("risk", "risky", "clear", "clearly", "safe", "free to use", "invalid", "valid", "validity", "scope", "breadth",
              "strength", "broad", "narrow", "infringe", "infringed", "covers", "covered", "reads on", "freedom to operate"):
        assert lapse_wording.structure_words(f"a {w} word"), w
    for w in ("same_claim", "dependency_chain", "cited_claim", "found_in", "unmatched_reference", "nuclear", "unsafe-inline",
              "discover", "asterisk", "microscope"):
        assert not lapse_wording.structure_words(w), w
    assert lapse_wording.structure_words("antecedent_scope") == ["scope"]
