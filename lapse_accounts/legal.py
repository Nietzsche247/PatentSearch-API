"""The legal pages at /docs/legal/ (PatentRef checklist 5.11 and 5.12).

  GET /docs/legal/                 the list of the documents below, each with its version and effective date
  GET /docs/legal/<name>           one document as a page on the shared skeleton (base.html)
  GET /docs/legal/<name>.md        the same document as its Markdown source (the machine-readable twin)

  <name>: terms, privacy, acceptable-use, dmca, processors

The text is read on every request from the patentref repo clone on the box (LAPSE_LEGAL_DIR, default
<LAPSE_REPO_DIR>/docs/legal), the same way /llms.txt is served from it (public.py), so the served words are the
approved words in that repo and there is no second copy here. A missing file answers 404. The converter below knows
only what those files use: headings, paragraphs, bullet and numbered lists, inline code, bold, links in the
[text](url) form, and bare patentref.io paths, https URLs and email addresses, which it turns into links. It changes
no word of the text.
"""
import html
import re
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

from lapse_accounts import public

DOCS = {  # name -> (file, short title used in the list and the page title)
    "terms": ("terms.md", "Terms of Service"),
    "privacy": ("privacy.md", "Privacy Policy"),
    "acceptable-use": ("acceptable-use.md", "Acceptable Use Policy"),
    "dmca": ("dmca.md", "Copyright complaints (DMCA)"),
    "processors": ("processors.md", "Processors and data processing agreements"),
}
ORDER = ("terms", "privacy", "acceptable-use", "dmca", "processors")
CACHE = "public, max-age=300"


def legal_dir():
    d = getattr(settings, "LAPSE_LEGAL_DIR", "")
    if d:
        return Path(d)
    return Path(getattr(settings, "LAPSE_REPO_DIR", "/srv/lapse")) / "docs" / "legal"


def read_doc(name):
    """The Markdown text of one document, or None when the file is not on the box."""
    try:
        return (legal_dir() / DOCS[name][0]).read_text(encoding="utf-8")
    except (OSError, KeyError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- Markdown to HTML (the subset the documents use)

_INLINE = re.compile(
    r"`(?P<code>[^`]+)`"
    r"|\*\*(?P<bold>[^*]+)\*\*"
    r"|\[(?P<ltext>[^\]]+)\]\((?P<lurl>https?://[^)\s]+|/[^)\s]*|mailto:[^)\s]+)\)"
    r"|(?P<url>https?://[^\s<>()]+)"
    r"|(?P<email>(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})"
    r"|(?P<site>(?<![\w@/.-])patentref\.io(?:/[A-Za-z0-9/_.~%-]*)?)"
)
_TRAIL = ".,;:"


def _split_trailing(s):
    """A bare link's trailing sentence punctuation is not part of it."""
    tail = ""
    while s and s[-1] in _TRAIL:
        tail = s[-1] + tail
        s = s[:-1]
    return s, tail


def _a(href, text):
    return f'<a href="{html.escape(href, quote=True)}">{html.escape(text)}</a>'


def inline(text):
    out, pos = [], 0
    for m in _INLINE.finditer(text):
        out.append(html.escape(text[pos:m.start()], quote=False))
        pos = m.end()
        if m.group("code") is not None:
            out.append(f"<code>{html.escape(m.group('code'), quote=False)}</code>")
        elif m.group("bold") is not None:
            out.append(f"<strong>{html.escape(m.group('bold'), quote=False)}</strong>")
        elif m.group("ltext") is not None:
            out.append(_a(m.group("lurl"), m.group("ltext")))
        elif m.group("url") is not None:
            u, tail = _split_trailing(m.group("url"))
            out.append(_a(u, u) + html.escape(tail, quote=False))
        elif m.group("email") is not None:
            e = m.group("email")
            out.append(_a("mailto:" + e, e))
        else:
            s, tail = _split_trailing(m.group("site"))
            path = s[len("patentref.io"):] or "/"
            out.append(_a("https://patentref.io" + path, s) + html.escape(tail, quote=False))
    out.append(html.escape(text[pos:], quote=False))
    return "".join(out)


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return "s-" + (s or "section")


_OL = re.compile(r"^(\d+)\.\s+(.*)$")


def to_html(md):
    """(title, body html) of a document: the first level-1 heading is the page's h1."""
    title, parts, para, items, kind, start = "", [], [], [], None, 1
    used = set()

    def flush_para():
        if para:
            parts.append("<p>" + inline(" ".join(x.strip() for x in para)) + "</p>")
            para.clear()

    def flush_list():
        nonlocal kind
        if items:
            if kind == "ol":
                attr = f' start="{start}"' if start != 1 else ""
                parts.append(f"<ol{attr}>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ol>")
            else:
                parts.append("<ul>" + "".join(f"<li>{inline(i)}</li>" for i in items) + "</ul>")
            items.clear()
        kind = None

    for raw in md.replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            flush_para(); flush_list()
            continue
        h = re.match(r"^(#{1,3})\s+(.*)$", line)
        if h:
            flush_para(); flush_list()
            level, text = len(h.group(1)), h.group(2).strip()
            if level == 1 and not title:
                title = text
                parts.append(f"<h1>{inline(text)}</h1>")
            else:
                level = max(level, 2)
                i, base = 2, slug(text)
                sid = base
                while sid in used:
                    sid, i = f"{base}-{i}", i + 1
                used.add(sid)
                parts.append(f'<h{level} id="{sid}">{inline(text)}</h{level}>')
            continue
        o = _OL.match(line)
        if line.startswith(("- ", "* ")):
            flush_para()
            if kind != "ul":
                flush_list(); kind = "ul"
            items.append(line[2:].strip())
        elif o:
            flush_para()
            if kind != "ol":
                flush_list(); kind = "ol"; start = int(o.group(1))
            items.append(o.group(2).strip())
        elif items and raw.startswith(("  ", "\t")):
            items[-1] += " " + line.strip()  # a list item continued on the next line
        else:
            flush_list()
            para.append(line)
    flush_para(); flush_list()
    return title, "\n".join(parts)


_META = re.compile(r"Effective:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\.?\s*(?:Version:\s*([A-Za-z0-9.\-]+))?")


def doc_meta(md):
    m = _META.search(md or "")
    return (m.group(1), (m.group(2) or "").rstrip(".") or None) if m else (None, None)


def in_force(name, beta, today=None):
    """(True, effective date) when the document served at /docs/legal/<name> is version beta-<beta> or later and its
    effective date (UTC) has come; (False, that date or None) otherwise, also when the file cannot be read. What the
    waitlist and the sign-up source count do is described from Privacy Policy beta-4 on, so they act only once that text
    is the one in force on this server (wave 4 Q1): posted 14 days ahead, they wait for its date by themselves."""
    from datetime import date, datetime, timezone

    effective, version = doc_meta(read_doc(name) or "")
    m = re.fullmatch(r"beta-(\d+)", version or "")
    if not (effective and m):
        return False, None
    try:
        when = date.fromisoformat(effective)
    except ValueError:
        return False, None
    today = today or datetime.now(timezone.utc).date()
    return (int(m.group(1)) >= beta and today >= when), when


def upcoming(today=None):
    """The documents posted ahead of their effective date (UTC), for the status page's notice: the Privacy Policy and the
    terms promise that material changes are announced there at least 14 days before they take effect. Each is
    {"name", "title", "version", "effective", "url", "summary"}; the summary is the document's own paragraph that starts
    "What is new in <version>:" (empty when it has none)."""
    from datetime import date, datetime, timezone

    today = today or datetime.now(timezone.utc).date()
    out = []
    for name in ORDER:
        md = read_doc(name)
        effective, version = doc_meta(md or "")
        try:
            when = date.fromisoformat(effective) if effective else None
        except ValueError:
            when = None
        if not when or when <= today:
            continue
        summary = ""
        for para in (md or "").split("\n\n"):
            para = " ".join(para.split())
            if version and para.startswith(f"What is new in {version}:"):
                summary = para[len(f"What is new in {version}:"):].strip()
                break
        out.append({"name": name, "title": DOCS[name][1], "version": version, "effective": effective,
                    "url": f"/docs/legal/{name}", "summary": summary})
    return out


# ---------------------------------------------------------------- views

def _context(**extra):
    from lapse_accounts.views import (
        _data_version,  # the header's data version, as on the sign-up page
    )

    version, as_of = _data_version()
    ctx = {"data_version": version, "as_of": as_of, "footer_lines": public.footer_lines()}
    ctx.update(extra)
    return ctx


def _not_found():
    resp = HttpResponse("not published yet\n", status=404, content_type="text/plain; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    return resp


@require_GET
def index(request):
    docs = []
    for name in ORDER:
        md = read_doc(name)
        if md is None:
            continue
        effective, version = doc_meta(md)
        docs.append({"name": name, "title": DOCS[name][1], "url": f"/docs/legal/{name}", "effective": effective, "version": version})
    html_out = render_to_string("lapse_accounts/legal_index.html", _context(docs=docs))
    resp = HttpResponse(html_out, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = CACHE
    return resp


@require_GET
def page(request, name):
    md = read_doc(name)
    if md is None:
        return _not_found()
    title, body = to_html(md)
    effective, version = doc_meta(md)
    ctx = _context(doc_title=title or DOCS[name][1], short_title=DOCS[name][1], body=body, name=name,
                   md_url=f"/docs/legal/{name}.md", effective=effective, version=version)
    resp = HttpResponse(render_to_string("lapse_accounts/legal.html", ctx), content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = CACHE
    resp["Link"] = f'</docs/legal/{name}.md>; rel="alternate"; type="text/markdown"'
    return resp


@require_GET
def source(request, name):
    md = read_doc(name)
    if md is None:
        return _not_found()
    resp = HttpResponse(md.encode("utf-8"), content_type="text/markdown; charset=utf-8")
    resp["Cache-Control"] = CACHE
    resp["Access-Control-Allow-Origin"] = "*"
    return resp
