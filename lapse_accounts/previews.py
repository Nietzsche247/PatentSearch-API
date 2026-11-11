"""Link previews and the home page's structured data (release AE).

Every page a link to it is shared from (the home page, /agents/, the record pages /p/<patent_id>, and the pages this app
serves under /docs/: the legal documents and the error index) carries Open Graph and Twitter card tags, written by
templates/lapse_accounts/_preview.html from the `preview` dict a view puts in its context. The text is ours and fixed:
a page's own title and description, and for a record page "US <number>: <its title>" and one line about what the page
holds; never the abstract and never a status. The images are static PNGs of 1200 by 630 pixels that the patentref repo
ships in public/site/og/ and ops/deploy.sh installs where Caddy serves them at /og/ (nothing is drawn at request time):
the site card, the record card, and a claim tree card of its own for each record page that has one, which is the case
when <patentref clone>/public/site/og/p/<patent_id>.png exists (the clone this server reads, LAPSE_REPO_DIR; read once
per server process). The home page also carries a schema.org WebAPI block (webapi_ld). No script, no pixel and no
address outside https://patentref.io is added to any page.
"""
import json
import os
import re

from django.utils.safestring import mark_safe

SITE = "https://patentref.io"
WIDTH, HEIGHT = 1200, 630
SITE_CARD = SITE + "/og/site.png"
RECORD_CARD = SITE + "/og/record.png"
SITE_ALT = "PatentRef, a PatentsView-compatible API for US patents granted from 1976 on"
RECORD_ALT = "A PatentRef record page: the claims of a US patent as a tree, with its status and record from USPTO data"
RECORD_LINE = "Its claims as a tree, its status and its record, from USPTO data."
HOME_TITLE = "PatentRef: a PatentsView-compatible US patent API"
# the home page's meta description, word for word (templates/lapse_accounts/home.html; a test keeps the two equal)
HOME_DESCRIPTION = ("PatentRef is a PatentsView-compatible API for US patents granted from 1976 on, refreshed every week. Change the "
                    "base URL and add a free key to run PatentsView code against it.")
AGENTS_TITLE = "PatentRef for agents: the MCP server and the machine-readable files"
# the Agents page's meta description, word for word (templates/lapse_accounts/agents.html; a test keeps the two equal)
AGENTS_DESCRIPTION = ("patentref-mcp, a local stdio MCP server from PyPI for the PatentRef API: install lines for Claude Desktop, "
                      "Claude Code and Cursor, its tools with their endpoints, the key rule and the machine-readable files.")
TITLE_MAX = 120          # a record title longer than this is cut at a word and ends with an ellipsis
CARD_NAME = re.compile(r"([A-Za-z0-9]{1,20})\.png")
_cards = {}


def _repo_dir():
    from API.lapse_group import repo_dir   # here, not at the top: the API package imports Django settings

    return repo_dir()


def card_ids(repo=None):
    """The patent numbers with a claim tree card of their own: the PNG files in <patentref clone>/public/site/og/p/, read
    once per server process (a deploy's worker reload reads them again); none when the folder is missing."""
    d = os.path.join(repo or _repo_dir(), "public", "site", "og", "p")
    got = _cards.get(d)
    if got is None:
        try:
            got = frozenset(m.group(1) for m in (CARD_NAME.fullmatch(n) for n in os.listdir(d)) if m)
        except OSError:
            got = frozenset()
        _cards[d] = got
    return got


def site_page(title, description, path, kind="article"):
    """The preview of a page of the site: its title and description, its address, the site card."""
    return {"type": kind, "title": title, "description": description, "url": SITE + path,
            "image": SITE_CARD, "alt": SITE_ALT, "width": WIDTH, "height": HEIGHT}


def cut_title(title, limit=TITLE_MAX):
    t = " ".join((title or "").split())
    if len(t) <= limit:
        return t
    head = t[:limit].rsplit(" ", 1)[0].rstrip(" ,;:.-")
    return (head or t[:limit]) + "…"


def record_page(pid, title, repo=None):
    """The preview of the record page of US <pid>: "US <pid>: <title>", the fixed line, the page's address without a data
    version (the address a link names), and its claim tree card when it has one, else the record card."""
    t = cut_title(title)
    if pid in card_ids(repo):
        image, alt = f"{SITE}/og/p/{pid}.png", f"The claim tree of US {pid} as its PatentRef record page shows it"
    else:
        image, alt = RECORD_CARD, RECORD_ALT
    return {"type": "article", "title": f"US {pid}: {t}" if t else f"US {pid}", "description": RECORD_LINE,
            "url": f"{SITE}/p/{pid}", "image": image, "alt": alt, "width": WIDTH, "height": HEIGHT}


def home_page():
    return site_page(HOME_TITLE, HOME_DESCRIPTION, "/", kind="website")


def agents_page():
    return site_page(AGENTS_TITLE, AGENTS_DESCRIPTION, "/agents/")


def legal_description(short_title, version=None, effective=None):
    """A legal page's meta description, as templates/lapse_accounts/legal.html writes it."""
    return (f"PatentRef {short_title}" + (f", version {version}" if version else "") + (f", effective {effective}" if effective else "")
            + ".")


LEGAL_INDEX_DESCRIPTION = ("PatentRef's terms of service, privacy policy, acceptable use policy, copyright (DMCA) page and processor "
                           "register.")


def error_description(code):
    """The error index's and an error code page's meta description, as templates/lapse_accounts/errors.html writes them."""
    if code:
        return f"What the PatentRef API error code {code} means."
    return "The error codes of the PatentRef API: the X-Status-Reason-Code header and the RFC 9457 problem types."


def webapi():
    """The home page's schema.org WebAPI: the service, its docs, its operator as the terms name him, its terms."""
    return {
        "@context": "https://schema.org", "@type": "WebAPI", "name": "PatentRef", "description": HOME_DESCRIPTION,
        "url": SITE + "/", "documentation": SITE + "/docs/", "termsOfService": SITE + "/docs/legal/terms",
        "logo": SITE + "/brand/logo.png",
        "provider": {"@type": "Person", "name": "Aaron Baker", "givenName": "Aaron", "familyName": "Baker",
                     "affiliation": {"@type": "Organization", "name": "PatentRef", "url": SITE + "/"}},
    }


def ld_json(obj):
    """JSON for a <script type="application/ld+json"> block: <, > and & escaped, so no text in it can end the block."""
    s = json.dumps(obj, ensure_ascii=False, separators=(", ", ": "))
    return mark_safe(s.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e"))
