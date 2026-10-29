"""PatentRef's own paths beside the v1 grammar (checklist 2.9 and 2.12; docs/SCAFFOLD.md 5.1 to 5.3).

  GET /api/v1/lapse/status/<patent_id>/   (alias GET /api/v1/status/<patent_id>/, the scaffold's path)
      The one-call verdict record, `check_patent_status` on REST: status, basis, reinstatement_possible with
      its 37 CFR 1.378 basis, as_of, data_version, the versioned permalink, `plain` (one sentence from the
      verdict-wording template) and `next_steps`. `?v=<data_version>`: a superseded version gets a
      `superseded` object (never a 404); a version that never existed is 404. X-Api-Key as every data path.
  GET /api/v1/lapse/similar?text=<string>&n=20
      FTS5 bm25 over patent_title and patent_abstract of the served snapshot, OR of the input's tokens, ties
      by patent_id ascending (scaffold 5.3). The tokens that go into the query are the rarest of the input
      (select_terms: document frequency from the snapshot, tokens in more than 2 percent of patents dropped,
      16 kept), which is what keeps the call near a second on 9.4 million patents; the response lists them
      as `terms`. X-Api-Key. With mode=structured (lane Y), patents ranked by the claim terms they share with
      the text, computed on read by claimgraph: API/lapse_similar_structured.py. Without mode, or with
      mode=text, the answer is the one above, byte for byte; another mode value is 400 ERR_Q.
  GET /p/<patent_id>?v=<data_version>
      The per-patent record page (HTML), the human-facing target of the permalink. No key; per-IP rate. Below the record
      it shows the patent's claims, with an anchor on each claim and each element (gate S0.4, API/lapse_permalinks.py).

Errors keep the upstream shape (`{"error": true}` plus X-Status-Reason and X-Status-Reason-Code), so the
RFC 9457 negotiation of gate 2.11 applies unchanged. Every response carries data_version and as_of, and the
status record is run through the banned-strings check before it leaves (never-regress item 8).
"""
import json
import logging
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

logger = logging.getLogger(__name__)

SIMILAR_MAX_TEXT = 4000
SIMILAR_MAX_N = 20
SIMILAR_MAX_TOKENS = 48   # distinct input tokens looked up, in order of appearance
SIMILAR_KEEP = 16         # the rarest of them that go into the OR query
SIMILAR_MIN_KEEP = 3      # when fewer pass the frequency cap, the rarest are kept up to this many
SIMILAR_MAX_DF = 0.02     # a token in more than this share of patents carries no signal and costs seconds
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
        mode = request.GET.get("mode")   # lane Y: mode=structured (API/lapse_similar_structured.py); absent or text: as before
        if mode is not None and mode not in ("text", "structured"):
            return _error("'mode' must be text or structured")
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
        if mode == "structured":
            from API.lapse_similar_structured import structured_answer

            return structured_answer(get_searcher(), text, n)
        terms = []
        for t in tokens(text):
            if len(t) > 1 and t not in terms:
                terms.append(t)
            if len(terms) >= SIMILAR_MAX_TOKENS:
                break
        searcher = get_searcher()
        con = searcher.connection()
        data_version = searcher.data_version() or ""
        cat = searcher.catalog_facts() or {}
        as_of = cat.get("as_of") or _as_of(searcher)
        keep = select_terms(con, terms)
        neighbors = []
        if keep:
            match = "{patent_title patent_abstract}: " + " OR ".join(fts_quote(t) for t in keep)
            sql = ('SELECT p.patent_id, p.patent_title, p.patent_date, bm25(fts_patents, 0.0, 1.0, 1.0) AS s '
                   'FROM fts_patents JOIN patents p ON p.rowid = fts_patents.rowid '
                   'WHERE fts_patents MATCH ? AND p.withdrawn = 0 ORDER BY s ASC, p.patent_id ASC LIMIT ?')
            rows = searcher._run(sql, (match, n))
            neighbors = [{"rank": i + 1, "patent_id": r[0], "patent_title": r[1], "patent_date": r[2], "score": round(-float(r[3]), 4)}
                         for i, r in enumerate(rows)]
        body = {"error": False, "data_version": data_version, "as_of": as_of, "method": "fts5_bm25_title_abstract",
                "n": n, "terms": keep, "neighbors": neighbors}
        return Response(body)


def select_terms(con, terms):
    """The query terms: the input's distinct tokens ranked by document frequency in the served snapshot
    (temp.fts_patents_vocab), those in more than SIMILAR_MAX_DF of patents dropped, the SIMILAR_KEEP rarest kept
    (at least SIMILAR_MIN_KEEP when the input has that many known tokens). Ties by the token itself, so the same
    input on the same snapshot picks the same terms. On the 20261006.2 corpus the unfiltered OR of a 30-token
    abstract took 14 to 27 s (the posting lists of 'a', 'and', 'system', 'method'); the rarest 16 take about 1 s."""
    if not terms:
        return []
    try:
        total = con.execute("SELECT n FROM _lapse_value_stats WHERE tbl = 'patents' AND col = '*'").fetchone()
        total = int(total[0]) if total else None
    except Exception:
        total = None
    if not total:
        try:
            total = con.execute("SELECT count(*) FROM patents").fetchone()[0]
        except Exception:
            total = 0
    df = {}
    try:
        for t in terms:
            row = con.execute("SELECT doc FROM temp.fts_patents_vocab WHERE term = ?", (t,)).fetchone()
            df[t] = int(row[0]) if row else 0
    except Exception:  # no vocab table (a snapshot without fts_patents): use the input as given
        return terms[:SIMILAR_KEEP]
    known = sorted((t for t in terms if df[t] > 0), key=lambda t: (df[t], t))
    cap = total * SIMILAR_MAX_DF if total else None
    keep = [t for t in known if cap is None or df[t] <= cap][:SIMILAR_KEEP]
    if len(keep) < SIMILAR_MIN_KEEP:
        keep = known[:max(len(keep), min(SIMILAR_MIN_KEEP, len(known)))]
    return sorted(keep, key=lambda t: (df[t], t))


def _as_of(searcher):
    try:
        rows = dict(searcher.connection().execute("SELECT k, v FROM _lapse_build").fetchall())
        rel = rows.get("release") or ""
        return f"{rel[:4]}-{rel[4:6]}-{rel[6:]}" if len(rel) == 8 else None
    except Exception:
        return None


def _record_page_error(status, heading, message, link_href="", link_text=""):
    """The record page's own answer when it has no record to show: a page with a language, a title and a heading in the
    skeleton of every page (checklist 7.6; the plain-text answers had neither), with the same status codes as before."""
    from lapse_accounts.public import footer_lines
    from lapse_accounts.views import _data_version

    version, as_of = _data_version()
    html = render_to_string("lapse_accounts/page_error.html", {
        "heading": heading, "message": message, "link_href": link_href, "link_text": link_text,
        "data_version": version, "as_of": as_of, "footer_lines": footer_lines()})
    resp = HttpResponse(html, status=status, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    resp["X-Robots-Tag"] = "noindex"
    return resp


NO_RECORD = ("Not found", "PatentRef has no record for this patent number. Check the number in the address.", "/docs/", "PatentRef documentation")


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
            return _record_page_error(404, *NO_RECORD)
        searcher = get_searcher()
        con = searcher.connection()
        data_version = searcher.data_version() or ""
        cat = searcher.catalog_facts()
        sup = version_facts(request.GET.get("v"), data_version)
        if sup is False:
            return _record_page_error(404, "Unknown data version", "PatentRef never served the data version in this address.",
                                      f"/p/{pid}", f"The current record for US {pid}")
        try:
            got = lapse_group.verdict_with_engine(con, cat, pid, data_version, base_url(request))
        except RuntimeError:
            return _record_page_error(503, "Not available right now", "This record page cannot be built right now. Try again in a few minutes.",
                                      "/api/v1/meta/status/", "Service status")
        if got is None:
            return _record_page_error(404, *NO_RECORD)
        rec, engine_rec = got
        title = None
        try:
            row = con.execute("SELECT patent_title FROM patents WHERE patent_id = ?", (pid,)).fetchone()
            title = row[0] if row else None
        except Exception:
            title = None
        from API import lapse_family

        fam = lapse_family.family_for(con, pid)
        lines = footer_lines()
        if fam and fam["credit"] not in lines:
            lines = list(lines) + [fam["credit"]]  # the Google credit on every page with a family block (LICENSES.md section 3)
        from API import lapse_permalinks

        claims = lapse_permalinks.page_claims(searcher, pid)  # the claims with a link to each claim and element (gate S0.4)
        html = render_to_string("lapse_accounts/record.html", {
            "rec": rec, "pid": pid, "title": title, "superseded": sup, "data_version": data_version,
            "as_of": rec.get("as_of"), "footer_lines": lines, "family": fam,
            "basis_text": lapse_wording.basis_text(rec.get("basis"), engine_rec),
            "term_end_text": lapse_wording.term_end_text(engine_rec),
            "status_json": json.dumps({k: v for k, v in rec.items() if k != "plain"}, indent=1),
            "claims": claims,
        })
        bad = lapse_wording.check_banned(html.split('data-user-content="true"')[0])
        if bad:
            logger.error("record page %s: wording check failed: %s", pid, ", ".join(bad))
            return _record_page_error(500, "This page could not be shown", "PatentRef held this page back because its wording failed a check.",
                                      "/api/v1/meta/status/", "Service status")
        resp = HttpResponse(html, content_type="text/html; charset=utf-8")
        resp["Cache-Control"] = "public, max-age=300"
        resp["X-Data-Version"] = data_version
        return resp
