"""The claims on the record page /p/<patent_id> and their anchors (the structure layer, gate S0.4; API/lapse_permalinks.py
and lapse_accounts/templates/lapse_accounts/record.html).

The files of structure_files.py: claimgraph's 37 real claim sets and one made-up patent with markup in its claim text, a
status catalog, the status engine from a patentref checkout. Checks: every claim and every element of every patent has its
anchor on the page and the anchor holds exactly that text (the claim text read back from the page, the element text
equal to text[start:end]); the anchors are unique and of the documented form and come out the same after a rebuild; the
page and the claim tree endpoint share one cache; the page's script reads anchors with the module's own pattern; the
page's other states (no claims for the patent, no long-text file, over the work cap, a claim set it cannot lay out);
markup in a claim stays text; the page keeps its existing checks; the wording of what the page adds.

  python -m pytest lapse_accounts/tests/test_record_anchors.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import html
import json
import re
from html.parser import HTMLParser

import pytest
from django.test import RequestFactory

from API import lapse_brief, lapse_claimgraph, lapse_permalinks, lapse_structure, lapse_wording
from API.lapse_structure import ClaimTreeView
from API.lapse_views import RecordPage
from lapse_accounts.tests import structure_files as sf
from lapse_accounts.tests.conftest import _TMP

RF = RequestFactory()


@pytest.fixture(scope="module")
def files():
    paths = tuple(str(_TMP / n) for n in ("anchors_search.db", "anchors_catalog.db", "anchors_text.db"))
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


def page(pid, **q):
    """The record page called in process (no per-address rate): (the response, its HTML)."""
    r = RecordPage().get(RF.get(f"/p/{pid}", q), pk=pid)
    return r, r.content.decode("utf-8")


def expected(pid):
    """The claim tree claimgraph builds from the fixture file, as GET /api/v1/structure/claims/ serves it."""
    rows = [lapse_claimgraph.ClaimRow(*r) for r in sf.fixture_rows(pid)]
    return json.loads(json.dumps(lapse_claimgraph.build_patent(pid, rows)))


class Page(HTMLParser):
    """Each id on the page with its tag and classes; for the ids inside the claims, the claim text read back (.claim-lead
    and .el-line) and the element text (.el-text); and the text of the page's scripts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.ids, self.order, self.scripts, self.own = [], {}, [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        self.stack.append((tag, a))
        if a.get("id"):
            self.ids[a["id"]] = {"tag": tag, "class": a.get("class") or "", "text": [], "el": []}
            self.order.append(a["id"])

    def handle_endtag(self, tag):
        while self.stack:
            t, _a = self.stack.pop()
            if t == tag:
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == "script":
            self.scripts.append(data)
            return
        classes = {c for _t, a in self.stack for c in (a.get("class") or "").split()}
        in_list = any(a.get("id") == "claims-list" for _t, a in self.stack)
        claim_text = bool(classes & {"claim-lead", "el-line"})
        if in_list and not claim_text:
            self.own.append(data)   # what the page writes inside the claims: headings, dependency lines, link labels
        for _t, a in self.stack:
            i = a.get("id")
            if i and i in self.ids:
                if claim_text:
                    self.ids[i]["text"].append(data)
                if "el-text" in classes:
                    self.ids[i]["el"].append(data)


def parse(text):
    p = Page()
    p.feed(text)
    return p


def anchors_of(g):
    out = []
    for c in g["claims"]:
        out.append(lapse_permalinks.claim_anchor(c))
        out += [lapse_permalinks.element_anchor(c, el) for el in sorted(c["elements"], key=lambda e: e["start"])]
    return out


def claims_section(text):
    return text.split('<div class="claims" id="claims-list"', 1)[1].split("\n    </div>", 1)[0]


# ------------------------------------------------------------------ every anchor, with exactly its text

def test_every_claim_and_element_has_its_anchor_and_its_text(env):
    n_claims = n_els = 0
    for pid in sf.patent_ids():
        r, text = page(pid)
        assert r.status_code == 200, pid
        p = parse(text)
        g = expected(pid)
        for c in g["claims"]:
            a = lapse_permalinks.claim_anchor(c)
            assert a in p.ids and p.ids[a]["tag"] == "div" and p.ids[a]["class"] == "claim", (pid, a)
            assert "".join(p.ids[a]["text"]) == c["text"], (pid, a)   # the claim text reads back from the page exactly
            n_claims += 1
            for el in c["elements"]:
                ea = lapse_permalinks.element_anchor(c, el)
                assert ea in p.ids and p.ids[ea]["tag"] == "li", (pid, ea)
                assert "".join(p.ids[ea]["el"]) == el["text"] == c["text"][el["start"]:el["end"]], (pid, ea)
                n_els += 1
        inside = p.order[p.order.index("claims-list") + 1:]
        assert inside == anchors_of(g), pid                            # nothing else in the claims carries an id
        assert len(set(p.order)) == len(p.order), pid                  # every id on the page once
    assert n_claims == sum(m["claims"] for m in sf.MANIFEST.values()) + len(sf.MARKUP_ROWS) and n_els > 1000


def test_anchor_form():
    for pid in sf.patent_ids():
        g = expected(pid)
        for c in g["claims"]:
            a = lapse_permalinks.claim_anchor(c)
            m = lapse_permalinks.ANCHOR_RE.match(a)
            assert m and int(m.group(1)) == c["number"] and m.group(3) is None, (pid, a)
            assert "US" + pid + "-" + a == c["claim_id"]
            for el in c["elements"]:
                ea = lapse_permalinks.element_anchor(c, el)
                m = lapse_permalinks.ANCHOR_RE.match(ea)
                assert m and ea.startswith(a + "-e") and "US" + pid + "-" + ea.rsplit("-", 2)[0] == el["element_id"], (pid, ea)
                assert (int(m.group(4)), int(m.group(5))) == (el["start"], el["end"])


def test_a_repeated_claim_number_gets_its_own_anchor():
    rows = [lapse_claimgraph.ClaimRow("1234567", "0", "1. A cup comprising a handle.", "", "1"),
            lapse_claimgraph.ClaimRow("1234567", "1", "2. The cup of claim 1, wherein the handle is round.", "claim 1", "2"),
            lapse_claimgraph.ClaimRow("1234567", "2", "2. The cup of claim 1, wherein the handle is blue.", "claim 1", "2")]
    g = json.loads(json.dumps(lapse_claimgraph.build_patent("1234567", rows)))
    assert [lapse_permalinks.claim_anchor(c) for c in g["claims"]] == ["c1", "c2", "c2-s2"]
    out = lapse_permalinks.render_claims(g)
    assert 'id="c2-s2"' in out and "Claim 2 (this number appears more than once in the data)" in out
    assert lapse_permalinks.ANCHOR_RE.match("c2-s2").group(2) == "2"


def test_layout_joins_back_and_starts_a_line_at_its_label():
    rows = [lapse_claimgraph.ClaimRow("7654321", "0", "1. A method of frying comprising: a. heating a pan; b. adding oil; and c. frying an egg.", "", "1")]
    g = json.loads(json.dumps(lapse_claimgraph.build_patent("7654321", rows)))
    c = g["claims"][0]
    lead, lines = lapse_permalinks.layout(c)
    assert lead + "".join(ln["head"] + ln["text"] + ln["tail"] for ln in lines) == c["text"]
    for ln, el in zip(lines, sorted(c["elements"], key=lambda e: e["start"])):
        assert ln["text"] == el["text"]
        if el.get("label"):
            assert ln["head"].startswith(el["label"]) and not ln["head"][len(el["label"]):].strip()
    with pytest.raises(ValueError):
        lapse_permalinks.layout(dict(c, elements=[dict(c["elements"][0]), dict(c["elements"][0], element_id=c["claim_id"] + "-e9")]))


def test_anchors_unchanged_across_a_rebuild(env):
    first = {pid: claims_section(page(pid)[1]) for pid in sf.patent_ids()}
    sf.clear_caches()   # a new worker, or a restart: nothing kept
    again = {pid: claims_section(page(pid)[1]) for pid in sf.patent_ids()}
    assert first == again
    for pid in ("10034028", "RE42441", sf.MARKUP):   # claimgraph itself: the same rows, the same anchors
        assert anchors_of(expected(pid)) == anchors_of(expected(pid))


def test_the_page_and_the_claim_tree_share_one_cache(env):
    page("10049105")
    key = ("10049105", sf.DATA_VERSION, sf.TEXT_VERSION, lapse_claimgraph.STRUCTURE_VERSION)
    assert lapse_structure.CACHE.get(key) is not None
    r = ClaimTreeView().get(RF.get("/api/v1/structure/claims/10049105/"), pk="10049105")
    assert r.status_code == 200 and 'desc="hit"' in r["Server-Timing"]
    assert json.loads(r.content)["claims"] == expected("10049105")["claims"]


# ------------------------------------------------------------------ a link to an anchor the page does not have

def test_the_script_reads_anchors_with_the_module_pattern(env):
    _r, text = page("10034028")
    p = parse(text)
    script = "".join(p.scripts)
    m = re.search(r"var form = /(.*?)/;", script)
    assert m and m.group(1) == lapse_permalinks.ANCHOR_PATTERN
    assert 'window.addEventListener("hashchange", land)' in script and "scrollIntoView" in script
    assert "this link names is not on this page now" in script and "The claims start below." in script
    for rx in (r"var claim = document\.getElementById\(\"c\" \+ m\[1\]", r'note\.setAttribute\("role", "status"\)'):
        assert re.search(rx, script), rx


def test_a_moved_anchor_still_names_a_claim_the_page_has(env):
    _r, text = page("10034028")
    p = parse(text)
    g = expected("10034028")
    c = g["claims"][0]
    el = c["elements"][1]
    moved = f"{lapse_permalinks.claim_anchor(c)}-e{lapse_permalinks.element_place(el)}-{el['start'] + 7}-{el['end'] + 7}"
    assert moved not in p.ids
    m = lapse_permalinks.ANCHOR_RE.match(moved)
    assert "c" + m.group(1) in p.ids and "c99" not in p.ids   # the claim it names is on the page; a claim number not in it is not


# ------------------------------------------------------------------ the page's other states

def test_a_patent_without_claim_text(env):
    r, text = page(sf.NO_TEXT)
    assert r.status_code == 200
    assert f"The long-text file in service (text data version {sf.TEXT_VERSION}) has no claims for this patent" in text
    assert 'id="claims-list"' not in text and "<script>" not in text and 'href="#claims"' not in text


def test_no_long_text_file(no_text_env):
    r, text = page("10034028")
    assert r.status_code == 200 and "The claims are not on this page right now: no long-text file is in service." in text
    assert 'id="claims-list"' not in text and "<script>" not in text


def test_over_the_work_cap_and_a_claim_set_the_page_cannot_lay_out(env, monkeypatch):
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 3)
    r, text = page("10034028")
    assert r.status_code == 200 and "The claims of this patent are not on this page right now." in text
    monkeypatch.setattr(lapse_structure, "MAX_CLAIMS", 1000)

    def broken(_claim):
        raise ValueError("elements overlap")

    monkeypatch.setattr(lapse_permalinks, "layout", broken)
    r, text = page("10037710")
    assert r.status_code == 200 and "The claims of this patent are not on this page right now." in text
    assert '<p class="status">' in text   # the record itself is as before


def test_markup_in_a_claim_is_shown_as_text(env):
    r, text = page(sf.MARKUP)
    assert r.status_code == 200
    assert "<script>x()</script>" not in text and "&lt;script&gt;x()&lt;/script&gt;" in text and "<b>mesh</b>" not in text
    p = parse(text)
    c = expected(sf.MARKUP)["claims"][0]
    assert "".join(p.ids["c1"]["text"]) == c["text"]
    assert [lapse_permalinks.element_anchor(c, el) for el in c["elements"]] and all(
        "".join(p.ids[lapse_permalinks.element_anchor(c, el)]["el"]) == el["text"] for el in c["elements"])


# ------------------------------------------------------------------ the page keeps its checks; what the page writes

def test_the_page_keeps_its_checks(client, env):
    r = client.get("/p/10034028")   # through the client once: the headers and the per-address route as before
    text = r.content.decode("utf-8")
    assert r.status_code == 200 and r["Content-Type"] == "text/html; charset=utf-8" and r["Cache-Control"] == "public, max-age=300"
    assert r["X-Data-Version"] == sf.DATA_VERSION
    assert lapse_wording.check_banned(text.split('data-user-content="true"')[0]) == []
    assert '<div class="claims" id="claims-list" data-user-content="true"' in text   # the claim text is the patent's
    pre = re.search(r"^\s*pre \{([^}]*)\}", text, re.M).group(1)                      # the record's JSON block, as before
    assert "white-space: pre-wrap" in pre and "word-break: break-word" in pre
    assert "checklist" not in text.lower()
    css = text.split("<style>", 1)[1].split("</style>", 1)[0]
    assert ".claims { overflow-wrap: anywhere; word-break: break-word; }" in css   # long words wrap at 320 px
    assert re.search(r"\.el-link \{[^}]*min-width: 24px; min-height: 24px;", css)   # a 24 px target (WCAG 2.5.8)
    assert ".claim:target, ol.claim-els li:target { outline: 2px solid var(--focus);" in css
    for href in ("#claims", "/api/v1/structure/claims/10034028/", "/api/v1/structure/brief/10034028/"):
        assert f'href="{href}"' in text, href


def test_wording_of_what_the_page_adds(env):
    texts = []
    for pid in sf.patent_ids():
        _r, text = page(pid)
        p = parse(text)
        texts.append("".join(p.own))
        texts.append("".join(p.scripts))
        texts.append(text.split('<h2 id="claims">', 1)[1].split('<div class="claims"', 1)[0])
    texts.append(page(sf.NO_TEXT)[1].split('<h2 id="claims">', 1)[1].split("</main>", 1)[0])
    texts.append(lapse_permalinks.__doc__)
    for t in texts:
        low = html.unescape(t).lower()
        assert not [b for b in lapse_brief.BANNED if b in low], [b for b in lapse_brief.BANNED if b in low]
        assert lapse_wording.check_banned(t) == []
    assert "Independent claim." in texts[0] and "Depends on claim" in "".join(texts)
