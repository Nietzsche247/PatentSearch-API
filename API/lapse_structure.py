"""PatentRef's structure layer, tier S0 (gate S0.2): the claim tree of one patent, computed on read.

  GET /api/v1/structure/claims/<patent_id>/   (the trailing slash is optional, as on /api/v1/lapse/status/)
      The patent's claims as the long-text file in service prints them (txt.g_claims, read through its
      (patent_id, claim_sequence) index), parsed by claimgraph, PatentRef's claim parser, a private package installed in the API's
      environment and imported through API/lapse_claimgraph.py (standard library only, regular expressions and small state machines, no model call): per claim the number, the
      category, the dependency with its basis, the preamble, the transition, the elements, the term mentions with
      the antecedent each reference was matched to, the claim references and the flags; per patent the relations
      between those units and the patent's own flags. Every span is an offset into the claim text as published:
      text[start:end] is the span's text, and the validator checks every one before the answer leaves.
      The answer is claimgraph's patent object with error, generator, data_version (the search file, the same as
      X-Data-Version), text_data_version (the long-text file the claims came from, also in X-Text-Data-Version) and
      as_of added. Flags and categories describe the text and the data; none states a legal conclusion.

Computed on read, never stored (the lane X decision of 2026-10-28, recorded in its handoff; the owner may overrule): CACHE is an in-process LRU of the encoded answers, at most
CACHE_ENTRIES answers and CACHE_BYTES bytes per worker process (whichever comes first; an answer over CACHE_ITEM_MAX
is not kept), keyed by patent_id, both data versions and the structure version, so a data swap or a new structure
version never serves an older tree. Work cap per request: at most MAX_CLAIMS claim rows and MAX_TEXT_CHARS characters
(the corpus's largest claim set, 887 claims of 6684189, built in 60 ms on the box), each read interrupted after
READ_SECONDS. Over the cap or interrupted: 500 ERR_ES, as the search timeout answers.

Errors in the upstream shape ({"error": true}, X-Status-Reason, X-Status-Reason-Code), so the RFC 9457 negotiation
of gate 2.11 applies: 400 ERR_Q for a string that is not a patent number; 404 NOT_FOUND for a patent the data in
service does not have, and for a patent it has without claim text in the long-text file (grants after the file's last
grant date); 501 ERR_NOT_IMPLEMENTED when no long-text file is in service. Keyed and throttled like every /api/v1/
data path (the DRF defaults of lapse_local), and it counts toward the monthly allowance like them.
"""
import json
import logging
import sqlite3
import threading
import time
from collections import OrderedDict

from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.views import APIView

from API.lapse_claimgraph import claimgraph
from API.lapse_views import _as_of, _error, normalize_pid
from API.search import get_searcher
from API.search_sqlite import plain

logger = logging.getLogger(__name__)

GENERATOR = f"claimgraph {claimgraph.__version__}"
MAX_CLAIMS = 1000            # claim rows of one patent; the corpus's largest set is 887 (6684189, 5095054 868, 7096160 803)
MAX_TEXT_CHARS = 1_000_000   # characters of claim text of one patent; the largest sets hold 128,239 to 227,477
READ_SECONDS = 5.0           # one read of the claim rows or of the patent's row, then interrupted
CACHE_ENTRIES = 256          # answers kept per worker process
CACHE_BYTES = 32 * 1024 * 1024   # bytes kept per worker process (8 workers: 256 MB at most)
CACHE_ITEM_MAX = 4 * 1024 * 1024  # an answer larger than this is not kept (the largest is about 2.7 MB)
CACHE_CONTROL = "public, max-age=300"

CLAIMS_SQL = ("SELECT patent_id, claim_sequence, claim_text, claim_dependent, claim_number, exemplary FROM txt.g_claims "
              "WHERE patent_id = ? ORDER BY claim_sequence LIMIT ?")
PATENT_SQL = "SELECT 1 FROM main.patents WHERE patent_id = ?"


class _Interrupted(Exception):
    pass


class AnswerCache:
    """A bounded LRU of encoded answers (bytes), by entries and by bytes, safe for threads."""

    def __init__(self, entries=CACHE_ENTRIES, max_bytes=CACHE_BYTES, item_max=CACHE_ITEM_MAX):
        self.entries, self.max_bytes, self.item_max = entries, max_bytes, item_max
        self._d = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key, value):
        if len(value) > self.item_max:
            return
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self._bytes -= len(old)
            self._d[key] = value
            self._bytes += len(value)
            while self._d and (len(self._d) > self.entries or self._bytes > self.max_bytes):
                _, gone = self._d.popitem(last=False)
                self._bytes -= len(gone)

    def clear(self):
        with self._lock:
            self._d.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            return {"entries": len(self._d), "bytes": self._bytes}


CACHE = AnswerCache()


def _read(con, sql, params, seconds=READ_SECONDS):
    """One statement under its own deadline (a progress handler, as API.search_sqlite runs every search statement)."""
    deadline = time.monotonic() + seconds
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100000)
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            raise _Interrupted() from None
        raise
    finally:
        con.set_progress_handler(None, 0)


def _text(v):
    v = plain(v)
    if v is None:
        return ""
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)


def claim_rows(rows):
    """txt.g_claims rows as claimgraph's ClaimRow, every field a string as in the g_claims TSV files."""
    return [claimgraph.ClaimRow(_text(p), _text(seq), _text(t), _text(dep), _text(num), _text(ex))
            for p, seq, t, dep, num, ex in rows]


def encode(body):
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def tree_answer(pid, rows, data_version, text_version, as_of):
    """The answer for one patent's rows: claimgraph's patent object with the version fields, or (None, problems)."""
    graph = claimgraph.build_patent(pid, claim_rows(rows))
    problems = claimgraph.validate_patent(graph)
    if problems:
        return None, problems
    body = {"error": False, "patent_id": graph["patent_id"], "patent_unit_id": graph["patent_unit_id"],
            "structure_version": graph["structure_version"], "generator": GENERATOR, "data_version": data_version,
            "text_data_version": text_version, "as_of": as_of}
    for k, v in graph.items():
        if k not in body:
            body[k] = v
    return body, []


def _answer(data, text_version, cache_state, ms):
    resp = HttpResponse(data, content_type="application/json")
    resp["Cache-Control"] = CACHE_CONTROL
    if text_version:
        resp["X-Text-Data-Version"] = text_version
    resp["Server-Timing"] = f'claimtree;desc="{cache_state}";dur={ms:.1f}'
    return resp


class ClaimTreeView(APIView):
    """GET /api/v1/structure/claims/<patent_id>/ (gate S0.2)."""

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
        key = (pid, data_version, text_version, claimgraph.STRUCTURE_VERSION)
        hit = CACHE.get(key)
        if hit is not None:
            return _answer(hit, text_version, "hit", (time.perf_counter() - t0) * 1000)
        try:
            rows = _read(con, CLAIMS_SQL, (pid, MAX_CLAIMS + 1))
            known = bool(rows) or bool(_read(con, PATENT_SQL, (pid,)))
        except _Interrupted:
            return _error(f"Claim tree timed out: reading the claims of {pid} took over {READ_SECONDS:.0f} s", "ERR_ES",
                          http.HTTP_500_INTERNAL_SERVER_ERROR)
        if not rows:
            if not known:
                return _error(f"Unknown patent_id: {pid!r} is not in the data in service ({data_version})", "NOT_FOUND",
                              http.HTTP_404_NOT_FOUND)
            return _error(f"No claim text for patent_id {pid!r}: the long-text file in service ({text_version}) has no claims "
                          f"for it; it holds the claims of grants up to its last grant date (GET /api/v1/meta/health/)", "NOT_FOUND",
                          http.HTTP_404_NOT_FOUND)
        if len(rows) > MAX_CLAIMS:
            return _error(f"The claims of {pid} are over this endpoint's work cap ({MAX_CLAIMS} claim rows)", "ERR_ES",
                          http.HTTP_500_INTERNAL_SERVER_ERROR)
        cat = searcher.catalog_facts() or {}
        as_of = cat.get("as_of") or _as_of(searcher)
        rows = [(p, seq, _text(t), dep, num, ex) for p, seq, t, dep, num, ex in rows]
        chars = sum(len(r[2]) for r in rows)
        if chars > MAX_TEXT_CHARS:
            return _error(f"The claims of {pid} are over this endpoint's work cap ({chars:,} characters; at most {MAX_TEXT_CHARS:,})",
                          "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
        body, problems = tree_answer(pid, rows, data_version, text_version, as_of)
        if body is None:  # an offset that does not hold must not leave the process
            logger.error("claim tree of %s failed its check: %s", pid, problems[:3])
            return _error(f"The claim tree of {pid} failed its offset check; nothing was served", "ERR_ES",
                          http.HTTP_500_INTERNAL_SERVER_ERROR)
        data = encode(body)
        CACHE.put(key, data)
        return _answer(data, text_version, "miss", (time.perf_counter() - t0) * 1000)
