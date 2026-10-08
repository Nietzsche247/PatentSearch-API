"""Query cost estimate before execution (PatentRef checklist gate 5.6, "query cost cap").

The estimate is computed from the request's `q`, `o` and `s` as the client wrote them (the upstream
grammar, before the parser turns them into Elasticsearch DSL), so it costs nothing and runs before any
SQL. It is a rough unit count of the work the SQLite translator (`API/search_sqlite.py`) will do, with
the weights below; it is not a prediction of milliseconds. A query above `LAPSE_QUERY_COST_LIMIT` is
refused with the upstream 400 shape (`{"error": true}`, `X-Status-Reason` naming the cost, the limit and
the reason, `X-Status-Reason-Code: ERR_Q`), raised as `InvalidQueryStringError` so the unchanged
exception handler produces it. The per-statement timeout and the per-request budget in the searcher
remain the backstop for whatever the estimate misses.

Weights (units):
  leaf criterion (equality, range, list element)        1 each; a list value counts one per element
  a set of identifiers                                  1, plus 1 per 100 values, and one criterion however many
                                                        values: a list value on an id field ({"patent_id": [...]},
                                                        {"_eq": {"assignee_id": [...]}}) or an _or whose branches are
                                                        all scalar equalities on the same id field (what the R
                                                        package's qry_funs$eq(assignee_id = ids) sends). An id field
                                                        is patent_id, document_number or any field ending in _id; each
                                                        value is one index lookup, and Elasticsearch counted a terms
                                                        query as one clause. The documented R vignettes send 75 and
                                                        690 ids (gate 2.7).
  _begins                                               2 (4 when the prefix is shorter than 3 characters: no trigram prefilter)
  _contains                                             6 (20 when the needle is shorter than 3 characters or carries * or ?: a full scan)
  _text_all, _text_any, _text_phrase                    4 each (FTS posting lists; a cold text page is seconds)
  _not, _neq                                            +2 each (a complement)
  each distinct nested group used (dotted field)        +3 (one child-table join or IN set per criterion)
  _or                                                   +1 per branch beyond the first
  o.size                                                +1 per 100 rows (a 1000-row page is +10)
  sort                                                  +2 per sort field; +10 when a sort field is a text field (no index)
Hard limits, refused regardless of the sum: nesting deeper than `LAPSE_QUERY_MAX_DEPTH` (8) and more
than `LAPSE_QUERY_MAX_CRITERIA` (64) leaf criteria (a set of identifiers counts once). The `q` size cap
(`LAPSE_MAX_Q_BYTES`, 16 KB) bounds an id set at about 1,500 values.

Every contract example, every operator-matrix cell and every pagination and quirks request sits well
under the limit (the heaviest contract example is under 30 units); the figures are in the patentref
repo's audit `ops/audits/2026-10-06_gate-5.6_abuse-limits_patentref-us1.txt`.
"""
from API.exceptions import InvalidQueryStringError

TEXT_OPS = ("_text_all", "_text_any", "_text_phrase")
RANGE_OPS = ("_gt", "_gte", "_lt", "_lte")
BOOL_OPS = ("_and", "_or", "_not")
LEAF_OPS = ("_eq", "_neq", "_begins", "_contains") + TEXT_OPS + RANGE_OPS

W_LEAF = 1
W_BEGINS, W_BEGINS_SHORT = 2, 4
W_CONTAINS, W_CONTAINS_SCAN = 6, 20
W_TEXT = 4
W_NOT = 2
W_NESTED = 3
W_OR_BRANCH = 1
W_PER_100_ROWS = 1
W_SORT, W_SORT_TEXT = 2, 10
ID_SET_VALUES_PER_UNIT = 100
ID_FIELDS = ("patent_id", "document_number")


class Estimate:
    def __init__(self):
        self.cost = 0
        self.criteria = 0
        self.depth = 0
        self.nested = set()
        self.notes = []

    def add(self, units, note=None):
        self.cost += units
        if note:
            self.notes.append(note)


def _field_of(body):
    """The one field name in an operator body, or None when the body is not that shape."""
    if isinstance(body, dict) and len(body) == 1:
        return next(iter(body))
    return None


def _note_nested(est, field):
    if isinstance(field, str) and "." in field:
        group = field.split(".", 1)[0]
        if group not in est.nested:
            est.nested.add(group)
            est.add(W_NESTED, f"nested group {group}")


def _value_units(v):
    return len(v) if isinstance(v, list) else 1


def _is_id_field(field):
    if not isinstance(field, str):
        return False
    leaf = field.rsplit(".", 1)[-1]
    return leaf in ID_FIELDS or leaf.endswith("_id")


def _scalar_eq(q):
    """(field, value) when q is one scalar equality on one field, {"f": v} or {"_eq": {"f": v}}; else None."""
    if not isinstance(q, dict) or len(q) != 1:
        return None
    field, value = next(iter(q.items()))
    if field == "_eq":
        inner = _field_of(value)
        if inner is None:
            return None
        field, value = inner, value[inner]
    elif field.startswith("_"):
        return None
    if isinstance(value, (dict, list)):
        return None
    return field, value


def _id_set(est, field, n):
    """One criterion for n identifiers on one field: n index lookups, one IN set."""
    est.criteria += 1
    _note_nested(est, field)
    est.add(W_LEAF + n // ID_SET_VALUES_PER_UNIT, f"{n} values of {field}" if n >= ID_SET_VALUES_PER_UNIT else None)


def _walk(q, est, depth):
    if depth > est.depth:
        est.depth = depth
    if not isinstance(q, dict) or len(q) != 1:
        est.criteria += 1
        est.add(W_LEAF)
        return
    op, body = next(iter(q.items()))
    if op == "_or" and isinstance(body, list) and len(body) > 1:
        leaves = [_scalar_eq(s) for s in body]
        fields = {leaf[0] for leaf in leaves if leaf is not None}
        if None not in leaves and len(fields) == 1 and _is_id_field(next(iter(fields))):
            est.depth = max(est.depth, depth + 1)
            _id_set(est, next(iter(fields)), len(body))
            return
    if op == "_and" or op == "_or":
        subs = body if isinstance(body, list) else [body]
        for s in subs:
            _walk(s, est, depth + 1)
        if op == "_or" and len(subs) > 1:
            est.add(W_OR_BRANCH * (len(subs) - 1))
        return
    if op == "_not":
        est.add(W_NOT)
        _walk(body, est, depth + 1)
        return
    if op in LEAF_OPS:
        field = _field_of(body)
        value = body[field] if field is not None else None
        if op == "_eq" and isinstance(value, list) and _is_id_field(field):
            _id_set(est, field, len(value))
            return
        n = _value_units(value)
        est.criteria += n
        _note_nested(est, field)
        if op == "_neq":
            est.add(W_LEAF * n + W_NOT)
        elif op == "_begins":
            short = any(len(str(x)) < 3 for x in (value if isinstance(value, list) else [value]))
            est.add((W_BEGINS_SHORT if short else W_BEGINS) * n)
        elif op == "_contains":
            vals = value if isinstance(value, list) else [value]
            scan = any(len(str(x)) < 3 or "*" in str(x) or "?" in str(x) for x in vals)
            est.add((W_CONTAINS_SCAN if scan else W_CONTAINS) * n)
        elif op in TEXT_OPS:
            est.add(W_TEXT * n)
        else:
            est.add(W_LEAF * n)
        return
    # a plain field: equality (a list is a terms query)
    if isinstance(body, list) and _is_id_field(op):
        _id_set(est, op, len(body))
        return
    n = _value_units(body)
    est.criteria += n
    _note_nested(est, op)
    est.add(W_LEAF * n)


def estimate(q, o=None, s=None, text_fields=()):
    """The Estimate for a request; never raises."""
    est = Estimate()
    try:
        _walk(q if q is not None else {}, est, 1)
        size = (o or {}).get("size", 100) if isinstance(o, dict) else 100
        try:
            size = min(max(int(size), 1), 1000)
        except (TypeError, ValueError):
            size = 100
        if size > 100:
            est.add(W_PER_100_ROWS * (size // 100), f"page of {size} rows")
        for spec in (s or []):
            if isinstance(spec, dict) and len(spec) == 1:
                field = next(iter(spec))
                est.add(W_SORT_TEXT if field in text_fields else W_SORT, f"sort on {field}")
    except Exception:  # an estimate must never break a request; the parser will judge the shape
        pass
    return est


def check(q, o=None, s=None, text_fields=(), limit=None, max_depth=None, max_criteria=None, max_q_bytes=None):
    """Raise `InvalidQueryStringError` (400 ERR_Q) when the request is over a limit; return the Estimate."""
    from django.conf import settings

    limit = limit if limit is not None else int(getattr(settings, "LAPSE_QUERY_COST_LIMIT", 100))
    max_depth = max_depth if max_depth is not None else int(getattr(settings, "LAPSE_QUERY_MAX_DEPTH", 8))
    max_criteria = max_criteria if max_criteria is not None else int(getattr(settings, "LAPSE_QUERY_MAX_CRITERIA", 64))
    max_q_bytes = max_q_bytes if max_q_bytes is not None else int(getattr(settings, "LAPSE_MAX_Q_BYTES", 16384))
    if q is not None:
        import json

        try:
            nbytes = len(json.dumps(q, separators=(",", ":")).encode("utf-8"))
        except (TypeError, ValueError):
            nbytes = 0
        if nbytes > max_q_bytes:
            raise InvalidQueryStringError(f"'q' parameter too large: {nbytes} bytes, the limit is {max_q_bytes}")
    est = estimate(q, o, s, text_fields)
    if est.depth > max_depth:
        raise InvalidQueryStringError(f"Query too deeply nested: depth {est.depth}, the limit is {max_depth}")
    if est.criteria > max_criteria:
        raise InvalidQueryStringError(f"Query has too many criteria: {est.criteria}, the limit is {max_criteria}")
    if est.cost > limit:
        why = "; ".join(est.notes[:4])
        raise InvalidQueryStringError(
            f"Query too expensive: estimated cost {est.cost} units, the limit is {limit}"
            + (f" ({why})" if why else "")
            + ". Use fewer text, _contains or nested criteria, a smaller page, or split the query."
        )
    return est
