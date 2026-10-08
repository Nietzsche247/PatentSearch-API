"""SQLite backend for the PatentSearch API (Lapse).

Drop-in for API.search.PatentsViewElasticSearch: same search()/count() signatures and the same
response shape the views read ({"hits": {"hits": [{"_source": ...}]}, "timed_out": False} and
{"count": n}). The query argument is the Elasticsearch DSL that the unchanged API.queryparser emits;
this module translates that DSL to SQL, so the grammar, validation and error strings stay upstream's.

Database layout (built by lapse_tools/build_sample.py):
  <index>                  one row per document, columns named like the API/ES fields
  <index>__<nested path>   child rows, _pid = parent key, _ord = original order
  fts_<table>              FTS5 over the ES "text" fields of <table>
  _lapse_fields            idx, path, field, es_type, keyword_subfield
  _lapse_indices           idx, tbl, key_field
The long-text indices (g_claims, g_brf_sum_texts, g_detail_desc_texts, g_draw_desc_texts; PatentRef 1.6) live
in a second file with the same layout (lapse_tools/build_text.py), ATTACHed read-only as `txt` beside the
snapshot (LAPSE_TEXT_PATH, a symlink resolved once per request like the snapshot); an index is looked up in
main first, then in txt. Their text columns are zlib-compressed BLOBs: `_convert` and the Python matchers
decompress, so a response and a `_text_*` operator see plain text. Their FTS tables are contentless; the one
built with detail=none (descriptions) answers a phrase through an AND prefilter plus the exact Python test.

Semantics emulated (see REPORT.md section 2): match on .keyword / keyword = exact; match on text =
analyzed OR (FTS5); match with operator and/or; match_phrase; terms; range; prefix and wildcard with
case_insensitive; bool filter/must/should/must_not; nested = per-criterion IN (child) subquery;
sort with missing values last; search_after as an expanded keyset tuple comparison; ignore_above 256
on .keyword sub-fields. Value errors are raised as elasticsearch ApiError objects carrying the same
root_cause types ES returns, so API.exceptions maps them to the same status codes and headers.
"""
import datetime as dt
import fnmatch
import logging
import os
import re
import sqlite3
import threading
import time
import zlib

from API.exceptions import SearchTimeoutError  # noqa: F401  (kept for parity with API.search)
from API.lapse_errors import LapseTimeout

logger = logging.getLogger("API")
KEYWORD_IGNORE_ABOVE = 256
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def es_error(err_type, reason, status=400):
    """Build the elasticsearch ApiError that the upstream exception handler knows how to map."""
    import elasticsearch
    from elastic_transport import ApiResponseMeta, HttpHeaders, NodeConfig

    meta = ApiResponseMeta(status=status, http_version="1.1", headers=HttpHeaders(), duration=0.0,
                           node=NodeConfig("http", "sqlite", 0))
    body = {"error": {"root_cause": [{"type": err_type, "reason": reason}], "type": err_type, "reason": reason},
            "status": status}
    cls = elasticsearch.BadRequestError if status == 400 else elasticsearch.ApiError
    return cls(message=err_type, meta=meta, body=body)


def plain(v):
    """A stored value as text: a zlib-compressed BLOB (the long-text file) is decompressed, anything else
    returned as is. A BLOB that is not zlib (no such column exists today) comes back as its bytes."""
    if isinstance(v, (bytes, memoryview)):
        b = bytes(v)
        try:
            return zlib.decompress(b).decode("utf-8", errors="replace")
        except zlib.error:
            return b
    return v


def tokens(text):
    return [t.lower() for t in _TOKEN_RE.findall(str(plain(text)))]


def text_path():
    """The long-text database (PatentRef 1.6): settings.LAPSE_TEXT_PATH or the environment; the box default."""
    try:
        from django.conf import settings

        p = getattr(settings, "LAPSE_TEXT_PATH", None)
    except Exception:  # noqa: BLE001  (no Django settings: tests, tools)
        p = None
    return p or os.environ.get("LAPSE_TEXT_PATH", "/data/api/text_current.db")


def text_real():
    p = text_path()
    return os.path.realpath(p) if p and os.path.exists(p) else None


def attach_text(con, real):
    """ATTACH the long-text file read-only as `txt`. Returns its data_version (or None when not attached)."""
    if not real:
        return None
    try:
        con.execute("ATTACH DATABASE ? AS txt", ("file:" + real.replace("\\", "/") + "?mode=ro",))
        return read_data_version(con, real, fallback_env=False, schema="txt")
    except sqlite3.Error:
        return None


def fts_quote(tok):
    return '"' + tok.replace('"', '""') + '"'


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def wildcard_to_like(pattern):
    out = []
    for ch in pattern:
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        else:
            out.append(like_escape(ch))
    return "".join(out)


# ------------------------------------------------------------------ value coercion (ES field types)
_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?(?:[T ](\d{2}):?(\d{2})?:?(\d{2})?(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?)?$")


def coerce_date(field, v):
    """ES strict_date_optional_time||epoch_millis with the date-math rounding ES applies to query values:
    a partial value spans its whole unit ("2010" is the year, "2010-06" the month), a full day spans that
    day, a value with a time is an instant. Documents are stored at midnight UTC, so the span is returned
    as days: (lo_day, hi_day, lo_is_midnight). A value with a time after midnight has lo_day == hi_day and
    lo_is_midnight False, which no stored document equals."""
    if isinstance(v, bool):
        v = str(v).lower()
    if isinstance(v, (int, float)):
        d = dt.datetime.fromtimestamp(v / 1000.0, tz=dt.timezone.utc)
        day = d.date().isoformat()
        return day, day, d.time() == dt.time(0)
    s = str(v).strip()
    if re.fullmatch(r"-?\d{5,}", s):
        return coerce_date(field, int(s))
    m = _DATE_RE.match(s)
    if m:
        y, mo, d, hh, mi, ss, frac = (m.group(i) for i in range(1, 8))
        try:
            lo = dt.date(int(y), int(mo or 1), int(d or 1))
            if d:
                hi = lo
            elif mo:
                nxt = dt.date(lo.year + (lo.month == 12), lo.month % 12 + 1, 1)
                hi = nxt - dt.timedelta(days=1)
            else:
                hi = dt.date(lo.year, 12, 31)
            midnight = int(hh or 0) == 0 and int(mi or 0) == 0 and int(ss or 0) == 0 and int(frac or 0) == 0
            return lo.isoformat(), hi.isoformat(), midnight
        except ValueError:
            pass
    raise es_error("parse_exception",
                   f"failed to parse date field [{s}] with format [strict_date_optional_time||epoch_millis]")


def has_decimal_part(v):
    try:
        return float(v) % 1 != 0
    except (TypeError, ValueError):
        return False


def coerce_number(v, integral):
    """ES number parsing for term and range values. A fractional value against a long/integer field is
    not an error in ES: term and terms queries match nothing and range bounds are rounded (see
    integral_bound), so callers check has_decimal_part first; here it truncates toward zero like
    Numbers.toLong with coerce."""
    if isinstance(v, bool):
        raise es_error("illegal_argument_exception", f"Can't parse number [{v}]")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise es_error("number_format_exception", f'For input string: "{v}"')
    if integral:
        return int(f)
    return f


def integral_bound(v, op):
    """ES NumberType.LONG range bounds: truncate toward zero, then step a decimal bound inward on the side
    it falls (a positive decimal lower bound moves up, a negative decimal upper bound moves down) and an
    exclusive integral bound by one. Returns (inclusive_op, value)."""
    n = coerce_number(v, True)
    decimal = has_decimal_part(v)
    f = float(v)
    if op in ("gt", "gte"):
        if (not decimal and op == "gt") or (decimal and f > 0):
            n += 1
        return ">=", n
    if (not decimal and op == "lt") or (decimal and f < 0):
        n -= 1
    return "<=", n


def coerce_bool(v):
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, str) and v in ("true", "false"):
        return 1 if v == "true" else 0
    if v == "":
        return 0
    raise es_error("illegal_argument_exception",
                   f"Failed to parse value [{v}] as only [true] or [false] are allowed.")


def coerce_keyword(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v == int(v):
        return str(v)
    return str(v)


INTEGRAL = {"long", "integer", "short", "byte"}
FLOATING = {"double", "float", "half_float", "scaled_float"}


class FieldRef:
    """A resolved query field: table alias + column + ES type information."""

    def __init__(self, alias, table, column, es_type, keyword, has_kw):
        self.alias, self.table, self.column = alias, table, column
        self.es_type, self.keyword, self.has_kw = es_type, keyword, has_kw

    @property
    def sql(self):
        return f'{self.alias}."{self.column}"'

    def value(self, v):
        """Coerce a query value to the stored representation for equality/range."""
        if self.keyword or self.es_type in (None, "keyword", "text"):
            return coerce_keyword(v)
        if self.es_type in INTEGRAL:
            return coerce_number(v, True)
        if self.es_type in FLOATING:
            return coerce_number(v, False)
        if self.es_type == "boolean":
            return coerce_bool(v)
        if self.es_type == "date":
            return coerce_date(self.column, v)[0]
        return v

    @property
    def is_string(self):
        """keyword or text (or .keyword sub-field): the only types ES allows prefix and wildcard on."""
        return self.keyword or self.es_type in (None, "keyword", "text")


# ------------------------------------------------------------------ schema metadata
class IndexMeta:
    def __init__(self, con, idx):
        # main first, then the attached long-text file (schema `txt`, PatentRef 1.6)
        self.schema = "main"
        row = con.execute("SELECT tbl, key_field FROM main._lapse_indices WHERE idx=?", (idx,)).fetchone()
        if row is None and _has_schema(con, "txt"):
            try:
                row = con.execute("SELECT tbl, key_field FROM txt._lapse_indices WHERE idx=?", (idx,)).fetchone()
            except sqlite3.Error:
                row = None
            if row is not None:
                self.schema = "txt"
        if row is None:
            # documented 501 for views whose data set is not in the snapshot (API/lapse_errors.py)
            from API.lapse_errors import LapseNotImplemented

            raise LapseNotImplemented(idx)
        sch = self.schema
        self.index, self.table, self.key = idx, row[0], row[1]
        self.types = {}  # (path, field) -> (es_type, has_keyword_subfield)
        for path, field, t, kw in con.execute(
                f"SELECT path, field, es_type, keyword_subfield FROM {sch}._lapse_fields WHERE idx=?", (idx,)):
            self.types[(path, field)] = (t, bool(kw))
        tables = {r[0] for r in con.execute(f"SELECT name FROM {sch}.sqlite_master WHERE type IN ('table','view')")}
        self.columns = [r[1] for r in con.execute(f'PRAGMA {sch}.table_info("{self.table}")')]
        self.nested = {}  # path -> child table
        self.child_columns = {}
        prefix = self.table + "__"
        for t in sorted(tables):
            if t.startswith(prefix):
                path = t[len(prefix):]
                self.nested[path] = t
                self.child_columns[path] = [r[1] for r in con.execute(f'PRAGMA {sch}.table_info("{t}")')
                                            if r[1] not in ("_pid", "_ord")]
        # virtual nested groups (PatentRef 2.9): served from the attached catalog, not from a child table;
        # they appear in f like any group and are refused in q and s (Translator.q_nested, _sort_spec)
        self.virtual = {}
        if idx == "patents":
            from API.lapse_group import LAPSE_FIELDS, LAPSE_PATH

            self.virtual[LAPSE_PATH] = list(LAPSE_FIELDS)
            self.child_columns[LAPSE_PATH] = list(LAPSE_FIELDS)
        self.fts = {}  # table -> set(columns)
        self.trigram = {}  # table -> set(columns) of fts_trgm_<table> (substring prefilter, PatentRef 2.10)
        self.fts_detail = {}  # table -> "full" | "column" | "none" (the FTS5 detail option, from the DDL)
        for t, ddl in con.execute(f"SELECT name, sql FROM {sch}.sqlite_master WHERE type='table' AND name LIKE 'fts_%'"):
            if t.endswith(("_data", "_idx", "_docsize", "_config", "_content")):
                continue
            if t.startswith("fts_trgm_"):
                self.trigram[t[9:]] = {r[1] for r in con.execute(f'PRAGMA {sch}.table_info("{t}")')}
            else:
                self.fts[t[4:]] = {r[1] for r in con.execute(f'PRAGMA {sch}.table_info("{t}")')}
                m = re.search(r"detail\s*=\s*'?(full|column|none)", ddl or "", re.I)
                self.fts_detail[t[4:]] = m.group(1).lower() if m else "full"
        # columns known to hold no NULL (the long-text file records them; main is checked with a scan)
        self.nulls = {}
        if "_lapse_nulls" in tables:
            for tbl, col, has in con.execute(f"SELECT tbl, col, has_null FROM {sch}._lapse_nulls"):
                self.nulls[(tbl, col)] = bool(has)
        # value frequencies of low-cardinality columns (_lapse_value_stats, written by build_sample.py):
        # (table, column) -> {value: rows}; (table, "*") -> total rows. The planner gets likelihood()
        # hints from them, must_not on such a column becomes an index range union, and a nested group
        # whose match is dense is joined with EXISTS instead of IN.
        self.stats, self.rows = {}, {}
        self.hist = {}  # (table, column) -> (prefix length, {prefix: rows}); a range estimate for the column
        if "_lapse_value_stats" in tables:
            for tbl, col, val, n in con.execute(f"SELECT tbl, col, val, n FROM {sch}._lapse_value_stats"):
                if col == "*":
                    self.rows[tbl] = n
                elif "/" in col:
                    c, width = col.rsplit("/", 1)
                    self.hist.setdefault((tbl, c), (int(width), {}))[1][val] = n
                else:
                    self.stats.setdefault((tbl, col), {})[val] = n
        # single-column indexes of the main table, for steering an ORDER BY onto the index of its first key
        self.single_index = {}  # column -> index name
        for _, name, _, origin, _ in con.execute(f'PRAGMA {sch}.index_list("{self.table}")'):
            if origin != "c":
                continue
            cols = [r[2] for r in con.execute(f'PRAGMA {sch}.index_info("{name}")')]
            if len(cols) == 1 and cols[0] not in self.single_index:
                self.single_index[cols[0]] = name

    def freq(self, table, column, value):
        """Fraction of `table` rows holding `value` in `column` (None when the column has no stats)."""
        d = self.stats.get((table, column))
        total = self.rows.get(table)
        if d is None or not total:
            return None
        key = value if isinstance(value, str) else (str(value) if value is not None else None)
        return d.get(key, 0) / total

    def range_freq(self, table, column, lo, hi):
        """Estimated fraction of `table` rows whose `column` lies in [lo, hi] (either bound may be None),
        from the value stats (exact for a listed column) or the prefix histogram (a date by year, so the
        bounding years count whole); None when the column has neither."""
        total = self.rows.get(table)
        if not total:
            return None
        d = self.stats.get((table, column))
        width = None
        if d is None and (table, column) in self.hist:
            width, d = self.hist[(table, column)]
        if d is None:
            return None
        lo_s = None if lo is None else str(lo)[:width] if width else lo
        hi_s = None if hi is None else str(hi)[:width] if width else hi
        n = 0
        for v, rows in d.items():
            if v is None:
                continue
            key = v if width else (type(lo_s)(v) if lo_s is not None else type(hi_s)(v) if hi_s is not None else v)
            try:
                if (lo_s is None or key >= lo_s) and (hi_s is None or key <= hi_s):
                    n += rows
            except (TypeError, ValueError):
                return None
        return n / total

    def columns_of(self, path):
        return self.columns if path is None else self.child_columns.get(path, [])

    def table_of(self, path):
        return self.table if path is None else self.nested.get(path)

    def type_of(self, path, column):
        return self.types.get((path or "", column), ("keyword", False))


# ------------------------------------------------------------------ DSL -> SQL
class Translator:
    """ES DSL -> SQL WHERE clause. `mode` is "search" or "count": the two differ only where the best SQL
    shape differs (a dense nested group is EXISTS for a page and IN for a count). `density` is the
    estimated fraction of parent rows the top-level AND of the query keeps, from `_lapse_value_stats`,
    or None when any term has no estimate; the searcher uses it to steer an ORDER BY onto an index."""

    DENSE_NESTED = 0.02     # nested match rows / parent rows at or above this: EXISTS probes beat the IN set
    RARE_COMPLEMENT = 0.10  # must_not whose complement is this rare or rarer: index range union

    def __init__(self, meta, mode="search"):
        self.meta = meta
        self.mode = mode
        self.params = []
        self.density = None
        self._p = None  # estimated selectivity of the leaf just translated (None = unknown)

    # ---- field resolution
    def resolve(self, field, ctx):
        alias, path = ctx
        name = field
        if path is not None and name.startswith(path + "."):
            name = name[len(path) + 1:]
        keyword = False
        if name.endswith(".keyword"):
            name, keyword = name[:-8], True
        cols = self.meta.columns_of(path)
        if name not in cols:
            return None  # unmapped field: ES term-level queries on it match nothing
        es_type, has_kw = self.meta.type_of(path, name)
        if keyword and not has_kw:
            return None
        return FieldRef(alias, self.meta.table_of(path), name, es_type, keyword, has_kw)

    def p(self, v):
        self.params.append(v)
        return "?"

    # ---- entry
    def where(self, q, ctx=("m", None)):
        sql = self._where(q, ctx)
        if ctx == ("m", None):
            self.density = self._p
        return sql

    def _where(self, q, ctx):
        self._p = None
        if not q:
            self._p = 1.0
            return "1"
        if not isinstance(q, dict) or len(q) != 1:
            raise es_error("parsing_exception", "[_na] query malformed, must start with start_object")
        kind, body = next(iter(q.items()))
        fn = getattr(self, "q_" + kind, None)
        if fn is None:
            raise es_error("parsing_exception", f"unknown query [{kind}]")
        return fn(body, ctx)

    def q_match_all(self, body, ctx):
        self._p = 1.0
        return "1"

    def q_bool(self, body, ctx):
        parts, ps = [], []
        for clause in ("filter", "must"):
            subs = body.get(clause, [])
            subs = subs if isinstance(subs, list) else [subs]
            for s in subs:
                parts.append(f"({self._where(s, ctx)})")
                ps.append(self._p)
        should = body.get("should", [])
        should = should if isinstance(should, list) else [should]
        if should and not parts:
            ors, p_or = [], 0.0
            for s in should:
                ors.append(f"({self._where(s, ctx)})")
                p_or = None if (p_or is None or self._p is None) else min(1.0, p_or + self._p)
            parts.append("(" + " OR ".join(ors) + ")")
            ps.append(p_or)
        not_ = body.get("must_not", [])
        not_ = not_ if isinstance(not_, list) else [not_]
        for s in not_:
            ranged = self.complement(s, ctx)
            if ranged is not None:
                parts.append(ranged)
                ps.append(self._p)
                continue
            # leaves are plain SQL predicates (index friendly); NULL only needs care under negation,
            # where ES treats a missing value as "does not match", so NOT(missing) is true
            parts.append(f"NOT COALESCE(({self._where(s, ctx)}), 0)")
            ps.append(None if self._p is None else 1.0 - self._p)
        p = 1.0
        for x in ps:
            p = None if (p is None or x is None) else p * x
        self._p = p
        return " AND ".join(parts) if parts else "1"

    def complement(self, q, ctx):
        """must_not of equality on one low-cardinality column of the main table, when the complement is
        rare: NOT (c = a OR c = b) is rewritten as the union of the index ranges around a and b (NULL
        included, as ES counts a missing value as not matching), so SQLite walks a few index ranges
        instead of testing every row. For a page the union sits in a rowid subquery, which keeps the
        planner from scanning the sort index for it. Returns None when the rewrite does not apply."""
        alias, path = ctx
        if path is not None or not isinstance(q, dict) or len(q) != 1:
            return None
        kind, body = next(iter(q.items()))
        leaves = []
        if kind in ("match", "terms"):
            leaves.append((kind, body))
        elif kind == "bool" and set(body) == {"should"}:
            for s in body["should"] if isinstance(body["should"], list) else [body["should"]]:
                if not isinstance(s, dict) or len(s) != 1 or next(iter(s)) not in ("match", "terms"):
                    return None
                leaves.append(next(iter(s.items())))
        else:
            return None
        field, values = None, []
        for kind, body in leaves:
            if not isinstance(body, dict) or len(body) != 1:
                return None
            k, v = next(iter(body.items()))
            if isinstance(v, dict):
                v = v.get("query")
            vs = v if isinstance(v, list) else [v]
            if field is None:
                field = k
            elif field != k:
                return None
            values += vs
        f = self.resolve(field, ctx)
        if f is None or f.keyword or f.es_type in ("text", "date") or f.es_type in FLOATING:
            return None
        if self.meta.stats.get((f.table, f.column)) is None:
            return None
        try:
            coerced = sorted({f.value(v) for v in values if v is not None})
        except (TypeError, ValueError):
            return None
        if not coerced or any(f.es_type in INTEGRAL and has_decimal_part(v) for v in values):
            return None
        kept = 1.0 - sum(self.meta.freq(f.table, f.column, v) for v in coerced)
        if kept > self.RARE_COMPLEMENT:
            return None
        col = f'"{f.column}"'
        ors = [f"{col} < {self.p(coerced[0])}"]
        for lo, hi in zip(coerced, coerced[1:]):
            ors.append(f"({col} > {self.p(lo)} AND {col} < {self.p(hi)})")
        ors.append(f"{col} > {self.p(coerced[-1])}")
        ors.append(f"{col} IS NULL")
        ranges = "(" + " OR ".join(ors) + ")"
        self._p = max(kept, 0.0)
        if self.mode == "count":
            return ranges.replace(col, f"{alias}.{col}")  # col is the quoted name, so this is exact
        return f'{alias}.rowid IN (SELECT rowid FROM "{f.table}" WHERE {ranges})'

    def q_nested(self, body, ctx):
        alias, path = ctx
        npath = body.get("path")
        if npath in self.meta.virtual:
            from API.lapse_errors import LapseBadRequest

            raise LapseBadRequest(f"Invalid field: the '{npath}' group is returned with f only; it cannot be queried or sorted in this version")
        child = self.meta.nested.get(npath)
        if child is None or path is not None:
            self._p = None
            return "0"
        inner = self._where(body.get("query", {}), ("c", npath))
        rows = self.meta.rows.get(child)
        parent_rows = self.meta.rows.get(self.meta.table)
        dense = (self.mode == "search" and self._p is not None and rows and parent_rows
                 and self._p * rows / parent_rows >= self.DENSE_NESTED)
        self._p = None
        if dense:
            # a dense group: probing the child per parent in sort order reaches the page size after a
            # few rows; the IN form first materializes every matching child (millions for a CPC section)
            return f'EXISTS (SELECT 1 FROM "{child}" c WHERE c."_pid" = {alias}."{self.meta.key}" AND ({inner}))'
        return f'{alias}."{self.meta.key}" IN (SELECT c."_pid" FROM "{child}" c WHERE {inner})'

    # ---- leaves
    @staticmethod
    def one(body, qname):
        if not isinstance(body, dict) or len(body) != 1:
            raise es_error("parsing_exception", f"[{qname}] query doesn't support multiple fields")
        return next(iter(body.items()))

    def kw_guard(self, f):
        return f" AND length({f.sql}) <= {KEYWORD_IGNORE_ABOVE}" if f.keyword else ""

    def eq(self, f, v):
        """Exact (term-level) equality on a resolved non-text field."""
        self._p = None
        if f.es_type == "date" and not f.keyword:
            lo, hi, midnight = coerce_date(f.column, v)
            if not midnight:
                return "0"
            if lo == hi:
                return f"({f.sql} = {self.p(lo)})"
            # a partial date is a span in ES (term "2010" matches the whole year)
            return f"({f.sql} >= {self.p(lo)} AND {f.sql} <= {self.p(hi)})"
        if f.es_type in INTEGRAL and not f.keyword and has_decimal_part(v):
            return "0"  # ES: "Value [x] has a decimal part" is a match-none query, not an error
        value = f.value(v)
        term = f"{f.sql} = {self.p(value)}"
        p = None if f.keyword else self.meta.freq(f.table, f.column, value)
        if p is not None:
            self._p = p
            # the planner's own estimate is rows / distinct values; the stats know the skew (nine
            # patents in ten are utility), and likelihood() carries that without touching the result
            term = f"likelihood({term}, {min(max(p, 1e-7), 0.9999):.7f})"
        return f"({term}{self.kw_guard(f)})"

    def fts(self, f, expr):
        """Rows of f.table whose FTS column matches expr; falls back to a Python matcher when no FTS table.
        An index built with detail=none (the long descriptions) has no positions, so a phrase of two or more
        tokens is answered by the AND of its tokens on the index and the exact phrase test in Python on those
        candidates (same rows as a positional index would give)."""
        cols = self.meta.fts.get(f.table, set())
        if f.column in cols:
            if self.meta.fts_detail.get(f.table, "full") != "full":
                # detail=none also refuses column filters; such a table (build_text.py) holds this one column
                m = re.fullmatch(r'"((?:[^"]|"")*)"', expr)
                if m and len(m.group(1).replace('""', '"').split()) > 1:
                    pre = " AND ".join(fts_quote(t) for t in m.group(1).replace('""', '"').split())
                    return (f'({f.alias}.rowid IN (SELECT rowid FROM "fts_{f.table}" WHERE "fts_{f.table}" MATCH {self.p(pre)}) '
                            f"AND COALESCE(lapse_fts_match({f.sql}, {self.p(expr)}), 0))")
                return f'{f.alias}.rowid IN (SELECT rowid FROM "fts_{f.table}" WHERE "fts_{f.table}" MATCH {self.p(expr)})'
            q = f'"{f.column}" : ({expr})'
            return f'{f.alias}.rowid IN (SELECT rowid FROM "fts_{f.table}" WHERE "fts_{f.table}" MATCH {self.p(q)})'
        return f"COALESCE(lapse_fts_match({f.sql}, {self.p(expr)}), 0)"

    def text_match(self, f, text, operator="or", phrase=False):
        toks = tokens(text)
        if not toks:
            return "0"
        if phrase:
            expr = '"' + " ".join(t.replace('"', '""') for t in toks) + '"'
        else:
            expr = f" {'AND' if operator == 'and' else 'OR'} ".join(fts_quote(t) for t in toks)
        return self.fts(f, expr)

    def q_match(self, body, ctx):
        field, v = self.one(body, "match")
        operator = "or"
        if isinstance(v, dict):
            unknown = [k for k in v if k not in ("query", "operator", "analyzer", "zero_terms_query", "lenient")]
            if unknown:
                raise es_error("parsing_exception", f"[match] query does not support [{unknown[0]}]")
            operator = str(v.get("operator", "or")).lower()
            v = v.get("query")
        if isinstance(v, list):
            raise es_error("parsing_exception", "[match] unknown token [START_ARRAY] after [query]")
        f = self.resolve(field, ctx)
        if f is None:
            return "0"
        if f.es_type == "text" and not f.keyword:
            return self.text_match(f, v, operator)
        return self.eq(f, v)

    def q_match_phrase(self, body, ctx):
        field, v = self.one(body, "match_phrase")
        if isinstance(v, dict):
            v = v.get("query")
        f = self.resolve(field, ctx)
        if f is None:
            return "0"
        if f.es_type == "text" and not f.keyword:
            return self.text_match(f, v, phrase=True)
        return self.eq(f, v)

    def q_terms(self, body, ctx):
        field, values = self.one(body, "terms")
        if not isinstance(values, list):
            raise es_error("parsing_exception", "[terms] query does not support [" + str(field) + "]")
        f = self.resolve(field, ctx)
        if f is None or not values:
            return "0"
        ors, p = [], 0.0
        for v in values:
            if f.es_type == "text" and not f.keyword:
                # terms is not analyzed: only a single lowercase token can equal an indexed term
                s = coerce_keyword(v)
                toks = tokens(s)
                ors.append(self.fts(f, fts_quote(s)) if len(toks) == 1 and toks[0] == s else "0")
                p = None
            else:
                ors.append(self.eq(f, v))
                p = None if (p is None or self._p is None) else min(1.0, p + self._p)
        self._p = p
        return "(" + " OR ".join(ors) + ")"

    def q_range(self, body, ctx):
        field, spec = self.one(body, "range")
        if not isinstance(spec, dict):
            raise es_error("parsing_exception", "[range] query malformed, no start_object after query name")
        f = self.resolve(field, ctx)
        if f is None:
            return "0"
        ops = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        parts = []
        if f.es_type == "text" and not f.keyword:
            # ES range on an analyzed field is a term range over its tokens (any token inside the bounds
            # matches); the bounds are compared raw, the tokens are lowercase
            lo = hi = None
            inc_lo = inc_hi = 1
            for k, v in spec.items():
                if k not in ops:
                    raise es_error("parsing_exception", f"[range] query does not support [{k}]")
                if k in ("gt", "gte"):
                    lo, inc_lo = coerce_keyword(v), int(k == "gte")
                else:
                    hi, inc_hi = coerce_keyword(v), int(k == "lte")
            return (f"COALESCE(lapse_tok_range({f.sql}, {self.p(lo)}, {self.p(hi)}, "
                    f"{self.p(inc_lo)}, {self.p(inc_hi)}), 0)")
        lo_b = hi_b = None  # the bounds as stored, for the selectivity estimate
        for k, v in spec.items():
            if k not in ops:
                raise es_error("parsing_exception", f"[range] query does not support [{k}]")
            if f.es_type == "date" and not f.keyword:
                lo, hi, midnight = coerce_date(f.column, v)
                if k in ("gte", "gt"):
                    lo_b = lo
                else:
                    hi_b = hi
                # ES rounds a partial value down for gte/lt and up for gt/lte ("2010" is the whole year);
                # a time later than 00:00 excludes the document stored at midnight of that day
                if k == "gte":
                    parts.append(f"{f.sql} {'>=' if midnight else '>'} {self.p(lo)}")
                elif k == "gt":
                    parts.append(f"{f.sql} > {self.p(hi)}")
                elif k == "lte":
                    parts.append(f"{f.sql} <= {self.p(hi)}")
                else:
                    parts.append(f"{f.sql} {'<' if midnight else '<='} {self.p(lo)}")
            elif f.es_type in INTEGRAL and not f.keyword:
                op, n = integral_bound(v, k)
                parts.append(f"{f.sql} {op} {self.p(n)}")
                if k in ("gte", "gt"):
                    lo_b = n
                else:
                    hi_b = n
            else:
                parts.append(f"{f.sql} {ops[k]} {self.p(f.value(v))}")
        sql = f"{' AND '.join(parts) or '1'}"
        p = None
        if parts and not f.keyword and (lo_b is not None or hi_b is not None):
            p = self.meta.range_freq(f.table, f.column, lo_b, hi_b)
        if p is not None:
            self._p = p
            sql = f"likelihood({sql}, {min(max(p, 1e-7), 0.9999):.7f})"
        return f"({sql}{self.kw_guard(f)})"

    def _pattern(self, body, qname, ctx, to_like):
        field, spec = self.one(body, qname)
        if isinstance(spec, dict):
            value = spec.get("value", spec.get("wildcard", spec.get("prefix")))
            ci = bool(spec.get("case_insensitive", False))
        else:
            value, ci = spec, False
        f = self.resolve(field, ctx)
        if f is None or value is None:
            return "0"
        if not f.is_string:
            # ES MappedFieldType default: numeric, date and boolean fields reject prefix and wildcard;
            # the upstream handler reports the query_shard_exception as 500 ERR_ES
            what = "prefix queries on keyword, text and wildcard" if qname == "prefix" else "wildcard queries on keyword and text"
            raise es_error("query_shard_exception",
                           f"Can only use {what} fields - not on [{field}] which is of type [{f.es_type}]")
        value = coerce_keyword(value)
        if f.es_type == "text" and not f.keyword:
            # term-level pattern against the analyzed tokens (lowercase, no spaces): a token matches the
            # whole pattern, case-insensitively (the parser always sets case_insensitive)
            low = value.lower()
            toks = tokens(low)
            if qname == "prefix":
                if low == "":
                    return f"({f.sql} IS NOT NULL AND length({f.sql}) > 0)"
                if len(toks) == 1 and toks[0] == low:
                    return self.fts(f, fts_quote(low) + "*")
                return "0"  # no token contains a space or punctuation, so nothing starts with the value
            inner = low[1:-1] if len(low) > 2 and low[0] == low[-1] == "*" else None
            if inner is not None and not any(ch in inner for ch in "*?"):
                # the _contains operator: a cheap substring test on the whole value first, then the token test
                return (f"({self.trigram(f, inner)}instr(lapse_lower({f.sql}), {self.p(inner)}) > 0 AND "
                        f"COALESCE(lapse_tok_wild({f.sql}, {self.p(low)}), 0))")
            return f"COALESCE(lapse_tok_wild({f.sql}, {self.p(low)}), 0)"
        pat = to_like(value)
        inner = value[1:-1] if qname == "wildcard" and len(value) > 2 and value[0] == value[-1] == "*" else None
        if ci and inner and not any(ch in inner for ch in "*?"):
            # plain substring (the _contains operator): instr() is much cheaper than a leading-% LIKE
            low = inner.lower()
            return (f"({self.trigram(f, low)}(instr(lower({f.sql}), {self.p(low)}) > 0 OR (octet_length({f.sql}) > length({f.sql}) AND "
                    f"instr(lapse_lower({f.sql}), {self.p(low)}) > 0)){self.kw_guard(f)})")
        if ci:
            # SQLite lower() folds ASCII only; values with non-ASCII characters go through Python str.lower()
            low = pat.lower()
            pre = self.trigram(f, value.lower()) if qname == "prefix" and value == like_escape(value) else ""
            return (f"({pre}(lower({f.sql}) LIKE {self.p(low)} ESCAPE '\\' OR (octet_length({f.sql}) > length({f.sql}) AND "
                    f"lapse_lower({f.sql}) LIKE {self.p(low)} ESCAPE '\\')){self.kw_guard(f)})")
        return f"(lapse_like_cs({f.sql}, {self.p(pat)}){self.kw_guard(f)})"

    def trigram(self, f, needle):
        """Substring prefilter for _contains and _begins: rows whose fts_trgm_<table> column holds the
        trigrams of `needle` as a phrase, which every value containing it does. Only an ASCII needle of
        three or more characters qualifies (the trigram index is case-folded by SQLite, the exact test
        by Python; the two agree on ASCII, and shorter needles have no trigram). The exact predicate
        still follows, so the result set is the same with or without the prefilter."""
        cols = self.meta.trigram.get(f.table)
        if not cols or f.column not in cols or len(needle) < 3 or not needle.isascii():
            return ""
        q = f'"{f.column}" : "{needle.replace(chr(34), chr(34) * 2)}"'
        return (f'{f.alias}.rowid IN (SELECT rowid FROM "fts_trgm_{f.table}" WHERE "fts_trgm_{f.table}" '
                f"MATCH {self.p(q)}) AND ")

    def q_prefix(self, body, ctx):
        return self._pattern(body, "prefix", ctx, lambda v: like_escape(v) + "%")

    def q_wildcard(self, body, ctx):
        return self._pattern(body, "wildcard", ctx, wildcard_to_like)


# ------------------------------------------------------------------ SQL functions
def _lapse_lower(s):
    return None if s is None else str(plain(s)).lower()


def _like_cs(value, pattern):
    if value is None:
        return 0
    value = plain(value)
    rx, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            rx.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        rx.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return 1 if re.fullmatch("".join(rx), str(value), re.S) else 0


def _tok_range(text, lo, hi, inc_lo, inc_hi):
    """Any token of text inside [lo, hi] (bounds compared as strings, like an ES term range on text)."""
    if text is None:
        return 0
    for tok in tokens(text):
        if lo is not None and (tok < lo or (tok == lo and not inc_lo)):
            continue
        if hi is not None and (tok > hi or (tok == hi and not inc_hi)):
            continue
        return 1
    return 0


_wild_cache = {}


def _tok_wild(text, pattern):
    """Any token of text matching the ES wildcard pattern (* and ?), case-insensitively."""
    if text is None:
        return 0
    rx = _wild_cache.get(pattern)
    if rx is None:
        rx = re.compile("".join(".*" if ch == "*" else "." if ch == "?" else re.escape(ch) for ch in pattern), re.S)
        if len(_wild_cache) > 512:
            _wild_cache.clear()
        _wild_cache[pattern] = rx
    return 1 if any(rx.fullmatch(tok) for tok in tokens(text)) else 0


def _fts_match(text, expr):
    if text is None:
        return 0
    toks = tokens(text)
    chunks = [c.replace('""', '"') for c in re.findall(r'"((?:[^"]|"")*)"', expr)]
    if len(chunks) == 1 and expr.endswith("*"):
        return 1 if any(t.startswith(chunks[0]) for t in toks) else 0
    if len(chunks) == 1 and " " in chunks[0]:
        ph = chunks[0].split()
        return 1 if any(toks[i:i + len(ph)] == ph for i in range(len(toks))) else 0
    have = set(toks)
    if " AND " in expr:
        return 1 if all(c in have for c in chunks) else 0
    return 1 if any(c in have for c in chunks) else 0


# ------------------------------------------------------------------ profiling hook
SQL_LOG = os.environ.get("LAPSE_SQL_LOG", "")  # path of a JSON-lines file; empty = off (PatentRef gate 2.10)


def _log_sql(sql, params, ms):
    """Append one line per executed statement: the exact SQL, its parameters and the wall time."""
    import json

    try:
        with open(SQL_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ms": round(ms, 2), "sql": sql, "params": list(params)}, default=str) + "\n")
    except OSError:
        pass


# ------------------------------------------------------------------ searcher
def _has_schema(con, name):
    try:
        return any(r[1] == name for r in con.execute("PRAGMA database_list"))
    except sqlite3.Error:
        return False


def read_data_version(con, real_path, fallback_env=True, schema="main"):
    """The data_version of an open search database: `_lapse_build.data_version` (stamped by the refresh
    runner, PatentRef 4.2); for a file built before the runner, LAPSE_DATA_VERSION from the environment
    if set, else the file's own name without the extension (full2.db -> full2). Never empty."""
    try:
        if con.execute(f"SELECT 1 FROM {schema}.sqlite_master WHERE name='_lapse_build'").fetchone():
            row = con.execute(f"SELECT v FROM {schema}._lapse_build WHERE k='data_version'").fetchone()
            if row and row[0]:
                return str(row[0])
    except sqlite3.Error:
        pass
    env = os.environ.get("LAPSE_DATA_VERSION", "") if fallback_env else ""
    return env or os.path.splitext(os.path.basename(real_path))[0] or "unversioned"


def current_data_version():
    """The data_version of the connection this thread is holding, or None when it has opened none yet.
    Read by `API.lapse_errors.DataVersionMiddleware` after the view ran, so the header on a response
    names the file that produced its body."""
    return getattr(LapseSQLiteSearch._local, "data_version", None)


class LapseSQLiteSearch:
    """One instance per request (API.search.get_searcher builds one in the view). The configured path is
    normally a symlink (`snapshot_current.db`); it is resolved ONCE, here, so every query of a request
    hits the same file even if the symlink moves mid-request (PatentRef gate 4.3). A thread's cached
    connection is reopened when the resolved file differs from the one it holds, so after a swap the
    next request on this thread serves the new file without a worker restart."""

    _local = threading.local()
    _meta_cache = {}
    STEER_DENSITY = 0.05  # estimated fraction of rows kept by the filter from which the sort index wins

    def __init__(self, path, timeout=60, cache_kb=400000, mmap_bytes=1073741824, count_cache=None,
                 count_cache_min_ms=20.0, request_budget=None):
        self.path = path
        self.real = os.path.realpath(path)
        # the Lapse catalog (PatentRef 2.9): resolved once per request like the snapshot, attached read-only
        from API.lapse_group import catalog_real

        self.catalog = catalog_real()
        self.text = text_real()  # the long-text file (PatentRef 1.6), resolved once per request too
        self.timeout = timeout
        # one instance serves one request (page then count): the budget caps the two together (gate 5.6)
        self.budget = float(request_budget) if request_budget else float(timeout)
        self.deadline = (time.monotonic() + float(request_budget)) if request_budget else None
        self.cache_kb = int(cache_kb)
        self.mmap_bytes = int(mmap_bytes)
        self.count_cache = count_cache  # path of the shared total_hits memo, or None
        self.count_cache_min_ms = float(count_cache_min_ms)

    @classmethod
    def from_django_settings(cls):
        from django.conf import settings

        cfg = settings.LAPSE_SQLITE
        return cls(path=cfg["path"], timeout=int(cfg.get("timeout", 60)), cache_kb=cfg.get("cache_kb", 400000),
                   mmap_bytes=cfg.get("mmap_bytes", 1073741824), count_cache=cfg.get("count_cache") or None,
                   count_cache_min_ms=cfg.get("count_cache_min_ms", 20.0),
                   request_budget=getattr(settings, "LAPSE_REQUEST_BUDGET", None))

    def connection(self):
        con = getattr(self._local, "con", None)
        if con is None or getattr(self._local, "real", None) != self.real or getattr(self._local, "catalog_real", None) != self.catalog \
                or getattr(self._local, "text_real", None) != self.text:
            if con is not None:
                try:
                    con.close()
                except sqlite3.Error:
                    pass
                self._local.con = None
            uri = "file:" + self.real.replace("\\", "/") + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=self.timeout)
            con.execute("PRAGMA case_sensitive_like=ON")
            con.execute("PRAGMA temp_store=MEMORY")  # the IN sets of nested groups never touch a temp file
            try:
                # document frequencies for /api/v1/lapse/similar: a temp virtual table (main stays read-only),
                # created after temp_store is set (changing temp_store drops every temp table) and before query_only
                con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_patents_vocab USING fts5vocab('main', 'fts_patents', 'row')")
            except sqlite3.Error:
                pass
            con.execute("PRAGMA query_only=ON")
            con.execute(f"PRAGMA cache_size=-{self.cache_kb}")
            con.execute(f"PRAGMA mmap_size={self.mmap_bytes}")
            con.create_function("lapse_lower", 1, _lapse_lower, deterministic=True)
            con.create_function("lapse_like_cs", 2, _like_cs, deterministic=True)
            con.create_function("lapse_fts_match", 2, _fts_match, deterministic=True)
            con.create_function("lapse_tok_range", 5, _tok_range, deterministic=True)
            con.create_function("lapse_tok_wild", 2, _tok_wild, deterministic=True)
            from API.lapse_group import attach

            self._local.cat = attach(con, self.catalog)  # None when no catalog is configured or it cannot be opened
            self._local.text_version = attach_text(con, self.text)  # None when there is no long-text file
            self._local.con, self._local.real, self._local.catalog_real = con, self.real, self.catalog
            self._local.text_real = self.text
            self._local.data_version = read_data_version(con, self.real)
        return con

    def text_data_version(self):
        """The attached long-text file's data_version, or None when none is attached."""
        self.connection()
        return getattr(self._local, "text_version", None)

    def catalog_facts(self):
        """The attached catalog's facts (data_version, as_of, fee_rule, columns) or None."""
        self.connection()
        return getattr(self._local, "cat", None)

    def data_version(self):
        self.connection()
        return self._local.data_version

    def meta(self, index):
        key = (self.real, self.text, index)
        if key not in self._meta_cache:
            self._meta_cache[key] = IndexMeta(self.connection(), index)
        return self._meta_cache[key]

    @staticmethod
    def _query_body(query):
        if query is None:
            return {}
        if "query" in query:
            return query["query"] or {}
        return query

    def _where(self, meta, query, mode="search"):
        tr = Translator(meta, mode)
        sql = tr.where(self._query_body(query))
        return sql, tr.params, tr.density

    def count(self, index, query):
        meta = self.meta(index)
        where, params, _ = self._where(meta, query, "count")
        sql = f'SELECT count(*) FROM "{meta.table}" m WHERE {where}'
        n = self._memo_get(index, sql, params)
        if n is None:
            t0 = time.perf_counter()
            n = self._run(sql, params)[0][0]
            self._memo_put(index, sql, params, n, (time.perf_counter() - t0) * 1000)
        return {"count": n, "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0}}

    # ---- total_hits memo (the analogue of the Elasticsearch shard request cache)
    # A count is a pure function of (data file, query), and the file never changes under a data_version,
    # so a count once computed is stored under the sha256 of (data_version, index, SQL, parameters):
    # in this process, and in a small shared SQLite file every worker reads (LAPSE_COUNT_CACHE), so a
    # repeat of a slow count answers in a millisecond on any worker and after a restart. Only counts
    # that took at least count_cache_min_ms are stored. Nothing here can change a response body: a
    # stored count is the count the same SQL returned on the same file.
    _memo_local = {}
    _memo_order = []
    MEMO_LOCAL_MAX = 4096

    def _memo_key(self, index, sql, params):
        import hashlib
        import json

        ver = self.data_version()
        if self.meta(index).schema == "txt":
            ver = f"{ver}+text:{self.text_data_version()}"
        raw = "\n".join([ver, index, sql, json.dumps(list(params), default=str)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _memo_con(self):
        con = getattr(self._local, "memo_con", None)
        if con is None:
            con = sqlite3.connect(self.count_cache, timeout=2, check_same_thread=False)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("CREATE TABLE IF NOT EXISTS counts(key TEXT PRIMARY KEY, data_version TEXT, idx TEXT, "
                        "n INTEGER, ms REAL, created REAL)")
            con.commit()
            self._local.memo_con = con
        return con

    def _memo_get(self, index, sql, params):
        key = self._memo_key(index, sql, params)
        n = self._memo_local.get(key)
        if n is not None:
            return n
        if not self.count_cache:
            return None
        try:
            row = self._memo_con().execute("SELECT n FROM counts WHERE key=?", (key,)).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        self._memo_local[key] = row[0]
        return row[0]

    def _memo_put(self, index, sql, params, n, ms):
        if ms < self.count_cache_min_ms:
            return
        key = self._memo_key(index, sql, params)
        if key not in self._memo_local:
            self._memo_local[key] = n
            self._memo_order.append(key)
            if len(self._memo_order) > self.MEMO_LOCAL_MAX:
                self._memo_local.pop(self._memo_order.pop(0), None)
        if not self.count_cache:
            return
        try:
            con = self._memo_con()
            con.execute("INSERT OR REPLACE INTO counts VALUES (?,?,?,?,?,?)",
                        (key, self.data_version(), index, n, round(ms, 1), time.time()))
            if int(time.time() * 1000) % 100 == 0:  # about one write in a hundred prunes old versions
                con.execute("DELETE FROM counts WHERE data_version != ?", (self.data_version(),))
            con.commit()
        except sqlite3.Error:
            pass

    # ---- sort / search_after
    def _sort_spec(self, meta, sort):
        out = []
        for spec in sort or []:
            if isinstance(spec, str):
                field, order = spec, "asc"
            else:
                field, order = next(iter(spec.items()))
                if isinstance(order, dict):
                    order = order.get("order", "asc")
            order = str(order).lower()
            if order not in ("asc", "desc"):
                raise es_error("parsing_exception", f"[order] unknown value [{order}]")
            keyword = field.endswith(".keyword")
            name = field[:-8] if keyword else field
            if name in meta.virtual or name.split(".", 1)[0] in meta.virtual:
                from API.lapse_errors import LapseBadRequest

                raise LapseBadRequest(f"Invalid field: the '{name.split('.', 1)[0]}' group is returned with f only; it cannot be queried or sorted in this version")
            if name not in meta.columns or (keyword and not meta.type_of(None, name)[1]):
                # ES: No mapping found for [x] in order to sort on (query_shard_exception, surfaced as 500)
                raise es_error("query_shard_exception", f"No mapping found for [{field}] in order to sort on")
            es_type = meta.type_of(None, name)[0]
            if es_type == "text" and not keyword:
                raise es_error("illegal_argument_exception",
                               f"Text fields are not optimised for operations that require per-document field data "
                               f"like aggregations and sorting, so these operations are disabled by default. "
                               f"Please use a keyword field instead. Alternatively, set fielddata=true on [{name}] "
                               f"in order to load field data by uninverting the inverted index.")
            ref = FieldRef("m", meta.table, name, es_type, keyword, keyword)
            out.append((ref, order))
        return out

    def _after(self, sort_spec, after, params, nullable=None):
        """Keyset condition equivalent to ES search_after with missing values sorted last.
        When no sort column holds NULLs and no cursor value is missing, the condition is written in
        the nested form k1 <= v1 AND (k1 < v1 OR (k2 <= v2 AND (k2 < v2 OR ...))): the same set of rows
        as the OR expansion below, but its leading range term lets SQLite walk the index of the first
        sort key from the cursor instead of sorting every row that passes the filter."""
        if not after:
            return "1"
        keys = sort_spec[:len(after)]
        if nullable is not None and not any(nullable.get(r.column) for r, _ in keys) \
                and all(v is not None for v in after[:len(keys)]):
            local = []
            sql = None
            for i in range(len(keys) - 1, -1, -1):
                ref, order = keys[i]
                v = ref.value(after[i])
                op = ">" if order == "asc" else "<"
                local = [v, v] + local if sql is not None else [v] + local
                if sql is None:
                    sql = f"{ref.sql} {op} ?"
                else:
                    sql = f"{ref.sql} {op}= ? AND ({ref.sql} {op} ? OR ({sql}))"
            params.extend(local)
            return f"({sql})"
        # A later key holds NULLs (or a cursor value is missing): the OR expansion. When the FIRST key holds no
        # NULL and has a cursor value, every row of the expansion has k1 >= v1 (asc; <= for desc), so that range
        # is written in front as its own term and the expansion's column references carry SQLite's unary plus,
        # which keeps them from driving an index: the planner walks the first key's index from the cursor and
        # sorts within equal first keys, instead of a MULTI-INDEX OR over every row past the cursor and a sort
        # of all of them (PatentRef 1.6: the ref_after_cursor example on 102M claims, 33 s before, see the test).
        lead = ""
        first, first_order = sort_spec[0]
        if nullable is not None and not nullable.get(first.column) and after[0] is not None:
            lead_v = first.value(after[0])
        else:
            lead_v = None
        col = (lambda r: f"+{r.sql}") if lead_v is not None else (lambda r: r.sql)
        ors = []
        expansion_params = []
        for i, (ref, order) in enumerate(sort_spec):
            if i >= len(after):
                break
            ands, local = [], []
            for j, (ref_j, _) in enumerate(sort_spec[:i]):
                v = after[j]
                if v is None:
                    ands.append(f"{col(ref_j)} IS NULL")
                else:
                    local.append(ref_j.value(v))
                    ands.append(f"{col(ref_j)} = ?")
            v = after[i]
            if v is None:
                continue  # nothing sorts after a missing value within this key
            local.append(ref.value(v))
            op = ">" if order == "asc" else "<"
            ands.append(f"({col(ref)} {op} ? OR {col(ref)} IS NULL)")
            ors.append("(" + " AND ".join(ands) + ")")
            expansion_params.extend(local)
        if not ors:
            return "0"
        if lead_v is not None:
            params.append(lead_v)
            lead = f"{first.sql} {'>=' if first_order == 'asc' else '<='} ? AND "
        params.extend(expansion_params)
        return f"({lead}(" + " OR ".join(ors) + "))"

    # ---- _source filtering
    @staticmethod
    def _wanted(meta, fields):
        """Map ES _source includes to (top-level columns, {nested path: [sub columns]})."""
        top, nested = [], {}
        pats = list(fields) if fields else ["*"]
        for c in meta.columns:
            if any(p == c or fnmatch.fnmatchcase(c, p) for p in pats):
                top.append(c)
        for path, cols in meta.child_columns.items():
            if any(p == path or fnmatch.fnmatchcase(path, p) or p == path + ".*" for p in pats):
                nested[path] = list(cols)
                continue
            subs = [c for c in cols if any(p.startswith(path + ".") and fnmatch.fnmatchcase(path + "." + c, p)
                                           for p in pats)]
            if subs:
                nested[path] = subs
        return top, nested

    def _convert(self, meta, path, col, v):
        if v is None:
            return None
        if isinstance(v, (bytes, memoryview)):
            return plain(v)  # a compressed text column of the long-text file
        if meta.type_of(path, col)[0] == "boolean":
            return bool(v)
        return v

    _null_cache = {}

    def _run(self, sql, params):
        """Execute with the configured timeout; an interrupted query surfaces as TimeoutError, which the
        upstream exception handler reports as 500 ERR_ES (same as an ES timeout)."""
        con = self.connection()
        deadline = time.monotonic() + self.timeout
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
            if time.monotonic() > deadline:
                raise LapseTimeout(self._timeout_reason())  # the request's budget is already spent
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 200000)
        t0 = time.perf_counter()
        try:
            return con.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise LapseTimeout(self._timeout_reason())
            raise
        finally:
            con.set_progress_handler(None, 0)
            if SQL_LOG:
                _log_sql(sql, params, (time.perf_counter() - t0) * 1000)

    def _timeout_reason(self):
        return (f"Search timed out: one statement may run {int(self.timeout)} s and one request's page and count "
                f"together {int(self.budget)} s (PatentRef gate 5.6); narrow the query or split it")

    def _has_nulls(self, meta, column):
        key = (self.real if meta.schema == "main" else self.text, meta.table, column)
        if key not in self._null_cache and (meta.table, column) in meta.nulls:
            self._null_cache[key] = meta.nulls[(meta.table, column)]
        if key not in self._null_cache:
            row = self.connection().execute(
                f'SELECT 1 FROM "{meta.table}" WHERE "{column}" IS NULL LIMIT 1').fetchone()
            self._null_cache[key] = row is not None
        return self._null_cache[key]

    # ---- search
    def search(self, index, query, fields, size, offset, sort):
        meta = self.meta(index)
        con = self.connection()
        where, params, density = self._where(meta, query, "search")
        sort_spec = self._sort_spec(meta, sort)
        nullable = {r.column: self._has_nulls(meta, r.column) for r, _ in sort_spec}
        after = offset
        if after is not None and not isinstance(after, list):
            after = [after]
        if after:
            if len(after) != len(sort_spec):
                raise es_error("illegal_argument_exception",
                               f"search_after has {len(after)} value(s) but sort has {len(sort_spec)}.")
            where = f"({where}) AND {self._after(sort_spec, after, params, nullable)}"
        # missing values sort last (ES default); the IS NULL key is dropped for columns without NULLs so
        # SQLite can walk an index for the ORDER BY
        order = [(f"({r.sql} IS NULL), " if nullable[r.column] else "") + f"{r.sql} {o.upper()}"
                 for r, o in sort_spec] + ["m.rowid ASC"]
        top, nested = self._wanted(meta, fields)
        key = meta.key
        select_cols = list(dict.fromkeys([key] + top + [r.column for r, _ in sort_spec]))
        size = int(size) if size is not None else 10
        if size < 0:
            raise es_error("illegal_argument_exception", f"[size] parameter cannot be negative, found [{size}]")
        # A broad filter (the stats say most rows pass) with a sort on an indexed non-key column: walk
        # that index in sort order and stop at the page size. Left alone the planner picks the filter's
        # index and sorts millions of rows, because it costs the scan without the LIMIT.
        hint = ""
        if density is not None and density >= self.STEER_DENSITY and sort_spec and not after \
                and sort_spec[0][0].column != key and not nullable[sort_spec[0][0].column]:
            ix = meta.single_index.get(sort_spec[0][0].column)
            if ix:
                hint = f' INDEXED BY "{ix}"'
        sql = (f'SELECT {", ".join(chr(34) + c + chr(34) for c in select_cols)} FROM "{meta.table}" m{hint} '
               f"WHERE {where} ORDER BY {', '.join(order)} LIMIT {size}")
        try:
            rows = self._run(sql, params)
        except sqlite3.OperationalError as e:
            if not hint or "no query solution" not in str(e):
                raise
            rows = self._run(sql.replace(hint, "", 1), params)
        rows = [dict(zip(select_cols, r)) for r in rows]
        docs = []
        for r in rows:
            src = {c: self._convert(meta, None, c, r[c]) for c in top}
            docs.append({"_index": index, "_id": r[key], "_source": src,
                         "sort": [r[ref.column] for ref, _ in sort_spec]})
        if docs and nested:
            ids = [r[key] for r in rows]
            by_id = {d["_id"]: d["_source"] for d in docs}
            marks = ",".join("?" * len(ids))
            for path, cols in nested.items():
                if path in meta.virtual:
                    continue
                child = meta.nested[path]
                q = (f'SELECT "_pid", {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{child}" '
                     f'WHERE "_pid" IN ({marks}) ORDER BY "_pid", "_ord"')
                for rec in con.execute(q, ids):
                    obj = {c: self._convert(meta, path, c, v) for c, v in zip(cols, rec[1:])}
                    by_id[rec[0]].setdefault(path, []).append(obj)
            for path, cols in nested.items():
                if path not in meta.virtual:
                    continue
                from API.lapse_group import groups_for

                for pid, objs in groups_for(con, self.catalog_facts(), ids).items():
                    if pid in by_id:
                        by_id[pid][path] = [{c: o.get(c) for c in cols} for o in objs]
        return {"took": 0, "timed_out": False,
                "hits": {"total": {"value": len(docs), "relation": "eq"}, "max_score": None, "hits": docs}}
