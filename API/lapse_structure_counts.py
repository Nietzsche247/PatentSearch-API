"""PatentRef's structure layer, gate S0.6: the claim counts of one patent, computed on read.

  GET /api/v1/structure/counts/<patent_id>/   (the trailing slash is optional, as on /api/v1/structure/claims/)
      Counts in the spirit of the USPTO Patent Claims Research Dataset (grants of 1976 to 2014), for any grant the
      long-text file in service holds: per patent the claims, the independent and dependent claims, the claims whose text
      names another claim, words (in all, of the first claim, and the least, mean and most of the independent and the
      dependent claims), characters, the elements of the independent claims, the longest chain of dependencies and the
      USPTO dataset's three word counts; per claim the same counts for that claim. API/claim_counts.py counts them from the
      claim tree claimgraph builds from the same rows the claim tree endpoint reads (txt.g_claims through its
      (patent_id, claim_sequence) index, API.lapse_structure.CLAIMS_SQL), and gives each field's definition and the USPTO
      variable it follows. Every field is a count, or the least, mean or most of counts.

Computed on read, never stored, like the claim tree (the lane X decision of 2026-10-28): CACHE is an in-process LRU of the
encoded answers (at most CACHE_ENTRIES answers and CACHE_BYTES bytes per worker process), keyed by patent_id, both data
versions, the structure version and the counts version. The claim tree's work cap holds here too (MAX_CLAIMS claim rows,
MAX_TEXT_CHARS characters, each read interrupted after READ_SECONDS): over it, 500 ERR_ES.

Errors in the upstream shape ({"error": true}, X-Status-Reason, X-Status-Reason-Code), so the RFC 9457 negotiation of gate
2.11 applies: 400 ERR_Q for a string that is not a patent number; 404 NOT_FOUND for a patent the data in service does not
have, and for a patent it has without claim text in the long-text file; 501 ERR_NOT_IMPLEMENTED when no long-text file is
in service. Keyed and throttled like every /api/v1/ data path, and it counts toward the monthly allowance like them; the
request log's structure_log row counts it with the rest of /api/v1/structure/.
"""
import json
import logging
import time

from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.views import APIView

from API import claim_counts, lapse_structure
from API.lapse_views import _as_of, _error, normalize_pid
from API.search import get_searcher

logger = logging.getLogger(__name__)

CACHE_ENTRIES = 1024              # answers kept per worker process (an answer is about 1 KB plus 0.3 KB a claim)
CACHE_BYTES = 8 * 1024 * 1024     # bytes kept per worker process (8 workers: 64 MB at most)
CACHE_ITEM_MAX = 1024 * 1024      # an answer larger than this is not kept (887 claims make about 280 KB)
CACHE = lapse_structure.AnswerCache(entries=CACHE_ENTRIES, max_bytes=CACHE_BYTES, item_max=CACHE_ITEM_MAX)


def counts_answer(pid, rows, data_version, text_version, as_of):
    """The answer for one patent's rows, or (None, problems) when its claim tree fails claimgraph's own check."""
    pc, per, problems = claim_counts.counts_from_rows(pid, rows)
    if problems:
        return None, problems
    body = {"error": False, "patent_id": pid, "patent_unit_id": f"US{pid}", "counts_version": claim_counts.COUNTS_VERSION,
            "structure_version": claim_counts.STRUCTURE_VERSION, "generator": claim_counts.GENERATOR,
            "data_version": data_version, "text_data_version": text_version, "as_of": as_of,
            "counts": pc, "claim_counts": per}
    return body, []


def _answer(data, text_version, cache_state, ms):
    resp = HttpResponse(data, content_type="application/json")
    resp["Cache-Control"] = lapse_structure.CACHE_CONTROL
    if text_version:
        resp["X-Text-Data-Version"] = text_version
    resp["Server-Timing"] = f'claimcounts;desc="{cache_state}";dur={ms:.1f}'
    return resp


class ClaimCountsView(APIView):
    """GET /api/v1/structure/counts/<patent_id>/ (gate S0.6)."""

    def get(self, request, pk, format=None):
        t0 = time.perf_counter()
        pid = normalize_pid(pk)
        if not pid:
            return _error(f"Not a patent number: {pk!r}")
        searcher = get_searcher()
        con = searcher.connection()
        data_version = searcher.data_version() or ""
        text_version = searcher.text_data_version()
        if not text_version:
            return _error("Endpoint not implemented: the 'g_claims' data set is not in this snapshot; no long-text file is in "
                          "service (GET /api/v1/meta/health/ shows text_data_version)", "ERR_NOT_IMPLEMENTED", http.HTTP_501_NOT_IMPLEMENTED)
        key = (pid, data_version, text_version, claim_counts.STRUCTURE_VERSION, claim_counts.COUNTS_VERSION)
        hit = CACHE.get(key)
        if hit is not None:
            return _answer(hit, text_version, "hit", (time.perf_counter() - t0) * 1000)
        cap, seconds = lapse_structure.MAX_CLAIMS, lapse_structure.READ_SECONDS
        try:
            rows = lapse_structure._read(con, lapse_structure.CLAIMS_SQL, (pid, cap + 1), seconds)
            known = bool(rows) or bool(lapse_structure._read(con, lapse_structure.PATENT_SQL, (pid,), seconds))
        except lapse_structure._Interrupted:
            return _error(f"Claim counts timed out: reading the claims of {pid} took over {seconds:.0f} s", "ERR_ES",
                          http.HTTP_500_INTERNAL_SERVER_ERROR)
        if not rows:
            if not known:
                return _error(f"Unknown patent_id: {pid!r} is not in the data in service ({data_version})", "NOT_FOUND",
                              http.HTTP_404_NOT_FOUND)
            return _error(f"No claim text for patent_id {pid!r}: the long-text file in service ({text_version}) has no claims "
                          f"for it; it holds the claims of grants up to its last grant date (GET /api/v1/meta/health/)", "NOT_FOUND",
                          http.HTTP_404_NOT_FOUND)
        if len(rows) > cap:
            return _error(f"The claims of {pid} are over this endpoint's work cap ({cap} claim rows)", "ERR_ES",
                          http.HTTP_500_INTERNAL_SERVER_ERROR)
        rows = [(p, seq, claim_counts.text_of(t), dep, num, ex) for p, seq, t, dep, num, ex in rows]
        chars = sum(len(r[2]) for r in rows)
        if chars > lapse_structure.MAX_TEXT_CHARS:
            return _error(f"The claims of {pid} are over this endpoint's work cap ({chars:,} characters; at most "
                          f"{lapse_structure.MAX_TEXT_CHARS:,})", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
        cat = searcher.catalog_facts() or {}
        as_of = cat.get("as_of") or _as_of(searcher)
        body, problems = counts_answer(pid, rows, data_version, text_version, as_of)
        if body is None:  # counts of a tree that fails its offset check are never given out
            logger.error("claim counts of %s: the claim tree failed its check: %s", pid, problems[:3])
            return _error(f"The claim tree of {pid} failed its offset check; no counts were served", "ERR_ES",
                          http.HTTP_500_INTERNAL_SERVER_ERROR)
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        CACHE.put(key, data)
        return _answer(data, text_version, "miss", (time.perf_counter() - t0) * 1000)
