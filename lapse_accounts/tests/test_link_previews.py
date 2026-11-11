"""Release AE: link previews and the home page's structured data (lapse_accounts/previews.py, templates/lapse_accounts/_preview.html).

The home page, /agents/, the record pages and the pages this app serves under /docs/ (the legal documents, the error index)
carry the Open Graph and Twitter card tags, with our own text and a static 1200 by 630 card under https://patentref.io/og/;
a record page names its own claim tree card when the patentref clone has one (public/site/og/p/<id>.png), else the record
card; its description is a fixed line, never the abstract and never a status. The home page carries a schema.org WebAPI
block, checked here against the schema.org vocabulary (fixtures/schemaorg_30.1_subset.json, made from schema.org's
schemaorg-all-https.jsonld of version 30.1). Nothing else is added to a page: a page with its preview taken out is byte for
byte the page as it was, no script runs that did not run before, and every address the tags name is on patentref.io.

  python -m pytest lapse_accounts/tests/test_link_previews.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory

from API.lapse_views import RecordPage
from lapse_accounts import demo, previews, public, site_pages
from lapse_accounts.tests import structure_files as sf
from lapse_accounts.tests.conftest import _TMP

RF = RequestFactory()
HERE = Path(__file__).resolve().parent
VOCAB = json.loads((HERE / "fixtures" / "schemaorg_30.1_subset.json").read_text(encoding="utf-8"))
BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
OG = ("og:type", "og:site_name", "og:title", "og:description", "og:url", "og:image", "og:image:type", "og:image:width",
      "og:image:height", "og:image:alt")
TW = ("twitter:card", "twitter:title", "twitter:description", "twitter:image", "twitter:image:alt")
# the words barred from release AE's copy, whole words in any case with their endings (as release AD's list)
AE_WORDS = re.compile(r"\b(risk\w*|clear\w*|safe\w*|free to use|invalid\w*|valid\w*|scope\w*|breadth\w*|strength\w*|broad\w*|"
                      r"narrow\w*|infring\w*|no other|first)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
STATUS_WORDS = re.compile(r"\b(active|expired|lapsed?|lapse|reinstat\w*|term end|in force|not yet passed|has passed|withdrawn|"
                          r"unknown|fee|nonpayment)\b", re.I)


class Head(HTMLParser):
    """The meta tags of a page's head as (key, content) in order, its JSON-LD blocks, every script element's attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.ld, self.scripts, self._in_ld = [], [], [], False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta" and (a.get("property") or a.get("name")):
            self.meta.append((a.get("property") or a.get("name"), a.get("content")))
        if tag == "script":
            self.scripts.append(a)
            self._in_ld = a.get("type") == "application/ld+json"
            if self._in_ld:
                self.ld.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_ld = False

    def handle_data(self, data):
        if self._in_ld:
            self.ld[-1] += data


def head(h):
    p = Head()
    p.feed(h)
    p.close()
    return p


def preview_tags(h):
    """The page's og: and twitter: tags as a dict; each named once."""
    tags = [(k, v) for k, v in head(h).meta if k.startswith(("og:", "twitter:"))]
    keys = [k for k, _ in tags]
    assert len(keys) == len(set(keys)), keys
    return dict(tags)


def check_preview(h, url, title=None):
    t = preview_tags(h)
    assert set(t) == set(OG + TW), sorted(set(OG + TW) ^ set(t))
    assert t["og:site_name"] == "PatentRef" and t["og:url"] == url and t["og:type"] in ("website", "article")
    assert t["og:image"].startswith("https://patentref.io/og/") and t["og:image"].endswith(".png") and t["og:image:type"] == "image/png"
    assert (t["og:image:width"], t["og:image:height"]) == ("1200", "630")
    assert t["twitter:card"] == "summary_large_image"
    assert (t["twitter:title"], t["twitter:description"], t["twitter:image"], t["twitter:image:alt"]) == (
        t["og:title"], t["og:description"], t["og:image"], t["og:image:alt"])
    if title is not None:
        assert t["og:title"] == title
    words(t)
    return t


def words(tags):
    for k, v in tags.items():
        assert v and v == v.strip(), (k, v)
        assert not AE_WORDS.search(v), (k, v, AE_WORDS.search(v))
        assert chr(0x2014) not in v and chr(0x2013) not in v and not EMOJI.search(v), (k, v)


def meta_description(h):
    return dict(head(h).meta).get("description")


def without_preview(h):
    """The page with the preview's lines taken out (base.html writes them as one block right after the page's own head)."""
    out = re.sub(r"\r?\n  <meta property=\"og:type\".*?<meta name=\"twitter:image:alt\" content=\"[^\"]*\">", "", h, count=1, flags=re.S)
    assert out != h
    return out


# ---------------------------------------------------------------- the schema.org check

DATATYPES = {"Text", "URL", "Date", "DateTime", "Time", "Number", "Integer", "Float", "Boolean"}
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}$")
ISO_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})?$")
URL = re.compile(r"https?://[^\s\"<>]+$")


def ancestors(c):
    out, todo = set(), [c]
    while todo:
        x = todo.pop()
        if x not in out:
            out.add(x)
            todo += VOCAB["classes"].get(x, [])
    return out


def schema_errors(node, where="$"):
    """What in one JSON-LD node schema.org does not allow: an unknown type or property, a property outside its type's
    domain, a value of a kind its range does not take (a nested node of another type, a non-boolean where only Boolean
    goes, a date that is not ISO 8601, a non-URL where URL is the only text form). Text where a Thing is expected is
    allowed, as schema.org's data model allows it."""
    errs = []
    t = node.get("@type")
    if not isinstance(t, str) or t not in VOCAB["classes"] or t in DATATYPES:
        return [f"{where}: @type {t!r} is not a schema.org class"]
    up = ancestors(t)
    for k, v in node.items():
        if k in ("@context", "@type", "@id"):
            continue
        p = VOCAB["properties"].get(k)
        if p is None:
            errs.append(f"{where}.{k}: not a property of schema.org's {t} (or not in the subset)")
            continue
        if not up & set(p["domain"]):
            errs.append(f"{where}.{k}: its domain {p['domain']} does not take {t}")
        rng = set(p["range"])
        for i, item in enumerate(v if isinstance(v, list) else [v]):
            at = f"{where}.{k}[{i}]" if isinstance(v, list) else f"{where}.{k}"
            if isinstance(item, dict):
                it = item.get("@type")
                if not (isinstance(it, str) and ancestors(it) & rng):
                    errs.append(f"{at}: a {it} where {sorted(rng)} is expected")
                errs += schema_errors(item, at)
            elif isinstance(item, bool):
                if "Boolean" not in rng:
                    errs.append(f"{at}: a boolean where {sorted(rng)} is expected")
            elif isinstance(item, (int, float)):
                if not rng & {"Number", "Integer", "Float"}:
                    errs.append(f"{at}: a number where {sorted(rng)} is expected")
            elif isinstance(item, str):
                text_ok = rng & {"Text", "URL", "Date", "DateTime", "Time"}
                if not text_ok and not any("Thing" in ancestors(r) for r in rng if r not in DATATYPES):
                    errs.append(f"{at}: text where {sorted(rng)} is expected")
                elif text_ok and "Text" not in rng:
                    if rng & {"Date", "DateTime"} and "URL" not in rng and not (ISO_DATE.match(item) or ISO_DATETIME.match(item)):
                        errs.append(f"{at}: {item!r} is not an ISO 8601 date")
                    if "URL" in rng and not rng & {"Date", "DateTime"} and not URL.match(item):
                        errs.append(f"{at}: {item!r} is not a URL")
            else:
                errs.append(f"{at}: a {type(item).__name__}")
    return errs


def test_the_schema_check_finds_what_it_should():
    good = {"@type": "WebAPI", "name": "x", "documentation": "https://patentref.io/docs/", "provider": {"@type": "Person", "name": "A"}}
    assert schema_errors(good) == []
    assert schema_errors({"@type": "WebApi", "name": "x"})                                    # no such class
    assert schema_errors({"@type": "WebAPI", "nam": "x"})                                     # no such property
    assert schema_errors({"@type": "WebAPI", "temporalCoverage": "1976/.."})                  # a CreativeWork property
    assert schema_errors({"@type": "WebAPI", "provider": {"@type": "Place", "name": "x"}})    # not a Person or Organization
    assert schema_errors({"@type": "WebAPI", "documentation": "the docs"})                    # CreativeWork or URL: text is no URL
    assert schema_errors({"@type": "Dataset", "datePublished": "24 October 2026"})            # not ISO 8601
    assert schema_errors({"@type": "Dataset", "isAccessibleForFree": "yes"})                  # Boolean only
    assert schema_errors({"@type": "Dataset", "isAccessibleForFree": True, "temporalCoverage": "1976/.."}) == []
    assert schema_errors({"@type": "DataDownload", "contentUrl": "file name"})                # URL only
    assert VOCAB["version"] == "30.1" and "WebAPI" in VOCAB["classes"] and "documentation" in VOCAB["properties"]


# ---------------------------------------------------------------- the home page

def test_the_home_page_has_its_preview_and_its_webapi_block(client):
    r = client.get("/", HTTP_ACCEPT=BROWSER)
    h = r.content.decode("utf-8")
    t = check_preview(h, "https://patentref.io/", previews.HOME_TITLE)
    assert t["og:type"] == "website" and t["og:image"] == "https://patentref.io/og/site.png" and t["og:image:alt"] == previews.SITE_ALT
    assert t["og:description"] == meta_description(h) == previews.HOME_DESCRIPTION
    p = head(h)
    assert len(p.ld) == 1 and [s.get("type") for s in p.scripts] == ["application/ld+json"] and not any(s.get("src") for s in p.scripts)
    ld = json.loads(p.ld[0])
    assert ld == previews.webapi() and schema_errors(ld) == [], schema_errors(ld)
    assert ld["@context"] == "https://schema.org" and ld["@type"] == "WebAPI" and ld["name"] == "PatentRef"
    assert ld["description"] == meta_description(h) and ld["url"] == "https://patentref.io/"
    assert ld["documentation"] == "https://patentref.io/docs/" and ld["termsOfService"] == "https://patentref.io/docs/legal/terms"
    assert 'href="/docs/legal/terms"' in h and 'href="/docs/"' in h     # the terms and the docs, as the page links them
    assert ld["provider"]["@type"] == "Person" and ld["provider"]["name"] == "Aaron Baker"   # the Operator the terms name
    assert ld["provider"]["affiliation"] == {"@type": "Organization", "name": "PatentRef", "url": "https://patentref.io/"}
    words({k: v for k, v in ld.items() if isinstance(v, str)})


def test_the_home_page_is_the_page_it_was_with_the_preview_and_the_block_taken_out(client):
    h = client.get("/", HTTP_ACCEPT=BROWSER).content.decode("utf-8")
    old = render_to_string("lapse_accounts/home.html", demo._ctx())     # the page without release AE's two additions
    assert "og:" not in old and "ld+json" not in old
    cut = re.sub(r"\r?\n  <script type=\"application/ld\+json\">.*?</script>", "", without_preview(h), count=1, flags=re.S)
    assert cut == old


def test_the_ld_json_cannot_end_its_block():
    s = str(previews.ld_json({"a": "</script><script>x()</script> & more", "b": "PatentRef"}))
    assert "<" not in s and ">" not in s and "&" not in s and json.loads(s)["a"] == "</script><script>x()</script> & more"


# ---------------------------------------------------------------- /agents/

def test_the_agents_page_has_its_preview_and_keeps_its_policy(client):
    r = client.get("/agents/")
    h = r.content.decode("utf-8")
    t = check_preview(h, "https://patentref.io/agents/", previews.AGENTS_TITLE)
    assert t["og:description"] == meta_description(h) == previews.AGENTS_DESCRIPTION and t["og:image"] == previews.SITE_CARD
    csp = r["Content-Security-Policy"]
    assert csp.count("'sha256-") == 2 and "img-src 'self' data:" in csp and "default-src 'none'" in csp   # its one script, one style
    assert [s.get("type") for s in head(h).scripts] == [None] and not head(h).ld
    ctx = site_pages.agents_context()
    ctx.pop("preview")
    assert without_preview(h) == render_to_string("lapse_accounts/agents.html", ctx)


# ---------------------------------------------------------------- the record pages

@pytest.fixture(scope="module")
def files():
    paths = tuple(str(_TMP / n) for n in ("previews_search.db", "previews_catalog.db", "previews_text.db"))
    sf.build_files(*paths)
    return paths


@pytest.fixture
def env(files):
    with sf.in_service(*files):
        yield files


def record(pid):
    r = RecordPage().get(RF.get(f"/p/{pid}"), pk=pid)
    return r, r.content.decode("utf-8")


def test_a_record_page_has_the_record_card_and_our_text(env, monkeypatch):
    pid = sf.patent_ids()[0]
    monkeypatch.setattr(previews, "card_ids", lambda repo=None: frozenset({"99999999"}))
    r, h = record(pid)
    assert r.status_code == 200
    t = check_preview(h, f"https://patentref.io/p/{pid}", f"US {pid}: {sf.title(pid)}")
    assert t["og:type"] == "article" and t["og:description"] == previews.RECORD_LINE
    assert t["og:image"] == "https://patentref.io/og/record.png" and t["og:image:alt"] == previews.RECORD_ALT
    # never a status, never the abstract or a claim: the description and the alt are fixed, the title is the number and title
    for k in ("og:description", "og:image:alt", "og:title"):
        assert not STATUS_WORDS.search(t[k]), (k, t[k])
    assert "data-user-content" not in t["og:description"] and sf.fixture_rows(pid)[0][2][:40] not in "".join(t.values())


def test_a_showcase_record_page_names_its_own_claim_tree_card(env, monkeypatch):
    pid = sf.patent_ids()[1]
    monkeypatch.setattr(previews, "card_ids", lambda repo=None: frozenset({pid}))
    r, h = record(pid)
    t = check_preview(h, f"https://patentref.io/p/{pid}")
    assert t["og:image"] == f"https://patentref.io/og/p/{pid}.png"
    assert t["og:image:alt"] == f"The claim tree of US {pid} as its PatentRef record page shows it"


def test_a_record_title_is_escaped_and_the_page_is_otherwise_unchanged(env, monkeypatch):
    monkeypatch.setattr(previews, "card_ids", lambda repo=None: frozenset())
    r, h = record(sf.MARKUP)
    t = preview_tags(h)
    assert t["og:title"] == 'US 11111111: A filter & a "seal" <b>made up</b>'          # the parser reads the escaped attribute back
    assert 'content="US 11111111: A filter &amp; a &quot;seal&quot; &lt;b&gt;made up&lt;/b&gt;"' in h and "<b>made up</b>\">" not in h
    # the policy is computed from the page: the preview adds no script or style, so it allows what it allowed before
    monkeypatch.setattr(previews, "record_page", lambda pid, title, repo=None: None)
    r0, h0 = record(sf.MARKUP)
    assert "og:" not in h0 and without_preview(h) == h0 and r["Content-Security-Policy"] == r0["Content-Security-Policy"]


def test_the_cards_are_read_from_the_clones_folder(tmp_path):
    d = tmp_path / "public" / "site" / "og" / "p"
    d.mkdir(parents=True)
    for n in ("12514139.png", "D366608.png", "notes.txt", "12514139.png.bak", "a b.png"):
        (d / n).write_bytes(b"x")
    previews._cards.clear()
    assert previews.card_ids(str(tmp_path)) == {"12514139", "D366608"}
    (d / "12618241.png").write_bytes(b"x")
    assert previews.card_ids(str(tmp_path)) == {"12514139", "D366608"}            # read once per process and folder
    assert previews.card_ids(str(tmp_path / "missing")) == frozenset()
    previews._cards.clear()
    p = previews.record_page("12514139", "A title", repo=str(tmp_path))
    assert p["image"] == "https://patentref.io/og/p/12514139.png" and p["url"] == "https://patentref.io/p/12514139"
    p = previews.record_page("10000000", None, repo=str(tmp_path))
    assert p["image"] == previews.RECORD_CARD and p["title"] == "US 10000000"
    previews._cards.clear()


def test_a_long_title_is_cut_at_a_word():
    long = "Method and apparatus for " + "measuring the thing " * 20
    t = previews.cut_title(long)
    assert len(t) <= previews.TITLE_MAX + 1 and t.endswith("…") and long.startswith(t[:-1]) and not t[:-1].endswith(" ")
    assert previews.cut_title("  A   short\ttitle ") == "A short title" and previews.cut_title(None) == ""


# ---------------------------------------------------------------- the pages under /docs/ this app serves

@pytest.fixture
def legal_dir(tmp_path, monkeypatch):
    (tmp_path / "terms.md").write_text("# PatentRef Terms of Service (free beta)\n\nEffective: 2026-10-20. Version: beta-2.\n\n## 1. One\n\nText.\n",
                                       encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(tmp_path), raising=False)
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    return tmp_path


def test_the_legal_pages_have_their_preview(client, legal_dir):
    h = client.get("/docs/legal/terms").content.decode("utf-8")
    t = check_preview(h, "https://patentref.io/docs/legal/terms", "Terms of Service")
    assert t["og:description"] == meta_description(h) == "PatentRef Terms of Service, version beta-2, effective 2026-10-20."
    h = client.get("/docs/legal/").content.decode("utf-8")
    t = check_preview(h, "https://patentref.io/docs/legal/", "Legal documents")
    assert t["og:description"] == meta_description(h) == previews.LEGAL_INDEX_DESCRIPTION
    assert previews.legal_description("Privacy Policy") == "PatentRef Privacy Policy."


def test_the_error_index_has_its_preview_and_an_unknown_code_none(client):
    h = client.get("/docs/errors/", HTTP_ACCEPT=BROWSER).content.decode("utf-8")
    t = check_preview(h, "https://patentref.io/docs/errors/", "API error codes")
    assert t["og:description"] == meta_description(h)
    code = next(iter(public.ERROR_CODES))
    h = client.get(f"/docs/errors/{code}", HTTP_ACCEPT=BROWSER).content.decode("utf-8")
    t = check_preview(h, f"https://patentref.io/docs/errors/{code}", f"API error code {code}")
    assert t["og:description"] == meta_description(h)
    r = client.get("/docs/errors/NOPE_CODE", HTTP_ACCEPT=BROWSER)
    assert r.status_code == 404 and "og:" not in r.content.decode("utf-8") and "twitter:" not in r.content.decode("utf-8")
    assert "og:" not in client.get("/docs/errors/").content.decode("utf-8")       # the plain text answer is unchanged


# ---------------------------------------------------------------- nothing else

def test_the_other_pages_carry_no_preview(client):
    for path in ("/try/", "/api/v1/meta/signup/", "/waitlist/"):
        h = client.get(path, HTTP_ACCEPT=BROWSER).content.decode("utf-8")
        assert "og:" not in h and "twitter:" not in h and "ld+json" not in h, path


def test_no_tracking_and_no_third_party_address():
    """Release AE adds tags and one data block only: the preview template holds meta tags and nothing that loads or runs,
    every address in it and in the WebAPI block is on patentref.io, and the module holds no other host."""
    tpl = (HERE.parent / "templates" / "lapse_accounts" / "_preview.html").read_text(encoding="utf-8")
    body = re.sub(r"\{% comment %\}.*?\{% endcomment %\}", "", tpl, flags=re.S)
    assert set(re.findall(r"<(\w+)", body)) == {"meta"} and "src=" not in body and "<img" not in body and "<script" not in body
    src = (HERE.parent / "previews.py").read_text(encoding="utf-8")
    assert set(re.findall(r"https?://([^/\s\"']+)", src)) == {"patentref.io", "schema.org"}   # schema.org: the @context name, never fetched
    for v in json.dumps({k: x for k, x in previews.webapi().items() if k != "@context"}).split('"'):
        if v.startswith("http"):
            assert v.startswith("https://patentref.io/"), v
    for p in (previews.home_page(), previews.agents_page(), previews.record_page("12514139", "x", repo="/nonexistent")):
        assert p["url"].startswith("https://patentref.io/") and p["image"].startswith("https://patentref.io/og/")
    for name in ("home.html", "base.html", "record.html", "agents.html"):
        t = (HERE.parent / "templates" / "lapse_accounts" / name).read_text(encoding="utf-8")
        assert not re.search(r"<script[^>]*\bsrc=", t) and not re.search(r"<img[^>]+src=\"https?://", t) and "<iframe" not in t, name


def test_the_preview_words():
    strings = [previews.SITE_ALT, previews.RECORD_ALT, previews.RECORD_LINE, previews.HOME_TITLE, previews.HOME_DESCRIPTION,
               previews.AGENTS_TITLE, previews.AGENTS_DESCRIPTION, previews.LEGAL_INDEX_DESCRIPTION, previews.error_description(""),
               previews.error_description("ERR_Q"), previews.record_page("12514139", "x", repo="/nonexistent")["alt"],
               "The claim tree of US 12514139 as its PatentRef record page shows it"]
    words(dict(enumerate(strings)))
    for s in (previews.RECORD_LINE, previews.RECORD_ALT):
        assert not STATUS_WORDS.search(s), s
    assert str(date.today()) not in previews.RECORD_LINE
