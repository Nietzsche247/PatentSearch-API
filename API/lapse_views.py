"""PatentRef's own paths beside the v1 grammar (checklist 2.9 and 2.12; docs/SCAFFOLD.md 5.1 to 5.3).

  GET /api/v1/lapse/status/<patent_id>/   (alias GET /api/v1/status/<patent_id>/, the scaffold's path)
      The one-call verdict record, `check_patent_status` on REST: status, basis, reinstatement_possible with
      its 37 CFR 1.378 basis, as_of, data_version, the versioned permalink, `plain` (one sentence from the
      verdict-wording template) and `next_steps`. `?v=<data_version>`: a superseded version gets a
      `superseded` object (never a 404); a version that never existed is 404. X-Api-Key as every data path.
  GET /api/v1/lapse/similar?text=<string>&n=20
      FTS5 bm25 over patent_title and patent_abstract of the served snapshot, OR of the input's tokens, ties
      by patent_id ascending (scaffold 5.3). X-Api-Key.
  GET /p/<patent_id>?v=<data_version>
      The per-patent record page (HTML), the human-facing target of the permalink. No key; per-IP rate.

Errors keep the upstream shape (`{"error": true}` plus X-Status-Reason and X-Status-Reason-Code), so the
RFC 9457 negotiation of gate 2.11 applies unchanged. Every response carries data_version and as_of, and the
status record is run through the banned-strings check before it leaves (never-regress item 8).
"""
import json
import re

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework import status as http
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from API import lapse_group, lapse_wording
from API.search import get_searcher
from API.search_sqlite import fts_quote, tokens

SIMILAR_MAX_TEXT = 4000
SIMILAR_MAX_N = 20
SIMILAR_MAX_TOKENS = 64  # distinct tokens in order of appearance; the whole input counts against SIMILAR_MAX_TEXT
PID_RE = re.compile(r"^(?:US)?(RE|PP|D|H|T|AI|X|)0*([0-9]{1,8})$", re.I)


def _error(reason, code="ERR_Q", status=400):
    resp = Response(status=status, data={"error": True})
    resp["X-Status-Reason"] = reason
    resp["X-Status-Reason-Code"] = code
    return resp


def base_url(request):
    host = request.get_host()
    scheme = "https" if request.is_secure() or getattr(settings, "LAPSE_BEHIND_PROXY", False) else "http"
    return f"{scheme}://{host}"


def normalize_pid(raw):
    """'10905426', 'US10905426', 'D500396', 'RE31316', '0010905426' to the API's patent_id; None when not an id."""
    m = PID_RE.match((raw or "").strip())
    if not m:
        return None
    return (m.group(1).upper() if m.group(1) else "") + m.group(2)


def version_facts(requested, current):
    """None for the current version or no `v`; a superseded object for a known older version; False when unknown."""
    if not requested or requested == current:
        return None
    from lapse_accounts.public import meta_facts

    facts = meta_facts()
    if requested in (facts.get("superseded_versions") or []) or requested == facts.get("previous_version"):
        return {"requested": requested, "current": current, "note": "the requested data version has been superseded; "
                "this record is from the current version", "snapshot_archive": facts.get("licenses_url")}
    return False


class StatusView(APIView):
    """check_patent_status on REST (gate 2.12)."""

    def get(self, request, pk, format=None):
        pid = normalize_pid(pk)
        if not pid:
            return _error(f"Invalid patent_id: {pk!r}")
        searcher = get_searcher()
        con = searcher.connection()
        data_version = searcher.data_version() or ""
        cat = searcher.catalog_facts()
        sup = version_facts(request.GET.get("v"), data_version)
        if sup is False:
            return _error(f"Unknown data version {request.GET.get('v')!r}", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        try:
            rec = lapse_group.verdict(con, cat, pid, data_version, base_url(request))
        except RuntimeError as e:
            return _error(str(e), "ERR_ES", http.HTTP_503_SERVICE_UNAVAILABLE)
        if rec is None:
            return Response(status=http.HTTP_404_NOT_FOUND, data={"detail": "Not found."})
        if sup:
            rec["superseded"] = sup
        bad = lapse_wording.check_banned(json.dumps(rec))
        if bad:  # never-regress 8: a template defect must not leave the process
            return _error(f"wording check failed: {', '.join(bad)}", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
        resp = Response(rec)
        resp["Cache-Control"] = "public, max-age=300"
        return resp


class SimilarView(APIView):
    """GET /api/v1/lapse/similar?text=&n= (scaffold 5.3)."""

    def get(self, request, format=None):
        text = request.GET.get("text", "")
        if not text.strip():
            return _error("'text' is required")
        if len(text) > SIMILAR_MAX_TEXT:
            return _error(f"'text' over {SIMILAR_MAX_TEXT} characters")
        try:
            n = int(request.GET.get("n", SIMILAR_MAX_N))
        except ValueError:
            return _error("'n' must be an integer")
        if n < 1 or n > SIMILAR_MAX_N:
            return _error(f"'n' must be 1 to {SIMILAR_MAX_N}")
        terms = []
        for t in tokens(text):
            if len(t) > 1 and t not in terms:
                terms.append(t)
            if len(terms) >= SIMILAR_MAX_TOKENS:
                break
        searcher = get_searcher()
        data_version = searcher.data_version() or ""
        cat = searcher.catalog_facts() or {}
        as_of = cat.get("as_of") or _as_of(searcher)
        neighbors = []
        if terms:
            match = "{patent_title patent_abstract}: " + " OR ".join(fts_quote(t) for t in terms)
            sql = ('SELECT p.patent_id, p.patent_title, p.patent_date, bm25(fts_patents, 0.0, 1.0, 1.0) AS s '
                   'FROM fts_patents JOIN patents p ON p.rowid = fts_patents.rowid '
                   'WHERE fts_patents MATCH ? AND p.withdrawn = 0 ORDER BY s ASC, p.patent_id ASC LIMIT ?')
            rows = searcher._run(sql, (match, n))
            neighbors = [{"rank": i + 1, "patent_id": r[0], "patent_title": r[1], "patent_date": r[2], "score": round(-float(r[3]), 4)}
                         for i, r in enumerate(rows)]
        body = {"error": False, "data_version": data_version, "as_of": as_of, "method": "fts5_bm25_title_abstract",
                "n": n, "terms": len(terms), "neighbors": neighbors}
        return Response(body)


def _as_of(searcher):
    try:
        rows = dict(searcher.connection().execute("SELECT k, v FROM _lapse_build").fetchall())
        rel = rows.get("release") or ""
        return f"{rel[:4]}-{rel[4:6]}-{rel[6:]}" if len(rel) == 8 else None
    except Exception:
        return None


class RecordPage(APIView):
    """GET /p/<patent_id>: the human page behind the permalink. No key; the per-IP meta rate."""

    permission_classes = [AllowAny]
    throttle_scope = "meta"

    def get_throttles(self):
        from lapse_accounts.throttling import MetaThrottle

        return [MetaThrottle()]

    def get(self, request, pk, format=None):
        from lapse_accounts.public import footer_lines

        pid = normalize_pid(pk)
        if not pid:
            return HttpResponse("not found\n", status=404, content_type="text/plain; charset=utf-8")
        searcher = get_searcher()
        con = searcher.connection()
        data_version = searcher.data_version() or ""
        cat = searcher.catalog_facts()
        sup = version_facts(request.GET.get("v"), data_version)
        if sup is False:
            return HttpResponse("unknown data version\n", status=404, content_type="text/plain; charset=utf-8")
        try:
            rec = lapse_group.verdict(con, cat, pid, data_version, base_url(request))
        except RuntimeError as e:
            return HttpResponse(f"status engine unavailable: {e}\n", status=503, content_type="text/plain; charset=utf-8")
        if rec is None:
            return HttpResponse("not found\n", status=404, content_type="text/plain; charset=utf-8")
        title = None
        try:
            row = con.execute("SELECT patent_title FROM patents WHERE patent_id = ?", (pid,)).fetchone()
            title = row[0] if row else None
        except Exception:
            title = None
        html = render_to_string("lapse_accounts/record.html", {
            "rec": rec, "pid": pid, "title": title, "superseded": sup, "data_version": data_version,
            "as_of": rec.get("as_of"), "footer_lines": footer_lines(),
            "basis_text": lapse_wording.basis_text(rec.get("basis")),
            "status_json": json.dumps({k: v for k, v in rec.items() if k != "plain"}, indent=1),
        })
        bad = lapse_wording.check_banned(html.split('data-user-content="true"')[0])
        if bad:
            return HttpResponse(f"wording check failed: {', '.join(bad)}\n", status=500, content_type="text/plain; charset=utf-8")
        resp = HttpResponse(html, content_type="text/html; charset=utf-8")
        resp["Cache-Control"] = "public, max-age=300"
        resp["X-Data-Version"] = data_version
        return resp
