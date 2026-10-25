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


def raw_doc(name):
    """The Markdown text of one document as the repo holds it, or None when the file is not on the box."""
    try:
        return (legal_dir() / DOCS[name][0]).read_text(encoding="utf-8")
    except (OSError, KeyError, UnicodeDecodeError):
        return None


# ---------------------------------------------------------------- the posting date (release wave4-Q)
#
# A version posted ahead of its effective date writes its dates as POSTED and EFFECTIVE in the repo ("Effective:
# {{effective}}. Version: beta-4. Posted: {{posted}}; ..."), so no commit has to guess the deploy day. The posted date is
# the day (UTC) the text went live on this server: the deploy records it once per document version in
# LAPSE_LEGAL_POSTED_FILE (manage.py legal_posted --stamp, run by ops/deploy_release.sh after the release passed its
# checks), and the effective date is NOTICE_DAYS after it (the 14 days the policy and the terms promise for a material
# change). Until the deploy has recorded it, the text shows today as the posted date, the effective date stays 14 days
# ahead and nothing waits on it switches on (in_force), so a late or a failed deploy never shows a date that is wrong
# or opens anything early. A document with written dates is served as written.
POSTED = "{{posted}}"
EFFECTIVE = "{{effective}}"
NOTICE_DAYS = 14
_VERSION = re.compile(r"Version:\s*([A-Za-z0-9.\-]+)")


def posted_file():
    f = getattr(settings, "LAPSE_LEGAL_POSTED_FILE", "")
    return Path(f) if f else Path(getattr(settings, "LAPSE_DATA_DIR", "/data/api")) / "legal_posted.json"


def read_stamps():
    """{name: {version: "YYYY-MM-DD"}} as the deploy recorded them; empty when the file is absent or unreadable."""
    import json

    try:
        data = json.loads(posted_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}


def _today():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date()


def posting(name, md=None, today=None):
    """For a document whose text carries POSTED or EFFECTIVE: {"name", "version", "posted", "effective", "stamped"} with
    dates; posted is the recorded day, or today while none is recorded (stamped False). None for a document with written
    dates or none at all."""
    from datetime import date, timedelta

    md = raw_doc(name) if md is None else md
    if not md or (POSTED not in md and EFFECTIVE not in md):
        return None
    m = _VERSION.search(md)
    version = m.group(1).rstrip(".") if m else None
    rec = read_stamps().get(name, {}).get(version or "") if version else None
    try:
        posted, stamped = date.fromisoformat(rec), True
    except (TypeError, ValueError):
        posted, stamped = today or _today(), False
    return {"name": name, "version": version, "posted": posted, "effective": posted + timedelta(days=NOTICE_DAYS), "stamped": stamped}


def read_doc(name, today=None):
    """The Markdown text of one document as served, or None when the file is not on the box: the repo's text with the
    posting dates filled in (posting above)."""
    md = raw_doc(name)
    p = posting(name, md, today) if md else None
    if p is None:
        return md
    return md.replace(POSTED, p["posted"].isoformat()).replace(EFFECTIVE, p["effective"].isoformat())


def _write_stamps(data):
    """Replace the record in one rename (a reader sees the old file or the new one, never half of one)."""
    import json
    import os
    import tempfile

    f = posted_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".legal_posted.", dir=str(f.parent))
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, f)


def stamp(names=None, today=None):
    """Record today (UTC) as the posted date of every document version that carries POSTED or EFFECTIVE and has none
    recorded yet; a recorded date never moves. Returns the posting of each such document after the call."""
    today = today or _today()
    data, todo = read_stamps(), []
    for name in ORDER:
        p = posting(name, today=today) if not names or name in names else None
        if p is None or not p["version"]:
            continue
        if not p["stamped"]:
            data.setdefault(name, {})[p["version"]] = today.isoformat()
        todo.append(name)
    if data != read_stamps():
        _write_stamps(data)
    return [posting(n, today=today) for n in todo]


def unstamp(name, version):
    """Forget the recorded posted date of one document version (a rollback that took the text off the site): the next
    deploy that serves it records its own day and the 14 days start again. True when there was one."""
    data = read_stamps()
    if version not in data.get(name, {}):
        return False
    del data[name][version]
    if not data[name]:
        del data[name]
    _write_stamps(data)
    return True


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
    is the one in force on this server (wave 4 Q1): posted 14 days ahead, they wait for its date by themselves. A version
    whose posted date the deploy has not recorded yet is never in force (its effective date stays 14 days ahead)."""
    from datetime import date, datetime, timezone

    today = today or datetime.now(timezone.utc).date()
    effective, version = doc_meta(read_doc(name, today) or "")
    m = re.fullmatch(r"beta-(\d+)", version or "")
    if not (effective and m):
        return False, None
    try:
        when = date.fromisoformat(effective)
    except ValueError:
        return False, None
    return (int(m.group(1)) >= beta and today >= when), when


def upcoming(today=None):
    """The documents posted ahead of their effective date (UTC), for the status page's notice: the Privacy Policy and the
    terms promise that material changes are announced there at least 14 days before they take effect. Each is
    {"name", "title", "version", "effective", "url", "summary", "posted", "stamped"}; the summary is the document's own
    paragraph that starts "What is new in <version>:" (empty when it has none). posted and stamped come from posting()
    for a version whose dates the deploy records (None and True for a document with written dates)."""
    from datetime import date, datetime, timezone

    today = today or datetime.now(timezone.utc).date()
    out = []
    for name in ORDER:
        md = read_doc(name, today)
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
        p = posting(name, today=today)
        out.append({"name": name, "title": DOCS[name][1], "version": version, "effective": effective,
                    "url": f"/docs/legal/{name}", "summary": summary,
                    "posted": p["posted"].isoformat() if p else None, "stamped": p["stamped"] if p else True})
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
