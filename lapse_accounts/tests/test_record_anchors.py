"""The claims on the record page /p/<patent_id> and their anchors (the structure layer, gate S0.4; API/lapse_permalinks.py
and lapse_accounts/templates/lapse_accounts/record.html).

The files of structure_files.py: claimgraph's 37 real claim sets and one made-up patent with markup in its claim text, a
status catalog, the status engine from a patentref checkout. Checks: every claim and every element of every patent has its
anchor on the page and the anchor holds exactly that text (the claim text read back from the page, the element text
equal to text[start:end]); the anchors are unique and of the documented form and come out the same after a rebuild; the
page and the claim tree endpoint share one cache; the page's script reads anchors with the module's own pattern; the
page's other states (no claims for the patent, no long-text file, over the work cap, a claim set it cannot lay out);
markup in a claim stays text; the page keeps its existing checks; the wording of what the page adds. Release AD (the
re-skin of 2026-11-05): the claims as a tree, each dependent claim under the independent claim its chain reaches, in a
collapsed details, so a claim's own text is read without the claims nested in it, and the ids come in the tree's order.

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
        for n, (_t, a) in enumerate(self.stack):
            i = a.get("id")
            deeper = self.stack[n + 1:]
            if any("claim" in (b.get("class") or "").split() for _u, b in deeper):
                continue   # a claim nested in this one (a dependent claim under its independent claim): its own text
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


def anchors_in_tree_order(g):
    """The anchors as the page lays them out: each independent claim and its elements, then its dependent claims and
    theirs, in claim order (lapse_permalinks.tree_of)."""
    out = []
    for top, deps in lapse_permalinks.tree_of(g):
        for c in [top] + [d for d, _depth in deps]:
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
            assert a in p.ids and p.ids[a]["tag"] == "div" and "claim" in p.ids[a]["class"].split(), (pid, a)   # the hooks the verbatim checker reads
            tops = {lapse_permalinks.claim_anchor(t) for t, _d in lapse_permalinks.tree_of(g)}
            assert ("top" in p.ids[a]["class"].split()) == (a in tops), (pid, a)   # a box for each claim at the top of the tree
            assert "".join(p.ids[a]["text"]) == c["text"], (pid, a)   # the claim text reads back from the page exactly
            n_claims += 1
            for el in c["elements"]:
                ea = lapse_permalinks.element_anchor(c, el)
                assert ea in p.ids and p.ids[ea]["tag"] == "li", (pid, ea)
                assert "".join(p.ids[ea]["el"]) == el["text"] == c["text"][el["start"]:el["end"]], (pid, ea)
                n_els += 1
        inside = p.order[p.order.index("claims-list") + 1:p.order.index("claims-printed")]   # the As printed view has no ids
        assert inside == anchors_in_tree_order(g), pid                 # nothing else in the claims carries an id
        assert sorted(inside) == sorted(anchors_of(g)), pid            # every claim and element once, in the tree's order
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
    # the page script is the same on every record page (release AD); it lands no anchor where there is no claims list
    assert '<div class="claims" id="claims-list"' not in text and 'href="#claims"' not in text and 'id="claims-printed"' not in text


def test_no_long_text_file(no_text_env):
    r, text = page("10034028")
    assert r.status_code == 200 and "The claims are not on this page right now: no long-text file is in service." in text
    assert '<div class="claims" id="claims-list"' not in text and 'id="claims-printed"' not in text


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


# ------------------------------------------------------------------ release AD: the record page's re-skin (2026-11-05)

def _cup_tree():
    rows = [lapse_claimgraph.ClaimRow("1234567", "0", "1. A cup comprising: a handle; and a base.", "", "1"),
            lapse_claimgraph.ClaimRow("1234567", "1", "2. The cup of claim 1, wherein the handle is round.", "claim 1", "2"),
            lapse_claimgraph.ClaimRow("1234567", "2", "3. The cup of claim 2, wherein the base is blue.", "claim 2", "3"),
            lapse_claimgraph.ClaimRow("1234567", "3", "4. A method of making a cup, comprising: shaping a handle.", "", "4"),
            lapse_claimgraph.ClaimRow("1234567", "4", "5. The cup of claim 1, wherein the base is wide.", "claim 1", "5"),
            lapse_claimgraph.ClaimRow("1234567", "5", "6. The method of claim 4, further comprising: firing the cup.", "claim 4", "6"),
            lapse_claimgraph.ClaimRow("1234567", "6", "7. The cup of claim 3, wherein the handle is blue.", "claim 3", "7")]
    return json.loads(json.dumps(lapse_claimgraph.build_patent("1234567", rows)))


def test_the_tree_nests_each_dependent_under_its_independent_claim():
    g = _cup_tree()
    tree = lapse_permalinks.tree_of(g)
    assert [(t["number"], [(d["number"], depth) for d, depth in deps]) for t, deps in tree] == [
        (1, [(2, 1), (3, 2), (5, 1), (7, 3)]), (4, [(6, 1)])]
    assert lapse_permalinks.deps_summary(*tree[0]) == "4 dependent claims: 2, 3, 5 and 7 (2, 5 on claim 1; 3 on 2; 7 on 3)"
    assert lapse_permalinks.deps_summary(*tree[1]) == "1 dependent claim: 6 (6 on claim 4)"
    assert lapse_permalinks._numbers_text([2, 3, 4, 5, 6, 7, 8, 9]) == "2 to 9"
    assert lapse_permalinks._numbers_text([2, 3, 5, 6, 7, 21]) == "2, 3, 5 to 7 and 21"
    out = lapse_permalinks.render_claims(g)
    p = parse('<div class="claims" id="claims-list">' + out + '</div><div id="claims-printed"></div>')
    assert p.ids["c1"]["class"] == "claim top" and p.ids["c4"]["class"] == "claim top" and '<p class="claim-dep">Independent claim.</p>' in out
    assert p.ids["c2"]["tag"] == "div" and "d1" in p.ids["c2"]["class"] and "d2" in p.ids["c3"]["class"] and "d3" in p.ids["c7"]["class"]
    assert out.index('id="c7"') < out.index('id="c4"')   # claim 7 sits in claim 1's box, before claim 4's
    assert out.count('<details class="deps"><summary>') == 2 and "<details open" not in out   # collapsed, as the canvas shows
    for c in g["claims"]:
        assert "".join(p.ids[lapse_permalinks.claim_anchor(c)]["text"]) == c["text"]   # each claim's own text, nothing nested
    assert out.count('class="el-copy" type="button" hidden') == len(g["claims"][0]["elements"]) + len(g["claims"][3]["elements"])
    printed = lapse_permalinks.render_printed(g)
    assert printed.count('<p class="cp">') == 7 and "1. A cup comprising: a handle; and a base." in printed and " id=" not in printed


def test_a_chain_to_no_claim_stands_at_the_top():
    rows = [lapse_claimgraph.ClaimRow("7654321", "0", "1. A lid.", "", "1"),
            lapse_claimgraph.ClaimRow("7654321", "1", "2. The lid of claim 9, wherein it is round.", "claim 9", "2")]
    g = json.loads(json.dumps(lapse_claimgraph.build_patent("7654321", rows)))
    tree = lapse_permalinks.tree_of(g)
    assert [t["number"] for t, _d in tree] == [1, 2] and all(not d for _t, d in tree)
    assert '<div class="claim top" id="c2">' in lapse_permalinks.render_claims(g)


def test_the_page_order_and_the_six_points(env):
    """The owner's six points of 2026-11-09 on the fixture pages: identity at the top, the claims above the status, the key note
    beside the API links, the term-end row as the canvas words it, nothing raw above the claims, and the rules that keep
    1200 px and 390 px readable."""
    for pid in ("10034028", "10049105"):
        r, text = page(pid)
        assert r.status_code == 200
        body = text.split("<main", 1)[1]
        order = [body.index(s) for s in ('<h1 class="rec-num">US ' + pid + "</h1>", 'id="claims-list"', '<h2 id="status">',
                                         '<h2 id="citations">', '<details class="rec-json" id="json">')]
        assert order == sorted(order), (pid, order)
        above = body.split('id="claims-list"', 1)[0]
        assert "<pre" not in above and '{"patent_id"' not in above and "&quot;patent_id&quot;" not in above
        rail = body.split('<h2 id="get-data">', 1)[1].split("</section>", 1)[0]
        for path in (f"/api/v1/patent/{pid}/", f"/api/v1/lapse/status/{pid}/", f"/api/v1/structure/claims/{pid}/", f"/api/v1/structure/brief/{pid}/"):
            assert f'<a href="{path}">' in rail, path
        assert 'id="key-note">These four links need a free key' in rail and "they answer 403" in rail
        assert '<a href="/api/v1/meta/signup/">Get a free key</a>' in rail
        status = body.split('<h2 id="status">', 1)[1].split("</section>", 1)[0]
        assert "<dt>Term end</dt>" in status and "Computed from the dates in the USPTO record" in status
        css = text.split("<style>", 1)[1].split("</style>", 1)[0]
        assert ".pr-header, .pr-main, .pr-footer { max-width: 1200px; }" in css and "@media (max-width: 719px)" in css
        assert "@media (min-width: 1100px)" in css and "grid-template-columns: minmax(0, 860px) 280px;" in css
        assert r["Content-Security-Policy"].startswith("default-src 'none'; script-src 'sha256-")


def test_the_page_words_pass_the_release_word_list(env):
    """The page's own words (lapse_record.own_text: everything but the patent's text) use none of the release's barred words
    and no em-dash; the view runs check_banned on the same text."""
    from API import lapse_record

    bar = re.compile(r"\b(risk\w*|clear\w*|safe\w*|free to use|invalid\w*|valid\w*|scope\w*|breadth\w*|strength\w*|broad\w*|"
                     r"narrow\w*|infring\w*|no other|first)\b", re.I)
    for pid in list(sf.patent_ids())[:6] + [sf.NO_TEXT]:
        _r, text = page(pid)
        own = html.unescape(lapse_record.own_text(text))
        assert not bar.search(own), (pid, bar.search(own).group(0))
        assert chr(0x2014) not in own and lapse_wording.check_banned(own) == []
        script = "".join(parse(text).scripts)
        assert not bar.search(script) and chr(0x2014) not in script
