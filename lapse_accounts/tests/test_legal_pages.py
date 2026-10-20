"""Gates 5.11 and 5.12: the legal pages at /docs/legal/ from the patentref repo's docs/legal/, and gate 7.6: every page this
app serves extends the one skeleton (lapse_accounts/templates/lapse_accounts/base.html) with the same footer links."""
import os
import re
from pathlib import Path

import pytest
from django.conf import settings

from API import lapse_wording
from lapse_accounts import legal, public

TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "lapse_accounts"
DASH = chr(0x2014)

TERMS = """# PatentRef Terms of Service (free beta)

Effective: 2026-10-08. Version: beta-1. Contact: legal@patentref.io (until then aaron@omnipoolbuilders.com).

## 1. What the Service is

The license register at patentref.io/LICENSES.md lists every table. The guide is at patentref.io/llms-full.txt; see
https://example.org/a_b. Use `X-Api-Key` and **read** [the policy](/docs/legal/privacy).

## 2. The beta

- Access is free. Nothing is charged.
- The Service may change <script>alert(1)</script> & stop.

1. Attacking the Service. No denial of service.
2. Evading limits.
"""


@pytest.fixture
def legal_dir(tmp_path, monkeypatch):
    (tmp_path / "terms.md").write_text(TERMS, encoding="utf-8")
    (tmp_path / "privacy.md").write_text("# Privacy\n\nEffective: 2026-10-08. Version: beta-1.\n\n## 1. One\n\nText.\n", encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(tmp_path), raising=False)
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    return tmp_path


def _real_legal_dir():
    """docs/legal of a patentref checkout: LAPSE_REPO_DIR, a sibling checkout, or the box's /srv/lapse."""
    root = Path(__file__).resolve().parent.parent.parent
    cands = [os.environ.get("LAPSE_REPO_DIR", "")] + [str(root.parent / n) for n in ("trust", "patentref", "lapse", "pr")] + ["/srv/lapse"]
    for c in cands:
        if c and (Path(c) / "docs" / "legal" / "terms.md").exists():
            return Path(c) / "docs" / "legal"
    return None


def test_page_renders_the_markdown(client, legal_dir):
    r = client.get("/docs/legal/terms")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/html")
    h = r.content.decode()
    assert "<h1>PatentRef Terms of Service (free beta)</h1>" in h
    assert '<h2 id="s-1-what-the-service-is">1. What the Service is</h2>' in h
    assert '<a href="https://patentref.io/LICENSES.md">patentref.io/LICENSES.md</a> lists' in h
    assert '<a href="https://patentref.io/llms-full.txt">patentref.io/llms-full.txt</a>;' in h
    assert '<a href="mailto:legal@patentref.io">legal@patentref.io</a>' in h
    assert '<a href="mailto:aaron@omnipoolbuilders.com">aaron@omnipoolbuilders.com</a>)' in h
    assert '<a href="https://example.org/a_b">https://example.org/a_b</a>.' in h
    assert "<code>X-Api-Key</code>" in h and "<strong>read</strong>" in h and '<a href="/docs/legal/privacy">the policy</a>' in h
    assert "<ul><li>Access is free. Nothing is charged.</li>" in h and "<ol><li>Attacking the Service." in h
    assert "<script>alert(1)</script>" not in h and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; stop." in h
    assert 'class="pr-header"' in h and 'class="pr-footer"' in h and "Data version " in h
    assert r["Link"] == '</docs/legal/terms.md>; rel="alternate"; type="text/markdown"'
    assert client.get("/docs/legal/terms/").status_code == 200


def test_words_unchanged(legal_dir):
    """The converter changes markup, never words: the page's text equals the source's text."""
    _, body = legal.to_html(TERMS)
    text = re.sub(r"<[^>]+>", " ", body).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    src = re.sub(r"^(?:- |\d+\. )", " ", TERMS, flags=re.MULTILINE)        # list markers become <ul> and <ol>
    src = re.sub(r"\]\([^)]*\)", "", src)                          # a link's target becomes its href
    src = re.sub(r"[`#\[]|\*\*", " ", src)                           # code, heading and bold marks
    assert "".join(text.split()) == "".join(src.split())  # every character of the text, in order; only spacing differs


def test_source_and_index_and_missing(client, legal_dir):
    r = client.get("/docs/legal/terms.md")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/markdown") and r.content.decode() == TERMS
    r = client.get("/docs/legal/")
    h = r.content.decode()
    assert r.status_code == 200 and '<a href="/docs/legal/terms">Terms of Service</a>, version beta-1, effective 2026-10-08' in h
    assert '<a href="/docs/legal/privacy">Privacy Policy</a>' in h and "Copyright complaints (DMCA)" not in h  # absent files are not listed
    assert client.get("/docs/legal/dmca").status_code == 404 and client.get("/docs/legal/dmca.md").status_code == 404
    assert client.get("/docs/legal/other").status_code == 404
    assert client.get("/docs/legal/..%2Fterms").status_code == 404


def test_every_page_extends_the_skeleton():
    """CI rule of skill page-template: every full page template extends base.html; partials start with an underscore."""
    pages = sorted(p.name for p in TEMPLATES.glob("*.html") if not p.name.startswith("_") and p.name != "base.html")
    assert {"signup.html", "status.html", "record.html", "legal.html", "legal_index.html", "unsubscribe.html"} <= set(pages)
    for name in pages:
        assert (TEMPLATES / name).read_text(encoding="utf-8").startswith('{% extends "lapse_accounts/base.html" %}'), name


def test_tokens_are_the_registry_copy():
    """_tokens.css is the byte copy of the patentref registry's src/app/tokens.css (skill css-tokens)."""
    real = _real_legal_dir()
    mine = (TEMPLATES / "_tokens.css").read_bytes()
    assert b"--accent: #1f5fbf;" in mine and b':root[data-theme="dark"]' in mine
    if real is None:
        pytest.skip("no patentref checkout beside this one")
    other = real.parent.parent / "registry" / "src" / "app" / "tokens.css"
    if not other.exists():
        pytest.skip("the patentref checkout has no registry tokens.css")
    assert mine == other.read_bytes()


def test_pages_share_header_footer_and_legal_links(client, legal_dir):
    for path in ("/api/v1/meta/signup/", "/api/v1/meta/status/", "/docs/legal/terms", "/docs/legal/"):
        h = client.get(path).content.decode()
        assert h.count('class="pr-header"') == 1 and h.count('class="pr-footer"') == 1, path
        for href in ("/docs/legal/terms", "/docs/legal/privacy", "/docs/legal/acceptable-use", "/docs/legal/dmca"):
            assert f'href="{href}"' in h, (path, href)
        assert DASH not in h, path
        assert '<html lang="en"' in h and '<main class="pr-main" id="main">' in h, path


def test_the_real_documents(client, monkeypatch):
    """The documents as drafted in the patentref repo: each serves, every patentref.io link in them is a route this app
    or the site answers, no em-dash, and the DMCA page names the registration."""
    real = _real_legal_dir()
    if real is None:
        pytest.skip("no patentref checkout beside this one")
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(real), raising=False)
    known = {"/", "/LICENSES.md", "/llms-full.txt", "/llms.txt", "/api/v1/meta/status/", "/api/v1/meta/signup/"} | {f"/docs/legal/{n}" for n in legal.DOCS}
    for name in legal.DOCS:
        md = legal.read_doc(name)
        assert md is not None, name
        assert DASH not in md, name
        r = client.get(f"/docs/legal/{name}")
        assert r.status_code == 200, name
        assert not lapse_wording.check_banned(r.content.decode()), name
        for href in re.findall(r'href="https://patentref\.io([^"]*)"', r.content.decode()):
            assert href in known, (name, href)
    assert "DMCA-1083075" in legal.read_doc("dmca")
