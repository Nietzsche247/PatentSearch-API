"""PatentRef Structure S1-D (gate S1D.4, release AG, API 1.24.0; experimental): for each patent, the problem it states and the
parts it names, each quoted with its place in the text, and links to other patents whose claims name the same part.

  GET /api/v1/structure/units/<patent_id>/        one patent's units (parts and stated problems) with their recites and
                                                  states relations, built on read from the long-text file
  GET /api/v1/structure/relations/<patent_id>/    rel=names_same_part (default): other patents whose claims name the same
                                                  parts, ranked and paged, from the structure file; rel=recites or
                                                  rel=states: on read, as the units endpoint gives them
  GET /api/v1/structure/locate/                   the text at one locator (patent_id, field, sequence, start, end)
  GET or POST /api/v1/structure/units/            units across patents in the PatentsView grammar (q, f, s, o): q names
                                                  patent_id (a value or a list of up to 100) or part (from the index)

Every answer is s1d's deterministic code (a private package reached through API/lapse_s1d.py) over the published text; no
language model runs in any request path. Units and relations carry s1d_version (the version of the rules, a string), never
structure_version (an integer in the claim tree and the counts). Every answer says "experimental": true.

Keyed and throttled like every /api/v1/ data path (the DRF defaults of lapse_local), counted toward the monthly allowance like
the claim tree. Errors in the upstream shape ({"error": true}, X-Status-Reason, X-Status-Reason-Code): 400 ERR_Q, 404 NOT_FOUND,
500 ERR_ES over the claim tree's work caps or a read past its time, 501 ERR_NOT_IMPLEMENTED when no long-text file is in
service, and 501 ERR_NO_STRUCTURE_FILE for names_same_part and the units query while no structure file is attached. The
structure file (settings.LAPSE_STRUCTURE_PATH) is opened read only and never written, copied or moved.

Two patents the corpus build could not read answer cleanly: RE31177, one of whose claim rows has no claim_sequence (s1d refuses
such rows, since a locator names its claim row by that number), gives its stated units with no part and says so in "unread";
a patent with neither claim rows nor a brief summary in the long-text file (8419894, 9815822) answers 404 NOT_FOUND, as the claim
tree answers a patent without claim text.
"""
import codecs
import json
import logging
import os
import sqlite3
import threading
import time
import zlib

from django.conf import settings
from django.http import HttpResponse
from rest_framework import status as http
from rest_framework.views import APIView

from API import lapse_s1d as s1d
from API import lapse_structure
from API.lapse_structure import AnswerCache, _Interrupted, _read, _text
from API.lapse_views import _as_of, _error, normalize_pid
from API.search import get_searcher

logger = logging.getLogger(__name__)

MAX_CLAIMS = lapse_structure.MAX_CLAIMS          # the claim tree's work caps: claim rows and characters of claim text
MAX_TEXT_CHARS = lapse_structure.MAX_TEXT_CHARS
READ_SECONDS = lapse_structure.READ_SECONDS      # one read of the long-text file, then interrupted
STATEMENT_SECONDS = 20.0                         # one lookup in the structure file, then interrupted (gate 5.6)
PARTS_MAX = 400                                  # parts per answer; then truncated and omitted
STATED_MAX = 50                                  # stated units of one type per answer
LOCATE_SPAN_MAX = 2000                           # characters one locate call returns at most
DESC_BYTES = 160000                              # a detailed description row is decompressed only to its opening bytes
LINKS_DEFAULT, LINKS_MAX = 20, 100               # names_same_part page size: default and the clamp
UNITS_PAGE_DEFAULT, UNITS_PAGE_MAX = 25, 100     # units query page size: default and the clamp
PATENT_LIST_MAX = 100                            # patent_id values one units query may name
QUERY_BUILDS_MAX = 10                            # patents a units query builds on read for their parts, per request
CACHE_CONTROL = "public, max-age=300"
CACHE = AnswerCache()                            # encoded answers of the units endpoint, per worker, as the claim tree's
UNIT_FIELDS = ("unit_id", "type", "part", "text", "term_id", "locator", "cue", "section", "also", "key_withheld", "s1d_version")
ALWAYS = ("unit_id", "type", "locator")
QUERY_FIELDS = ("patent_id", "unit_id", "type", "part", "cue", "section", "field", "text", "locator")
QUERY_FILTERS = ("patent_id", "part", "type", "cue", "section")
SORTABLE = ("patent_id", "unit_id", "type", "part", "cue", "section")
RELS = ("names_same_part", "recites", "states")
UNREAD_REASON = ("a claim row of this patent has no claim_sequence; S1-D reads a patent's claims only when every claim row has "
                 "one, so no claim is read and no part is given")

CLAIMS_SQL = lapse_structure.CLAIMS_SQL
PATENT_SQL = lapse_structure.PATENT_SQL
SUMMARY_SQL = "SELECT summary_text FROM txt.g_brf_sum_texts WHERE patent_id = ? ORDER BY rowid"
DESC_SQL = "SELECT description_text FROM txt.g_detail_desc_texts WHERE patent_id = ? ORDER BY rowid"
CLAIM_AT_SQL = "SELECT claim_text FROM txt.g_claims WHERE patent_id = ? AND claim_sequence = ?"
SUMMARY_AT_SQL = "SELECT summary_text FROM txt.g_brf_sum_texts WHERE patent_id = ? ORDER BY rowid LIMIT 1 OFFSET ?"
DESC_AT_SQL = "SELECT description_text FROM txt.g_detail_desc_texts WHERE patent_id = ? ORDER BY rowid LIMIT 1 OFFSET ?"


class _Refused(Exception):
    """An answer other than 200, as (reason, code, status)."""

    def __init__(self, reason, code="ERR_Q", status=http.HTTP_400_BAD_REQUEST):
        super().__init__(reason)
        self.reason, self.code, self.status = reason, code, status


def _refuse(reason, code="ERR_Q", status=http.HTTP_400_BAD_REQUEST):
    raise _Refused(reason, code, status)


# ------------------------------------------------------------------ the structure file

_local = threading.local()


def structure_path():
    return getattr(settings, "LAPSE_STRUCTURE_PATH", None) or os.environ.get("LAPSE_STRUCTURE_PATH", "")


def reset():
    """Forget this thread's structure connection and the kept answers (the tests swap files)."""
    con = getattr(_local, "con", None)
    if con is not None:
        try:
            con.close()
        except sqlite3.Error:
            pass
    _local.__dict__.clear()
    CACHE.clear()
    _gap.clear()


def df_share():
    """The share of the patents in the file above which a part takes no part in links: 0.2 percent (s1d's DF_SHARE), or
    0.02 percent (DF_SHARE_FALLBACK) when settings.LAPSE_S1D_DF_SHARE names it; nothing else."""
    v = getattr(settings, "LAPSE_S1D_DF_SHARE", None)
    return s1d.DF_SHARE_FALLBACK if v is not None and float(v) == s1d.DF_SHARE_FALLBACK else s1d.DF_SHARE


def structure():
    """(connection, facts) of the structure file for this thread, or (None, why). Opened read only once per thread and file;
    a file of another s1d version than the one served is not used."""
    path = structure_path()
    real = os.path.realpath(path) if path and os.path.isfile(path) else None
    if not real:
        return None, "The structure file is not attached on this server."
    if getattr(_local, "real", None) == real and getattr(_local, "con", None) is not None:
        return _local.con, _local.facts
    old = getattr(_local, "con", None)
    if old is not None:
        try:
            old.close()
        except sqlite3.Error:
            pass
        _local.con = None
    try:
        con = s1d.connect_structure(real)
        con.execute("PRAGMA cache_size=-65536")
        meta = dict(con.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as e:
        logger.error("structure file %s: %s", real, e)
        return None, "The structure file is not attached on this server."
    if meta.get("structure_version") != s1d.S1D_VERSION:
        con.close()
        return None, (f"The structure file is not attached on this server: the file holds {meta.get('structure_version')!r}, "
                      f"and the API serves {s1d.S1D_VERSION}.")
    patents = int(meta.get("patents") or 0)
    share = df_share()
    facts = {"text_data_version": meta.get("text_data_version") or "", "structure_version": meta["structure_version"],
             "patents": patents, "share": share, "df_max": s1d.df_max_for(patents, share),
             "structure_data_version": f"{meta.get('text_data_version') or ''}_{meta['structure_version']}",
             "text_grants_through": meta.get("text_grants_through") or None}   # release AH: a later structure file may say it
    _local.con, _local.real, _local.facts = con, real, facts
    return con, facts


def _need_structure():
    con, facts = structure()
    if con is None:
        _refuse(facts, "ERR_NO_STRUCTURE_FILE", http.HTTP_501_NOT_IMPLEMENTED)
    return con, facts


_gap = {}   # (structure text data version, text data version in service) -> grants lacking, per worker


def grants_lacking(structure_text, text_version, grants_json, through=None):
    """How many grants of the long-text file in service the structure file has no rows for (release AH): the patents of the
    text file's weekly grants (its _lapse_build grants list: each week's issue_date and patents) issued after the newest week
    of the text the structure file was built from: its meta's text_grants_through when it has one, else the release part of
    that text's data version (a weekly text build is stamped with its newest issue date). 0 when the two texts are the same;
    None when it cannot be told (no grants list)."""
    if not structure_text or not text_version:
        return None
    if structure_text == text_version:
        return 0
    through = str(through or "").replace("-", "") or str(structure_text).split(".", 1)[0]
    try:
        weeks = json.loads(grants_json) if grants_json else None
    except ValueError:
        weeks = None
    if not isinstance(weeks, list) or not through.isdigit():
        return None
    n = 0
    for w in weeks:
        if isinstance(w, dict) and str(w.get("issue_date") or "").replace("-", "") > through:
            try:
                n += int(w.get("patents") or 0)
            except (TypeError, ValueError):
                return None
    return n


def structure_gap():
    """(the text data version the structure file was built from, how many grants of the text in service it lacks) for the
    health route (release AH, PatentRef 1.25.0). Read from the two files' own bookkeeping (the structure file's meta, the text
    file's _lapse_build), never from a scan; the count is kept per worker for each pair of versions. (None, None) without a
    structure file or a text file; never raises: the health route answers on its own checks."""
    try:
        con, facts = structure()
        if con is None:
            return None, None
        stv = facts.get("text_data_version") or None
        searcher = get_searcher()
        tv = searcher.text_data_version()
        if not tv:
            return stv, None
        key = (stv, tv)
        if key not in _gap:
            row = None
            if stv != tv:
                row = searcher.connection().execute("SELECT v FROM txt._lapse_build WHERE k = 'grants'").fetchone()
            _gap[key] = grants_lacking(stv, tv, row[0] if row else None, facts.get("text_grants_through"))
        return stv, _gap[key]
    except Exception:  # noqa: BLE001 - the health route never fails on this pair of fields
        logger.exception("structure gap not read")
        return None, None


class _Deadline:
    """A progress handler on the structure connection for one lookup, as every search statement has one (gate 5.6)."""

    def __init__(self, con, seconds=STATEMENT_SECONDS):
        self.con, self.seconds = con, seconds

    def __enter__(self):
        end = time.monotonic() + self.seconds
        self.con.set_progress_handler(lambda: 1 if time.monotonic() > end else 0, 100000)
        return self

    def __exit__(self, kind, err, tb):
        self.con.set_progress_handler(None, 0)
        if kind is sqlite3.OperationalError and "interrupted" in str(err):
            raise _Refused(f"The structure lookup took over {self.seconds:.0f} s", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
        return False


# ------------------------------------------------------------------ the long-text file

def _desc(v):
    """A detailed description row as text, decompressed only to its opening DESC_BYTES bytes (s1d reads the opening 40,000
    characters of a row); an incomplete character at the cut is left out, so every offset before it holds."""
    if isinstance(v, (bytes, memoryview)):
        b = bytes(v)
        try:
            raw = zlib.decompressobj().decompress(b, DESC_BYTES)
        except zlib.error:
            raw = b[:DESC_BYTES]
        return codecs.getincrementaldecoder("utf-8")(errors="replace").decode(raw, final=False)
    return "" if v is None else str(v)


def _text_or_none(v):
    t = _text(v)
    return t if t else None


class Texts:
    """One request's reads of the long-text file (through the search connection, where it is attached as txt), each under the
    claim tree's time per read, and the claim texts of other patents kept for the request."""

    def __init__(self, con):
        self.con = con
        self._claims = {}

    def read(self, sql, params):
        try:
            return _read(self.con, sql, params, READ_SECONDS)
        except _Interrupted:
            _refuse(f"Reading the long-text file took over {READ_SECONDS:.0f} s", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)

    def patent(self, pid):
        """(claim rows, summaries, descriptions) of one patent, as s1d's batch build reads them: every claim row field a
        string (a missing claim_sequence is '', which s1d refuses), the summaries and the descriptions in row order."""
        rows = self.read(CLAIMS_SQL, (pid, MAX_CLAIMS + 1))
        if len(rows) > MAX_CLAIMS:
            _refuse(f"The claims of {pid} are over this endpoint's work cap ({MAX_CLAIMS} claim rows)", "ERR_ES",
                    http.HTTP_500_INTERNAL_SERVER_ERROR)
        rows = [tuple(_text(x) for x in r) for r in rows]
        chars = sum(len(r[2]) for r in rows)
        if chars > MAX_TEXT_CHARS:
            _refuse(f"The claims of {pid} are over this endpoint's work cap ({chars:,} characters; at most {MAX_TEXT_CHARS:,})",
                    "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
        sums = [_text_or_none(r[0]) for r in self.read(SUMMARY_SQL, (pid,))]
        descs = [_desc(r[0]) or None for r in self.read(DESC_SQL, (pid,))]
        return rows, sums, descs

    def known(self, pid):
        return bool(self.read(PATENT_SQL, (pid,)))

    def claim(self, pid, sequence):
        k = (pid, sequence)
        if k not in self._claims:
            r = self.read(CLAIM_AT_SQL, (pid, sequence))
            self._claims[k] = _text(r[0][0]) if r else None
        return self._claims[k]

    def at(self, pid, field, sequence):
        """The whole text a locator reads, or None."""
        if field == "claims":
            return self.claim(pid, sequence)
        if field == "brief_summary":
            r = self.read(SUMMARY_AT_SQL, (pid, sequence))
            return _text_or_none(r[0][0]) if r else None
        if field == "detail_description":
            r = self.read(DESC_AT_SQL, (pid, sequence))
            return (_desc(r[0][0]) or None) if r else None
        return None


def _versions(searcher):
    data_version = searcher.data_version() or ""
    text_version = searcher.text_data_version()
    if not text_version:
        _refuse("Endpoint not implemented: the long-text data sets are not in this snapshot; no long-text file is in service "
                "(GET /api/v1/meta/health/ shows text_data_version)", "ERR_NOT_IMPLEMENTED", http.HTTP_501_NOT_IMPLEMENTED)
    cat = searcher.catalog_facts() or {}
    return data_version, text_version, cat.get("as_of") or _as_of(searcher)


def _pid(raw):
    pid = normalize_pid(raw)
    if not pid:
        _refuse(f"Not a patent number: {raw!r}")
    return pid


# ------------------------------------------------------------------ one patent on read

def _rename(item):
    """s1d's structure_version as s1d_version (the claim tree's structure_version is an integer), and a shared part's
    patents_naming as patents_with_key, the words of the docs pages."""
    out = {}
    for k, v in item.items():
        if k == "structure_version":
            out["s1d_version"] = v
        elif k == "shared_parts":
            out[k] = [{("patents_with_key" if kk == "patents_naming" else kk): vv for kk, vv in sp.items()} for sp in v]
        else:
            out[k] = v
    return out


def on_read(texts, pid):
    """One patent's s1d answer on read, as a dict: s1d's units, relations, counts, none_found, texts_read and left_out, plus
    unread (what could not be read and why). 404 for a patent the data in service does not have and for one with neither claim
    rows nor a brief summary in the long-text file (s1d's batch build gives such a patent no units)."""
    rows, sums, descs = texts.patent(pid)
    if not rows and not any(sums):
        if not texts.known(pid):
            _refuse(f"Unknown patent_id: {pid!r} is not in the data in service", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        _refuse(f"No claim text and no brief summary for patent_id {pid!r}: the long-text file in service has neither for it, "
                f"and S1-D reads both", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
    try:
        ans = s1d.build(pid, rows, sums, descs)
        ans["unread"] = []
        return ans
    except ValueError as e:
        if "claim_sequence" not in str(e):
            raise
    # a claim row without claim_sequence (RE31177): the stated units alone, through the same quote check
    units, rels = s1d.stated_units(pid, sums, descs, part_keys=[])
    own = s1d.texts({"claims": []}, sums, descs)
    units, rels, problems = s1d.check(units, rels, lambda p, f, q: own(f, q) if p == pid else None)
    counts = {t: 0 for t in s1d.UNIT_TYPES}
    for u in units:
        counts[u["type"]] += 1
    counts.update({"recites": 0, "states": len(rels), "names_same_part": 0})
    return {"patent_id": pid, "units": units, "relations": rels, "counts": counts,
            "none_found": [t for t in s1d.STATED_TYPES if counts[t] == 0],
            "texts_read": {"claims": 0, "brief_summary": sum(1 for t in sums if t), "detail_description": sum(1 for t in descs if t)},
            "left_out": len(problems), "unread": [{"field": "claims", "claim_rows": len(rows), "reason": UNREAD_REASON}]}


def _types(raw):
    if raw is None or raw == "":
        return s1d.UNIT_TYPES
    got = [t.strip() for t in str(raw).split(",") if t.strip()]
    bad = [t for t in got if t not in s1d.UNIT_TYPES]
    if bad or not got:
        _refuse(f"'types' is a comma list of {', '.join(s1d.UNIT_TYPES)} (not {', '.join(bad) or repr(raw)})")
    return tuple(t for t in s1d.UNIT_TYPES if t in got)


def _json_param(raw, name, kind):
    if raw is None or raw == "":
        return None
    if isinstance(raw, kind):
        return raw
    try:
        v = json.loads(raw)
    except (TypeError, ValueError):
        _refuse(f"'{name}' could not be read as JSON")
    if not isinstance(v, kind):
        _refuse(f"'{name}' must be a JSON {'array' if kind is list else 'object'}")
    return v


def _unit_fields(raw):
    f = _json_param(raw, "f", list)
    if f is None:
        return None
    bad = [x for x in f if x not in UNIT_FIELDS]
    if bad:
        _refuse(f"'f' names unknown unit fields: {', '.join(map(str, bad))} (fields: {', '.join(UNIT_FIELDS)})")
    return tuple(dict.fromkeys(list(ALWAYS) + list(f)))


def _keep(unit, fields):
    return {k: v for k, v in unit.items() if k in fields} if fields else unit


def units_body(ans, versions, types, with_relations, fields):
    data_version, text_version, as_of = versions
    pid = ans["patent_id"]
    units = [u for u in ans["units"] if u["type"] in types]
    parts = [u for u in units if u["type"] == "Component"]
    omitted = {}
    if len(parts) > PARTS_MAX:
        omitted["Component"] = len(parts) - PARTS_MAX
    by_type = {}
    kept = []
    for u in units:
        n = by_type[u["type"]] = by_type.get(u["type"], 0) + 1
        if (u["type"] == "Component" and n > PARTS_MAX) or (u["type"] != "Component" and n > STATED_MAX):
            if u["type"] != "Component":
                omitted[u["type"]] = omitted.get(u["type"], 0) + 1
            continue
        kept.append(u)
    ids = {u["unit_id"] for u in kept}
    rels = []
    if with_relations:
        want = (("recites",) if "Component" in types else ()) + (("states",) if "StatedProblem" in types else ())
        rels = [r for r in ans["relations"] if r["rel"] in want and r["dst"] in ids]
    counts = {t: ans["counts"].get(t, 0) for t in types}
    if "Component" in types:
        counts["recites"] = ans["counts"].get("recites", 0)
    if "StatedProblem" in types:
        counts["states"] = ans["counts"].get("states", 0)
    body = {"error": False, "patent_id": pid, "patent_unit_id": f"US{pid}", "s1d_version": s1d.S1D_VERSION,
            "generator": s1d.GENERATOR, "data_version": data_version, "text_data_version": text_version, "as_of": as_of,
            "experimental": True,
            "units": [_keep(_rename(u), fields) for u in kept], "relations": [_rename(r) for r in rels], "counts": counts,
            "none_found": [t for t in ans["none_found"] if t in types], "texts_read": ans["texts_read"],
            "left_out": ans["left_out"], "unread": ans["unread"]}
    if omitted:
        body["truncated"] = True
        body["omitted"] = omitted
    return body


def _answer(data, text_version, state, t0, structure_version=None):
    resp = HttpResponse(data, content_type="application/json")
    resp["Cache-Control"] = CACHE_CONTROL
    if text_version:
        resp["X-Text-Data-Version"] = text_version
    resp["X-S1D-Version"] = s1d.S1D_VERSION
    if structure_version:
        resp["X-Structure-Data-Version"] = structure_version
    resp["Server-Timing"] = f's1d;desc="{state}";dur={(time.perf_counter() - t0) * 1000:.1f}'
    return resp


def encode(body):
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _guard(fn):
    """The view body with its refusals answered in the upstream shape and an unexpected failure as 500 ERR_ES (logged)."""
    def run(self, request, *a, **kw):
        try:
            return fn(self, request, *a, **kw)
        except _Refused as r:
            return _error(r.reason, r.code, r.status)
        except Exception:  # noqa: BLE001 - nothing half-made leaves the process
            logger.exception("S1-D answer failed")
            return _error("The S1-D answer could not be made; nothing was served", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
    return run


class UnitsView(APIView):
    """GET /api/v1/structure/units/<patent_id>/?types=&relations=&f= (gate S1D.4, 1.1)."""

    @_guard
    def get(self, request, pk, format=None):
        t0 = time.perf_counter()
        pid = _pid(pk)
        types = _types(request.GET.get("types"))
        rel = request.GET.get("relations", "all")
        if rel not in ("all", "none"):
            _refuse("'relations' is all or none")
        fields = _unit_fields(request.GET.get("f"))
        searcher = get_searcher()
        con = searcher.connection()
        versions = _versions(searcher)
        key = (pid, versions[0], versions[1], s1d.S1D_VERSION, types, rel, fields)
        hit = CACHE.get(key)
        if hit is not None:
            return _answer(hit, versions[1], "hit", t0)
        ans = on_read(Texts(con), pid)
        data = encode(units_body(ans, versions, types, rel == "all", fields))
        CACHE.put(key, data)
        return _answer(data, versions[1], "miss", t0)


# ------------------------------------------------------------------ names_same_part, recites, states

def _page(raw, default, top):
    o = _json_param(raw, "o", dict) or {}
    bad = [k for k in o if k not in ("size", "after")]
    if bad:
        _refuse(f"'o' takes size and after (not {', '.join(map(str, bad))})")
    size = o.get("size", default)
    if not isinstance(size, int) or isinstance(size, bool) or size < 1:
        _refuse("'o.size' must be a whole number from 1")
    after = o.get("after")
    if after is not None and not isinstance(after, list):
        _refuse("'o.after' must be a JSON array (the last row's sort values)")
    return min(size, top), after


def link_cursor(r):
    """A link's sort values as o.after takes them: [shared_count, patents_with_key_total, patent_number, rank]."""
    return [r["shared_count"], sum(sp["patents_with_key"] for sp in r["shared_parts"]), r["dst"][2:], r["rank"]]


def same_part_links(texts, st_con, facts, pid, part_units, size, after):
    """(page, stats, next_after, left_out): s1d's ranked names_same_part links from the structure file, from the rank after
    the cursor; the links before the page are ranked without reading their quotes, the page's quotes are read and checked."""
    skip = 0
    if after is not None:
        if len(after) != 4 or not isinstance(after[3], int) or isinstance(after[3], bool) or after[3] < 1:
            _refuse("'o.after' is the last link's [shared_count, patents_with_key_total, patent_number, rank]")
        skip = after[3]
    seen = set()

    def quote_of(other, loc):
        seen.add(other)   # s1d reads the quotes link by link in its rank order
        if len(seen) <= skip:
            return ""   # a link before this page: ranked, its quote not read
        t = texts.claim(other, loc["sequence"])
        return t[loc["start"]:loc["end"]] if t else None

    with _Deadline(st_con):
        rels, stats = s1d.links_from_index(st_con, pid, part_units, quote_of, cap=skip + size, df_max=facts["df_max"])
    if after is not None:
        if len(rels) < skip:
            _refuse("'o.after' names a rank past the links of this patent")
        prev = rels[skip - 1]
        if prev["shared_count"] != after[0] or prev["dst"][2:] != str(after[2]):
            _refuse("'o.after' does not match the links of this patent (the data in service may have changed): ask again "
                    "without after")
    page = [dict(r, rank=skip + 1 + i) for i, r in enumerate(rels[skip:])]   # the rank in s1d's order, kept through the check

    def text_of(p, field, sequence):
        return texts.claim(p, sequence) if field == "claims" else None

    _, kept, problems = s1d.check([], page, text_of)
    out = [_rename(r) for r in kept]
    more = stats.get("candidates", 0) > skip + len(page)
    stats = dict(stats, df_max=facts["df_max"], share=facts["share"])
    return out, stats, (link_cursor(out[-1]) if out and more else None), len(problems)


class RelationsView(APIView):
    """GET /api/v1/structure/relations/<patent_id>/?rel=names_same_part|recites|states&o={"size":20,"after":[...]}
    (gate S1D.4, 1.2)."""

    @_guard
    def get(self, request, pk, format=None):
        t0 = time.perf_counter()
        pid = _pid(pk)
        rel = request.GET.get("rel", "names_same_part")
        if rel not in RELS:
            _refuse(f"'rel' is one of {', '.join(RELS)}")
        size, after = _page(request.GET.get("o"), LINKS_DEFAULT, LINKS_MAX)
        searcher = get_searcher()
        con = searcher.connection()
        versions = _versions(searcher)
        data_version, text_version, as_of = versions
        st_con = facts = None
        if rel == "names_same_part":
            st_con, facts = _need_structure()
        texts = Texts(con)
        ans = on_read(texts, pid)
        body = {"error": False, "patent_id": pid, "patent_unit_id": f"US{pid}", "s1d_version": s1d.S1D_VERSION,
                "generator": s1d.GENERATOR, "data_version": data_version, "text_data_version": text_version}
        if rel == "names_same_part":
            body["structure_data_version"] = facts["structure_data_version"]
        body.update({"as_of": as_of, "experimental": True, "rel": rel})
        if rel != "names_same_part":
            body["relations"] = [_rename(r) for r in ans["relations"] if r["rel"] == rel]
            body["count"] = len(body["relations"])
            body["unread"] = ans["unread"]
            return _answer(encode(body), text_version, "read", t0)
        parts = [u for u in ans["units"] if u["type"] == "Component"]
        page, stats, nxt, left = same_part_links(texts, st_con, facts, pid, parts, size, after)
        body.update({"relations": page, "count": len(page), "next_after": nxt, "link_lookup": stats, "left_out": left,
                     "unread": ans["unread"]})
        return _answer(encode(body), text_version, "index", t0, facts["structure_data_version"])


# ------------------------------------------------------------------ locate

def _int(raw, name):
    try:
        v = int(str(raw))
    except (TypeError, ValueError):
        _refuse(f"'{name}' must be a whole number")
    if v < 0:
        _refuse(f"'{name}' must be 0 or more")
    return v


class LocateView(APIView):
    """GET /api/v1/structure/locate/?patent_id=&field=&sequence=&start=&end= (gate S1D.4, 1.3)."""

    @_guard
    def get(self, request, format=None):
        t0 = time.perf_counter()
        g = request.GET
        missing = [k for k in ("patent_id", "field", "sequence", "start", "end") if g.get(k) in (None, "")]
        if missing:
            _refuse(f"locate needs {', '.join(missing)}")
        pid = _pid(g.get("patent_id"))
        field = g.get("field")
        if field not in s1d.FIELDS:
            _refuse(f"'field' is one of {', '.join(s1d.FIELDS)}")
        seq, start, end = _int(g.get("sequence"), "sequence"), _int(g.get("start"), "start"), _int(g.get("end"), "end")
        if end <= start:
            _refuse("'end' must be more than 'start'")
        if end - start > LOCATE_SPAN_MAX:
            _refuse(f"one locate call reads at most {LOCATE_SPAN_MAX} characters (end minus start)")
        searcher = get_searcher()
        con = searcher.connection()
        data_version, text_version, as_of = _versions(searcher)
        t = Texts(con).at(pid, field, seq)
        loc = {"patent_id": pid, "field": field, "sequence": seq, "start": start, "end": end}
        if t is None:
            _refuse(f"No text at this locator: the long-text file in service has no {field} row {seq} for patent_id {pid!r}",
                    "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        if end > len(t):
            cut = field == "detail_description" and len(t.encode("utf-8")) >= DESC_BYTES - 4
            _refuse(f"No text at this locator: the {field} row {seq} of patent_id {pid!r} holds {len(t):,} characters"
                    + (f" in its opening {DESC_BYTES:,} bytes, which is what locate reads of a description row" if cut else ""),
                    "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        body = {"error": False, "text": t[start:end], "locator": loc, "s1d_version": s1d.S1D_VERSION,
                "data_version": data_version, "text_data_version": text_version, "as_of": as_of, "experimental": True}
        return _answer(encode(body), text_version, "read", t0)


# ------------------------------------------------------------------ the units query (PatentsView grammar)

def _criteria(q, conds, depth=0):
    """q into {field: set of values}: plain values, _eq, lists (patent_id), _and of criteria, _or of criteria on one field."""
    if depth > 8:
        _refuse("'q' is nested too deep")
    if not isinstance(q, dict) or len(q) != 1:
        _refuse("'q' must be one JSON object with one key at each level, as on /api/v1/patent/")
    (k, v), = q.items()
    if k == "_and":
        if not isinstance(v, list) or not v:
            _refuse("'_and' takes a list of criteria")
        for c in v:
            _criteria(c, conds, depth + 1)
        return conds
    if k == "_or":
        if not isinstance(v, list) or not v:
            _refuse("'_or' takes a list of criteria")
        sub = [_criteria(c, {}, depth + 1) for c in v]
        fields = {f for s in sub for f in s}
        if len(fields) != 1:
            _refuse("'_or' combines criteria on one field here (patent_id, part, type, cue or section)")
        f = fields.pop()
        vals = set().union(*(s[f] for s in sub))
        conds[f] = conds[f] & vals if f in conds else vals
        return conds
    if k not in QUERY_FILTERS:
        if k.startswith("_"):
            _refuse(f"the operator {k} is not offered by the units query (plain values, _eq, _and and _or)")
        _refuse(f"'q' filters by {', '.join(QUERY_FILTERS)} (not {k!r})")
    if isinstance(v, dict):
        if list(v) != ["_eq"]:
            _refuse(f"the operator {', '.join(map(str, v))} is not offered by the units query (plain values, _eq, _and and _or)")
        v = v["_eq"]
    vals = v if isinstance(v, list) else [v]
    if k != "patent_id" and isinstance(v, list) and len(v) > 1:
        _refuse(f"a list of values is offered on patent_id only; use _or for {k}")
    if not vals or any(not isinstance(x, str) or not x.strip() for x in vals):
        _refuse(f"'{k}' takes text values")
    if k == "patent_id":
        if len(vals) > PATENT_LIST_MAX:
            _refuse(f"a units query names at most {PATENT_LIST_MAX} patent_id values")
        vals = [_pid(x) for x in vals]
    elif k == "part":
        vals = [s1d.link_key(" ".join(x.lower().split())) for x in vals]
    elif k == "type":
        bad = [x for x in vals if x not in s1d.UNIT_TYPES]
        if bad:
            _refuse(f"'type' is one of {', '.join(s1d.UNIT_TYPES)}")
    got = set(vals)
    conds[k] = conds[k] & got if k in conds else got
    return conds


TIEBREAK = ("unit_id", False)   # release AH (1.25.0): every order ends with unit_id, so no page ends inside a tie


def _sort(raw):
    """(the order, how many sort values a cursor of the request's own order holds). Release AH: an order that does not name
    unit_id ends with it (ascending), so rows that tie on what s names keep one order and a page never ends between two of
    them; until 1.25.0 a cursor held only the values s named, and the next page left out the rest of its last patent's rows
    when s named patent_id alone."""
    s = _json_param(raw, "s", list)
    if s is None:
        return [("patent_id", False), TIEBREAK], 2
    out = []
    for item in s:
        if not isinstance(item, dict) or len(item) != 1:
            _refuse("'s' is a JSON array of {\"field\": \"asc\"|\"desc\"}")
        (f, d), = item.items()
        if f in ("text", "locator", "field"):
            _refuse(f"'s' cannot sort by {f}")
        if f not in SORTABLE or d not in ("asc", "desc"):
            _refuse(f"'s' sorts by {', '.join(SORTABLE)}, asc or desc")
        out.append((f, d == "desc"))
    if not out or out[0][0] != "patent_id":
        _refuse("a units query sorts by patent_id (asc or desc) before unit_id, type, part, cue or section")
    named = len(out)
    if all(f != "unit_id" for f, _ in out):
        out.append(TIEBREAK)
    return out, named


def _row(u, pid):
    loc = u["locator"]
    return {"patent_id": pid, "unit_id": u.get("unit_id"), "type": u["type"], "part": u.get("part"), "cue": u.get("cue"),
            "section": u.get("section"), "field": loc["field"], "text": u["text"], "locator": loc}


def _key(row, sort):
    return [("" if row.get(f) is None else str(row.get(f))) for f, _ in sort]


def _after_ok(row, sort, after):
    """True when row sorts after the cursor in the order of sort."""
    a = [("" if x is None else str(x)) for x in after]
    k = _key(row, sort)
    for (f, desc), x, y in zip(sort, k, a):
        if x != y:
            return (x < y) if desc else (x > y)
    return False


def _order(rows, sort):
    for f, desc in reversed(sort):
        rows.sort(key=lambda r, f=f: "" if r.get(f) is None else str(r.get(f)), reverse=desc)
    return rows


class UnitsQueryView(APIView):
    """GET or POST /api/v1/structure/units/?q=&f=&s=&o= (gate S1D.4, 1.4)."""

    def get(self, request, format=None):
        return self.run(request, request.GET)

    def post(self, request, format=None):
        data = request.data if isinstance(request.data, dict) else {}
        return self.run(request, data)

    @_guard
    def run(self, request, params):
        t0 = time.perf_counter()
        q = _json_param(params.get("q"), "q", dict)
        if q is None:
            _refuse("'q' is required: name patent_id (a value or a list of up to 100) or part")
        conds = _criteria(q, {})
        if "patent_id" not in conds and "part" not in conds:
            _refuse("'q' must name patent_id (a value or a list of up to 100) or part (an exact part, from the index)")
        f = _json_param(params.get("f"), "f", list)
        if f is not None:
            bad = [x for x in f if x not in QUERY_FIELDS]
            if bad:
                _refuse(f"'f' names unknown fields: {', '.join(map(str, bad))} (fields: {', '.join(QUERY_FIELDS)})")
        sort, named = _sort(params.get("s"))
        size, after = _page(params.get("o"), UNITS_PAGE_DEFAULT, UNITS_PAGE_MAX)
        if after is not None and len(after) not in (len(sort), named):
            _refuse("'o.after' holds the last row's sort values: next_after of the page before, as many values as 's' names "
                    "and then the row's unit_id when 's' does not name it")
        if after is not None and len(after) < len(sort):
            after = list(after) + [None] * (len(sort) - len(after))   # a cursor made before 1.25.0: its last patent's rows again
        searcher = get_searcher()
        con = searcher.connection()
        data_version, text_version, as_of = _versions(searcher)
        st_con, facts = _need_structure()
        texts = Texts(con)
        types = conds.get("type", set(s1d.UNIT_TYPES))
        if "part" in conds:
            rows, total, exact, budget = self._by_part(st_con, texts, conds, types, sort, size, after)
        else:
            rows, total, exact, budget = self._by_patent(st_con, texts, conds, types, sort, size, after)
        keep = f or list(QUERY_FIELDS)
        out = [{k: r[k] for k in keep if k in r} for r in rows]
        body = {"error": False, "units": out, "count": len(out), "total_hits": total, "total_hits_exact": exact,
                "budget_reached": budget, "next_after": _key(rows[-1], sort) if rows and len(rows) == size else None,
                "s1d_version": s1d.S1D_VERSION, "generator": s1d.GENERATOR, "data_version": data_version,
                "text_data_version": text_version, "structure_data_version": facts["structure_data_version"], "as_of": as_of,
                "experimental": True}
        return _answer(encode(body), text_version, "query", t0, facts["structure_data_version"])

    @staticmethod
    def _by_part(st_con, texts, conds, types, sort, size, after):
        """(rows, total_hits, exact, budget_reached): Component units of one part key, from the index, one row per patent
        naming it, at the place of its lowest claim that introduces the part, in patent order; at most POSTINGS_BUDGET index
        rows read per request. Without a patent_id filter total_hits is the key's patent count (exact); with one, the
        matches among the rows read."""
        if len(conds["part"]) != 1:
            _refuse("a units query names one part")
        if "Component" not in types or "cue" in conds or "section" in conds:
            return [], 0, True, False
        key = next(iter(conds["part"]))
        desc = sort[0][1]
        with _Deadline(st_con):
            r = st_con.execute("SELECT n, df FROM part_keys WHERE key = ?", (key,)).fetchone()
            if not r:
                return [], 0, True, False
            key_n, df = r
            start = None
            if after is not None:
                got = st_con.execute("SELECT n FROM patents WHERE patent_id = ?", (str(after[0]),)).fetchone()
                if not got:
                    _refuse("'o.after' names a patent_id the structure file does not hold")
                start = got[0]
            sql = ("SELECT p.patent_id, x.sequence, x.start, x.\"end\" FROM part_postings x JOIN patents p ON p.n = x.patent_n "
                   "WHERE x.key_n = ?" + ("" if start is None else (" AND x.patent_n < ?" if desc else " AND x.patent_n > ?"))
                   + (" ORDER BY x.patent_n DESC" if desc else " ORDER BY x.patent_n") + " LIMIT ?")
            args = (key_n,) + (() if start is None else (start,)) + (s1d.POSTINGS_BUDGET + 1,)
            wanted = conds.get("patent_id")
            rows, read, matched, budget = [], 0, 0, False
            for pid, seq, s, e in st_con.execute(sql, args):
                read += 1
                if read > s1d.POSTINGS_BUDGET:
                    budget = True
                    break
                if wanted is not None and pid not in wanted:
                    continue
                matched += 1
                if len(rows) >= size:
                    continue   # counted, not read
                t = texts.claim(pid, seq)
                if not t or not (0 <= s < e <= len(t)):
                    continue   # the text moved since the file was built: never served
                loc = {"patent_id": pid, "field": "claims", "sequence": seq, "start": s, "end": e}
                rows.append({"patent_id": pid, "unit_id": None, "type": "Component", "part": key, "cue": None, "section": None,
                             "field": "claims", "text": t[s:e], "locator": loc})
                if wanted is None and len(rows) >= size:
                    break
        if wanted is None:
            return rows, df, True, False
        return rows, matched, not budget, budget

    @staticmethod
    def _by_patent(st_con, texts, conds, types, sort, size, after):
        """(rows, total_hits, exact, budget_reached): the units of up to 100 patents in patent_id order: StatedProblem units
        from the structure file (each quote read from the long-text file and kept only when it hashes to the stored digest),
        Component units built on read, at most QUERY_BUILDS_MAX patents built per request and none once the page is full (then
        total_hits counts the patents read so far and is not exact)."""
        desc = sort[0][1]
        pids = sorted(conds["patent_id"], reverse=desc)
        if after is not None:
            a = str(after[0])
            pids = [p for p in pids if (p <= a if desc else p >= a)]
        rows, total, built, budget, exact = [], 0, 0, False, True
        parts = "Component" in types and "cue" not in conds and "section" not in conds
        for pid in pids:
            got = []
            if parts:
                if len(rows) >= size:
                    exact = False   # the page is full: the patents after it are not built
                    break
                if built >= QUERY_BUILDS_MAX:
                    budget, exact = True, False
                    break
                built += 1
                try:
                    ans = on_read(texts, pid)
                except _Refused as r:
                    if r.status == http.HTTP_404_NOT_FOUND:
                        continue
                    raise
                got += [_row(u, pid) for u in ans["units"] if u["type"] == "Component"]
            if "StatedProblem" in types:
                def text_of(field, sequence, pid=pid):
                    return texts.at(pid, field, sequence)
                with _Deadline(st_con):
                    stated, _rels, _problems = s1d.stated_from_store(st_con, pid, text_of)
                got += [_row(u, pid) for u in stated if u["type"] in types
                        and ("cue" not in conds or u.get("cue") in conds["cue"])
                        and ("section" not in conds or u.get("section") in conds["section"])]
            got = _order(got, sort)
            for r in got:
                if after is not None and r["patent_id"] == str(after[0]) and not _after_ok(r, sort, after):
                    continue
                total += 1
                if len(rows) < size:
                    rows.append(r)
        return rows, total, exact, budget
