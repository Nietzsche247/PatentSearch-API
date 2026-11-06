"""The triage table page (gate S1.8L): /triage/ and /triage/example/ on the site's page template (base.html).

  GET /triage/          the page: a pasted list of up to 500 patent numbers, or a search of the claims, sent from the browser
                        with the visitor's own key to POST /api/v1/structure/triage/ (API/lapse_triage.py); the answer is
                        drawn as a table, each independent claim as a list of its parts.
  GET /triage/example/  the same page with an example table already drawn: a fixed list (settings.LAPSE_TRIAGE_EXAMPLE, else
                        EXAMPLE) built by the same code as the API (lapse_triage.build_table) on read, without a key, and
                        kept per worker for the data versions in service. Nothing else can be asked here.

The have and lacks marks are the visitor's own notes on the parts of each independent claim. The page keeps them in the
browser (localStorage) and writes them into its own CSV and JSON export; they never reach the server. The key is held by the
page while it is open and sent only to this site's /api/v1/ (connect-src 'self'); the form's fields have no names, so no
browser ever puts them in an address.

Content-Security-Policy: the inline script and the inline style by their SHA-256, computed from the page as rendered;
requests to this origin only; no frames, no form posts, nothing from elsewhere.
"""
import base64
import hashlib
import json
import re
import threading

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from lapse_accounts import public

SITE = "https://patentref.io"
# The example table: six pool cleaner patents of three statuses (two active, one whose maintenance fee went unpaid, three
# whose term ended) and four components that between them take each of the four positions. Picked by lane ZC on 2026-10-29
# from the claims index of the long-text file in service (claims with "pool cleaner" and brush, or "pool cleaner" and
# "drive motor"); each has claim text in that file, and the statuses and positions were read on patentref-us1 that day.
EXAMPLE = {"patents": ["11773614", "11649651", "8696821", "9051750", "6601255", "6797157"],
           "components": ["drive motor", "housing", "filter", "nozzle"]}
EXAMPLE_CACHE = "public, max-age=300"

_STYLE = re.compile(r"<style>(.*?)</style>", re.S)
_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
_example = {"key": None, "body": None}
_example_lock = threading.Lock()


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def csp_for(html):
    """The policy of one rendered page: its own inline style and script by hash, nothing else."""
    styles = " ".join(_sha(s) for s in _STYLE.findall(html)) or "'none'"
    scripts = " ".join(_sha(s) for s in _SCRIPT.findall(html)) or "'none'"
    return "; ".join(["default-src 'none'", f"script-src {scripts}", f"style-src {styles}", "img-src 'self' data:",
                      "connect-src 'self'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"])


def _ctx(**extra):
    from API import lapse_triage
    from lapse_accounts.views import _data_version   # here, not at the top: views imports public, which this imports

    version, as_of = _data_version()
    ctx = {"data_version": version, "as_of": as_of, "footer_lines": public.footer_lines(), "site": SITE,
           "max_patents": lapse_triage.MAX_PATENTS, "max_components": lapse_triage.MAX_COMPONENTS,
           "time_limit": int(lapse_triage.TIME_LIMIT_SECONDS), "example": None, "problem": None}
    ctx.update(extra)
    return ctx


def render(request, ctx, status=200, cache="public, max-age=300"):
    html = render_to_string("lapse_accounts/triage.html", ctx, request=request)
    resp = HttpResponse(html, status=status, content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = csp_for(html)
    resp["Cache-Control"] = cache
    return resp


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def page(request):
    return render(request, _ctx())


def example_config():
    conf = getattr(settings, "LAPSE_TRIAGE_EXAMPLE", None) or EXAMPLE
    return {"patents": list(conf["patents"]), "components": list(conf.get("components") or [])}


def example_table(request):
    """The example's answer for the data in service, built once per worker and data version; (body, None) or (None, why)."""
    from API import lapse_triage
    from API.lapse_views import base_url
    from API.search import get_searcher

    conf = example_config()
    searcher = get_searcher()
    base = base_url(request)
    key = (searcher.data_version(), searcher.text_data_version(), base, json.dumps(conf, sort_keys=True))
    with _example_lock:
        if _example["key"] == key:
            return _example["body"], None
    try:
        req = lapse_triage.read_input({"patents": conf["patents"], "components": conf["components"] or None})
        body, _info = lapse_triage.build_table(searcher, req, base)
    except lapse_triage.Refusal as e:
        return None, e.reason
    if lapse_triage.wording_problems(lapse_triage.own_words(body)):
        return None, "the example failed the wording check"
    if body["complete"]:
        with _example_lock:
            _example.update(key=key, body=body)
    return body, None


# The example table drawn on the server (release AD, PatentRef 1.22.0: until then /triage/example/ drew its table only in the
# browser, so a crawler or an agent fetching the page saw no table). The words are the page script's (_triage.js: POS_TEXT,
# STATUS_TEXT, NOTE_TEXT, statusBits, cellBits, drawTable and summary, for the list the example sends); when the script runs it
# draws the same table again, with its order menu, its marks and its export. A test compares the two.
POS_TEXT = {"every_independent": "Every independent claim", "some_independent": "Some independent claims",
            "only_dependent": "Dependent claims only", "absent": "Not in the claims"}
STATUS_TEXT = {"active": "Active", "expired_term": "Expired: term ended", "expired_fee_nonpayment": "Expired: fees not paid",
               "withdrawn": "Withdrawn", "unknown": "Unknown"}
NOTE_TEXT = {
    "not_in_data": "The data in service has no patent with this number.",
    "no_claim_text": "The long-text file in service has no claims for this patent (granted after its last grant date).",
    "work_cap": "Its claims are over the claim tree's work cap (1,000 claim rows, 1,000,000 characters).",
    "offset_check": "Its claim tree failed its offset check, so nothing of it is shown.",
    "time_limit": "Not read: the table reached its time limit. Send this patent again.",
}
MARKABLE = ("preamble", "element", "wherein_clause")


def _short_list(a):
    a = [str(x) for x in a]
    return ", ".join(a[:8]) + f" and {len(a) - 8} more" if len(a) > 8 else ", ".join(a)


def _yes_no(v):
    return "yes" if v is True else "no" if v is False else str(v)


def _status_bits(p):
    """[(kind, text)]: the status cell of one row, as statusBits() draws it."""
    s = p.get("status")
    if not s:
        return [("", "Not available" if p.get("found") else "Not in the data")]
    bits = [("strong", STATUS_TEXT.get(s.get("status")) or str(s.get("status")))]
    basis = s.get("basis")
    if basis:
        bits.append(("small", "Basis: " + (", ".join(str(b) for b in basis) if isinstance(basis, list) else str(basis))))
    for name, label in (("expiry_date_estimated", "Term end, computed: "), ("lapse_date", "Fee lapse: "), ("reinstated_date", "Reinstated: ")):
        if s.get(name):
            bits.append(("small", label + str(s[name])))
    bits.append(("small", "Reinstatement possible: " + _yes_no(s.get("reinstatement_possible"))))
    return bits


def _cell_bits(c):
    """[(kind, text)]: one component's cell, as cellBits() draws it."""
    bits = [("label", POS_TEXT.get(c.get("position")) or str(c.get("position")))]
    where = []
    if c.get("independent_claims"):
        where.append("independent " + _short_list(c["independent_claims"]))
    if c.get("dependent_claims"):
        where.append("dependent " + _short_list(c["dependent_claims"]))
    if where:
        bits.append(("small", "Claims: " + "; ".join(where)))
    if c.get("match") == "partial":
        bits.append(("small", "Partial: " + ", ".join(str(k) for k in c.get("claim_keys") or [])))
    return bits


def example_rows(t):
    """The example's rows in the order given, as drawTable() draws them before any mark: the patent's link and title, its
    status, one cell a component (or the note of a patent without a tree), its claim count and its number of parts."""
    rows = []
    for p in t.get("patents") or []:
        built = bool(p.get("built"))
        claims = p.get("claims") or None
        parts = sum(1 for cl in p.get("claim_parts") or [] for part in cl.get("parts") or [] if part.get("part") in MARKABLE)
        rows.append({"pid": p.get("patent_id"), "permalink": p.get("permalink"), "title": p.get("title"), "status": _status_bits(p),
                     "cells": [{"position": c.get("position"), "bits": _cell_bits(c)} for c in p.get("cells") or []] if built else None,
                     "note": "" if built else NOTE_TEXT.get(p.get("note"), ""),
                     "claims": f"{claims['total']} claims, {len(claims['independent'])} independent" if claims else "",
                     "parts": f"{parts} parts" if built else ""})
    return rows


def example_summary(t):
    """The sentence above the table, as summary() writes it for a list of patents (the example's input)."""
    c, inp, s = t.get("counts") or {}, t.get("input") or {}, []
    n = c.get("patents", 0)
    s.append(f"{n} patent{'' if n == 1 else 's'} in the table, {c.get('built', 0)} with a claim tree.")
    if n - c.get("found", 0):
        s.append(f"{n - c.get('found', 0)} not in the data in service.")
    if c.get("not_built"):
        s.append(f"{c['not_built']} without a tree; their rows say why.")
    if not t.get("complete"):
        s.append(f"The table reached its time limit of {t.get('time_limit_seconds')} s: build it again for the rest.")
    if inp.get("kind") == "list" and inp.get("unread_count"):
        k = inp["unread_count"]
        s.append(f"{k} piece{'' if k == 1 else 's'} of the list read as no patent number: {', '.join(str(u) for u in inp.get('unread') or [])}.")
    if inp.get("unread_components"):
        s.append("Read as no component: " + ", ".join(str(u) for u in inp["unread_components"]) + ".")
    if not t.get("status_available"):
        s.append("The status is not available on the server right now.")
    s.append(f"Data version {t.get('data_version')}, claims from {t.get('text_data_version')}, as of {t.get('as_of')}.")
    return " ".join(s)


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def example(request):
    body, why = example_table(request)
    if body is None:
        return render(request, _ctx(problem="The example table cannot be built right now. Try again in a few minutes."), 503,
                      cache="no-store")
    return render(request, _ctx(example=body, example_rows=example_rows(body), example_summary=example_summary(body)),
                  cache=EXAMPLE_CACHE if body["complete"] else "no-store")
