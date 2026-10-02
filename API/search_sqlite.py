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
import re
import sqlite3
import threading

from API.exceptions import SearchTimeoutError  # noqa: F401  (kept for parity with API.search)

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


def tokens(text):
    return [t.lower() for t in _TOKEN_RE.findall(str(text))]


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
        row = con.execute("SELECT tbl, key_field FROM _lapse_indices WHERE idx=?", (idx,)).fetchone()
        if row is None:
            # documented 501 for views whose data set is not in the snapshot (API/lapse_errors.py)
            from API.lapse_errors import LapseNotImplemented

            raise LapseNotImplemented(idx)
        self.index, self.table, self.key = idx, row[0], row[1]
        self.types = {}  # (path, field) -> (es_type, has_keyword_subfield)
        for path, field, t, kw in con.execute(
                "SELECT path, field, es_type, keyword_subfield FROM _lapse_fields WHERE idx=?", (idx,)):
            self.types[(path, field)] = (t, bool(kw))
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
        self.columns = [r[1] for r in con.execute(f'PRAGMA table_info("{self.table}")')]
        self.nested = {}  # path -> child table
        self.child_columns = {}
        prefix = self.table + "__"
        for t in sorted(tables):
            if t.startswith(prefix):
                path = t[len(prefix):]
                self.nested[path] = t
                self.child_columns[path] = [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')
                                            if r[1] not in ("_pid", "_ord")]
        self.fts = {}  # table -> set(columns)
        for t in tables:
            if t.startswith("fts_") and not t.endswith(("_data", "_idx", "_docsize", "_config", "_content")):
                base = t[4:]
                self.fts[base] = {r[1] for r in con.execute(f'PRAGMA table_info("{t}")')}

    def columns_of(self, path):
        return self.columns if path is None else self.child_columns.get(path, [])

    def table_of(self, path):
        return self.table if path is None else self.nested.get(path)

    def type_of(self, path, column):
        return self.types.get((path or "", column), ("keyword", False))


# ------------------------------------------------------------------ DSL -> SQL
class Translator:
    def __init__(self, meta):
        self.meta = meta
        self.params = []

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
        if not q:
            return "1"
        if not isinstance(q, dict) or len(q) != 1:
            raise es_error("parsing_exception", "[_na] query malformed, must start with start_object")
        kind, body = next(iter(q.items()))
        fn = getattr(self, "q_" + kind, None)
        if fn is None:
            raise es_error("parsing_exception", f"unknown query [{kind}]")
        return fn(body, ctx)

    def q_match_all(self, body, ctx):
        return "1"

    def q_bool(self, body, ctx):
        parts = []
        for clause in ("filter", "must"):
            subs = body.get(clause, [])
            subs = subs if isinstance(subs, list) else [subs]
            parts += [f"({self.where(s, ctx)})" for s in subs]
        should = body.get("should", [])
        should = should if isinstance(should, list) else [should]
        if should and not parts:
            parts.append("(" + " OR ".join(f"({self.where(s, ctx)})" for s in should) + ")")
        not_ = body.get("must_not", [])
        not_ = not_ if isinstance(not_, list) else [not_]
        # leaves are plain SQL predicates (index friendly); NULL only needs care under negation,
        # where ES treats a missing value as "does not match", so NOT(missing) is true
        parts += [f"NOT COALESCE(({self.where(s, ctx)}), 0)" for s in not_]
        return " AND ".join(parts) if parts else "1"

    def q_nested(self, body, ctx):
        alias, path = ctx
        npath = body.get("path")
        child = self.meta.nested.get(npath)
        if child is None or path is not None:
            return "0"
        inner = self.where(body.get("query", {}), ("c", npath))
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
        return f"({f.sql} = {self.p(f.value(v))}{self.kw_guard(f)})"

    def fts(self, f, expr):
        """Rows of f.table whose FTS column matches expr; falls back to a Python matcher when no FTS table."""
        cols = self.meta.fts.get(f.table, set())
        if f.column in cols:
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
        ors = []
        for v in values:
            if f.es_type == "text" and not f.keyword:
                # terms is not analyzed: only a single lowercase token can equal an indexed term
                s = coerce_keyword(v)
                toks = tokens(s)
                ors.append(self.fts(f, fts_quote(s)) if len(toks) == 1 and toks[0] == s else "0")
            else:
                ors.append(self.eq(f, v))
        return "(" + " OR ".join(ors) + ")"

    def q_range(self, body, ctx):
        field, spec = self.one(body, "range")
        if not isinstance(spec, dict):
            raise es_error("parsing_exception", f"[range] query malformed, no start_object after query name")
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
        for k, v in spec.items():
            if k not in ops:
                raise es_error("parsing_exception", f"[range] query does not support [{k}]")
            if f.es_type == "date" and not f.keyword:
                lo, hi, midnight = coerce_date(f.column, v)
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
            else:
                parts.append(f"{f.sql} {ops[k]} {self.p(f.value(v))}")
        return f"({' AND '.join(parts) or '1'}{self.kw_guard(f)})"

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
                return (f"(instr(lapse_lower({f.sql}), {self.p(inner)}) > 0 AND "
                        f"COALESCE(lapse_tok_wild({f.sql}, {self.p(low)}), 0))")
            return f"COALESCE(lapse_tok_wild({f.sql}, {self.p(low)}), 0)"
        pat = to_like(value)
        inner = value[1:-1] if qname == "wildcard" and len(value) > 2 and value[0] == value[-1] == "*" else None
        if ci and inner and not any(ch in inner for ch in "*?"):
            # plain substring (the _contains operator): instr() is much cheaper than a leading-% LIKE
            low = inner.lower()
            return (f"((instr(lower({f.sql}), {self.p(low)}) > 0 OR (octet_length({f.sql}) > length({f.sql}) AND "
                    f"instr(lapse_lower({f.sql}), {self.p(low)}) > 0)){self.kw_guard(f)})")
        if ci:
            # SQLite lower() folds ASCII only; values with non-ASCII characters go through Python str.lower()
            low = pat.lower()
            return (f"((lower({f.sql}) LIKE {self.p(low)} ESCAPE '\\' OR (octet_length({f.sql}) > length({f.sql}) AND "
                    f"lapse_lower({f.sql}) LIKE {self.p(low)} ESCAPE '\\')){self.kw_guard(f)})")
        return f"(lapse_like_cs({f.sql}, {self.p(pat)}){self.kw_guard(f)})"

    def q_prefix(self, body, ctx):
        return self._pattern(body, "prefix", ctx, lambda v: like_escape(v) + "%")

    def q_wildcard(self, body, ctx):
        return self._pattern(body, "wildcard", ctx, wildcard_to_like)


# ------------------------------------------------------------------ SQL functions
def _lapse_lower(s):
    return None if s is None else str(s).lower()


def _like_cs(value, pattern):
    if value is None:
        return 0
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


# ------------------------------------------------------------------ searcher
class LapseSQLiteSearch:
    _local = threading.local()
    _meta_cache = {}

    def __init__(self, path, timeout=60):
        self.path = path
        self.timeout = timeout

    @classmethod
    def from_django_settings(cls):
        from django.conf import settings

        cfg = settings.LAPSE_SQLITE
        return cls(path=cfg["path"], timeout=int(cfg.get("timeout", 60)))

    def connection(self):
        con = getattr(self._local, "con", None)
        if con is None or getattr(self._local, "path", None) != self.path:
            uri = "file:" + self.path.replace("\\", "/") + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=self.timeout)
            con.execute("PRAGMA case_sensitive_like=ON")
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA cache_size=-200000")
            con.execute("PRAGMA mmap_size=1073741824")
            con.create_function("lapse_lower", 1, _lapse_lower, deterministic=True)
            con.create_function("lapse_like_cs", 2, _like_cs, deterministic=True)
            con.create_function("lapse_fts_match", 2, _fts_match, deterministic=True)
            con.create_function("lapse_tok_range", 5, _tok_range, deterministic=True)
            con.create_function("lapse_tok_wild", 2, _tok_wild, deterministic=True)
            self._local.con, self._local.path = con, self.path
        return con

    def meta(self, index):
        key = (self.path, index)
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

    def _where(self, meta, query):
        tr = Translator(meta)
        sql = tr.where(self._query_body(query))
        return sql, tr.params

    def count(self, index, query):
        meta = self.meta(index)
        where, params = self._where(meta, query)
        sql = f'SELECT count(*) FROM "{meta.table}" m WHERE {where}'
        n = self._run(sql, params)[0][0]
        return {"count": n, "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0}}

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

    def _after(self, sort_spec, after, params):
        """Keyset condition equivalent to ES search_after with missing values sorted last."""
        if not after:
            return "1"
        ors = []
        for i, (ref, order) in enumerate(sort_spec):
            if i >= len(after):
                break
            ands, local = [], []
            for j, (ref_j, _) in enumerate(sort_spec[:i]):
                v = after[j]
                if v is None:
                    ands.append(f"{ref_j.sql} IS NULL")
                else:
                    local.append(ref_j.value(v))
                    ands.append(f"{ref_j.sql} = ?")
            v = after[i]
            if v is None:
                continue  # nothing sorts after a missing value within this key
            local.append(ref.value(v))
            op = ">" if order == "asc" else "<"
            ands.append(f"({ref.sql} {op} ? OR {ref.sql} IS NULL)")
            ors.append("(" + " AND ".join(ands) + ")")
            params.extend(local)
        return "(" + " OR ".join(ors) + ")" if ors else "0"

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
        if meta.type_of(path, col)[0] == "boolean":
            return bool(v)
        return v

    _null_cache = {}

    def _run(self, sql, params):
        """Execute with the configured timeout; an interrupted query surfaces as TimeoutError, which the
        upstream exception handler reports as 500 ERR_ES (same as an ES timeout)."""
        import time

        con = self.connection()
        deadline = time.monotonic() + self.timeout
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 200000)
        try:
            return con.execute(sql, params).fetchall()
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise TimeoutError("Search timed out")
            raise
        finally:
            con.set_progress_handler(None, 0)

    def _has_nulls(self, meta, column):
        key = (self.path, meta.table, column)
        if key not in self._null_cache:
            row = self.connection().execute(
                f'SELECT 1 FROM "{meta.table}" WHERE "{column}" IS NULL LIMIT 1').fetchone()
            self._null_cache[key] = row is not None
        return self._null_cache[key]

    # ---- search
    def search(self, index, query, fields, size, offset, sort):
        meta = self.meta(index)
        con = self.connection()
        where, params = self._where(meta, query)
        sort_spec = self._sort_spec(meta, sort)
        after = offset
        if after is not None and not isinstance(after, list):
            after = [after]
        if after:
            if len(after) != len(sort_spec):
                raise es_error("illegal_argument_exception",
                               f"search_after has {len(after)} value(s) but sort has {len(sort_spec)}.")
            where = f"({where}) AND {self._after(sort_spec, after, params)}"
        # missing values sort last (ES default); the IS NULL key is dropped for columns without NULLs so
        # SQLite can walk an index for the ORDER BY
        order = [(f"({r.sql} IS NULL), " if self._has_nulls(meta, r.column) else "") + f"{r.sql} {o.upper()}"
                 for r, o in sort_spec] + ["m.rowid ASC"]
        top, nested = self._wanted(meta, fields)
        key = meta.key
        select_cols = list(dict.fromkeys([key] + top + [r.column for r, _ in sort_spec]))
        size = int(size) if size is not None else 10
        if size < 0:
            raise es_error("illegal_argument_exception", f"[size] parameter cannot be negative, found [{size}]")
        sql = (f'SELECT {", ".join(chr(34) + c + chr(34) for c in select_cols)} FROM "{meta.table}" m '
               f"WHERE {where} ORDER BY {', '.join(order)} LIMIT {size}")
        rows = [dict(zip(select_cols, r)) for r in self._run(sql, params)]
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
                child = meta.nested[path]
                q = (f'SELECT "_pid", {", ".join(chr(34) + c + chr(34) for c in cols)} FROM "{child}" '
                     f'WHERE "_pid" IN ({marks}) ORDER BY "_pid", "_ord"')
                for rec in con.execute(q, ids):
                    obj = {c: self._convert(meta, path, c, v) for c, v in zip(cols, rec[1:])}
                    by_id[rec[0]].setdefault(path, []).append(obj)
        return {"took": 0, "timed_out": False,
                "hits": {"total": {"value": len(docs), "relation": "eq"}, "max_score": None, "hits": docs}}
