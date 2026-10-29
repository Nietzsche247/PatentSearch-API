"""PatentRef's structure layer, the triage table (gate S1.8L): up to 500 patents in one table, every part computed on read.

  POST /api/v1/structure/triage/   a JSON body: {"patents": [...] or "<a pasted list>"} or {"search": "<words>", "limit": n},
                                   with "components" (optional) and "output" (json, the default, or csv)
  GET  /api/v1/structure/triage/?patents=...|search=...&limit=&components=&output=   the same fields as parameters

What a row holds, per patent, in the order of the list (a list) or in the claims index's reverse load order (a search):
  - status: what the status engine gives for the patent (lapse.expiry.expiry_record in the patentref repo, through
    API/lapse_group.py, the code behind GET /api/v1/lapse/status/<patent_id>/): status, basis, reinstatement_possible
    with its basis, the estimated term end, the lapse and reinstatement dates and as_of, as that endpoint states them.
  - cells: one per component of the table, where the claims mention it: every_independent (each independent claim does),
    some_independent (some independent claims do), only_dependent (no independent claim does, a dependent claim does) or
    absent (no claim does), with the claims and the first mention as a quote. A claim mentions a component when one of its
    term mentions (claimgraph's "a widget", "said widget") has the component's key, or refers back to a mention that has
    it. match: exact (the same key) or partial (one key ends with the other as whole words, "drive motor" and "electric
    drive motor"), the rule of the structured mode of GET /api/v1/lapse/similar.
  - components: the patent's own component keys (the keys of the terms its claims introduce), grouped by those positions.
  - claim_parts: each independent claim as its parts in text order: the preamble, the transition, then every element and
    wherein clause, each a quote.
A quote is {claim, claim_id, part_id or term_id, start, end, text}: text is the claim text from start to end, compared with
the span claimgraph read before the answer leaves.

The table's components: the ones the request names (at most MAX_COMPONENTS, each read as a claim tree's term key: the noun
phrase claimgraph's term finder reads, lower case, the last word singular); when it names none, the TABLE_COMPONENTS keys
that the most patents of the table introduce, weighed by how rare their rarest word is in titles and abstracts (ln(N / df),
the weights of the structured mode of lapse/similar), each introduced in at least two patents of the table.

The have and lacks marks are not here. The page (/triage/, lapse_accounts/triage_page.py) keeps them in the browser and
writes them into its own export; the server never receives a mark, and nothing here holds a patent against anything but
the words a request names.

Computed on read, never stored: per patent, the facts a row needs, built from the claim rows of the long-text file by
claimgraph (through API/lapse_claimgraph.py) and kept in a bounded per-worker cache (FACTS_ENTRIES answers and
FACTS_BYTES bytes, keyed by patent, both data versions and the structure version); the word counts the table's own pick
weighs in a second one (DF_ENTRIES words per worker, keyed by data version). No corpus-wide index: a search reads the
existing FTS5 index of the claims (txt.fts_g_claims) from its last row back and stops at its limit.

Bounds, so a table of 500 patents cannot hold the workers: one table at a time per key (LAPSE_TRIAGE_PER_KEY) and at most
LAPSE_TRIAGE_TOTAL tables at once on the server, 429 with Retry-After beyond either (TriageSlots, released when the answer
leaves, as the per-key in-flight cap of gate 5.6 is); each read under the claim tree's deadline (5 s); and a time limit for
the table (TIME_LIMIT_SECONDS): a row not built by then says so (built false, note time_limit) and the answer says complete
false. Same key, throttles, metering (one request toward the month, whatever the number of patents) and error forms as
every /api/v1/ data path. No model call anywhere.
"""
import collections
import csv
import io
import json
import logging
import math
import re
import sqlite3
import threading
import time

from django.conf import settings
from django.http import HttpResponse
from rest_framework.exceptions import ParseError
from rest_framework.throttling import BaseThrottle
from rest_framework.views import APIView

from API import lapse_claimgraph as cg
from API import lapse_group, lapse_structure, lapse_wording
from API.lapse_views import _as_of, _error, base_url, normalize_pid
from API.search import get_searcher
from API.search_sqlite import fts_quote, plain, tokens

logger = logging.getLogger(__name__)

MAX_PATENTS = 500
MAX_COMPONENTS = 12
MAX_COMPONENT_CHARS = 80
MAX_SEARCH_TERMS = 12          # words and quoted phrases
MAX_SEARCH_CHARS = 500
SEARCH_LIMIT_DEFAULT = 100
TABLE_COMPONENTS = 6           # components the table picks when the request names none
TABLE_CANDIDATES = 200         # keys weighed for that pick, the most common first
TIME_LIMIT_SECONDS = 20.0      # the whole table; the search answers within 30 s on the documented request budget
SEARCH_SECONDS = lapse_structure.READ_SECONDS
FACTS_ENTRIES = 2048           # patents kept per worker process
FACTS_BYTES = 16 * 1024 * 1024   # bytes kept per worker process (8 workers: 128 MB at most)
FACTS_ITEM_MAX = 1024 * 1024
DF_ENTRIES = 65536             # words whose count in titles and abstracts is kept per worker, for the table's own pick
UNREAD_SHOWN = 20
POSITIONS = ("every_independent", "some_independent", "only_dependent", "absent")
NOTES = {   # a row's note when it has no cells and claim_parts; the docs give each the same sentence
    "not_in_data": "The data in service has no patent with this number.",
    "no_claim_text": "The long-text file in service has no claims for this patent (granted after its last grant date).",
    "work_cap": "Its claims are over the claim tree's work cap (1,000 claim rows, 1,000,000 characters).",
    "offset_check": "Its claim tree failed its offset check, so nothing of it is shown.",
    "time_limit": "Not read: the table reached its time limit. Send this patent again.",
}
FIELDS = ("patents", "search", "limit", "components", "output")
OUTPUTS = ("json", "csv")
EXPORT_COLUMNS = ["patent_id", "title", "status", "status_basis", "reinstatement_possible", "as_of", "note", "row_kind",
                  "component", "position", "match", "claim", "claim_id", "part_id", "label", "quote", "start", "end", "mark"]
CACHE_CONTROL = "private, max-age=60"
SCOPE_SLOTS = "triage"
# PatentRef's own words in an answer are run past these before it leaves (the structure layer's wording rule): never a
# word that says what a claim reaches or whether something may be made, only what the text says and where.
BANNED_WORDS = ("risk", "clear", "safe", "free to use", "invalid", "valid", "scope", "breadth", "strength", "broad", "narrow",
                "infring")
COVER_WORD = re.compile(r"\bcover(?:s|ed|age)?\b")
# the fields whose values are the patent's words, the request's words or claimgraph's keys, left out of that check
QUOTED_FIELDS = frozenset({"text", "title", "key", "claim_keys", "asked", "search", "terms", "unread", "unread_components",
                           "label", "every_independent", "some_independent", "only_dependent"})


def per_key():
    return int(getattr(settings, "LAPSE_TRIAGE_PER_KEY", 1))


def total_slots():
    return int(getattr(settings, "LAPSE_TRIAGE_TOTAL", 3))


def busy_reason():
    return (f"The triage table builds one table at a time per key and at most {total_slots()} at once on the server; "
            "this one waits for a turn. Retry-After says when to send it again.")


FACTS = lapse_structure.AnswerCache(entries=FACTS_ENTRIES, max_bytes=FACTS_BYTES, item_max=FACTS_ITEM_MAX)
_DF = collections.OrderedDict()   # (data_version, word): documents of titles and abstracts holding the word
_DF_LOCK = threading.Lock()


class Refusal(Exception):
    """A request the table does not answer: (reason, X-Status-Reason-Code, status)."""

    def __init__(self, reason, code="ERR_Q", status=400):
        super().__init__(reason)
        self.reason, self.code, self.status = reason, code, status


class _OffsetMismatch(Exception):
    pass


# ---------------------------------------------------------------- the slots (one table at a time per key)

class TriageSlots(BaseThrottle):
    """One table at a time per key and at most LAPSE_TRIAGE_TOTAL at once across the workers, on the throttles' atomic
    window counters (lapse_accounts.ratelimit). Runs after the default throttles and holds nothing when one of them
    refused. The slots join the request's in-flight reservations, which AbuseLimitMiddleware gives back when the answer
    leaves; a slot a crashed worker never gave back ends with its clock minute, as the gate 5.6 in-flight cap does."""

    def __init__(self):
        self._wait = 2

    def wait(self):
        return self._wait

    def allow_request(self, request, view):
        from lapse_accounts import ratelimit, throttling

        http_req = getattr(request, "_request", request)
        if getattr(http_req, "_lapse_denied", False):
            return True
        subject, _, _ = throttling._key_subject(request)
        if subject is None:
            return True   # no usable key: the key permission answers 403 first
        window, _ = ratelimit.minute_window(SCOPE_SLOTS, 60, throttling.timer())
        held = []
        for subj, limit in (("triage:" + subject, per_key()), ("triage:all", total_slots())):
            count = ratelimit.bump(ratelimit.WINDOW_TABLE, subj, "win", window, limit)
            if count is None:
                for s2, w2 in held:
                    ratelimit.refund(ratelimit.WINDOW_TABLE, s2, "win", w2)
                http_req._lapse_denied = True
                http_req._lapse_triage_busy = True
                return False
            if count == 1:
                ratelimit.drop_old_windows(subj, window)
            held.append((subj, window))
        http_req._lapse_inflight = list(getattr(http_req, "_lapse_inflight", None) or []) + held
        return True


# ---------------------------------------------------------------- reading the request

_THOUSANDS = re.compile(r"(?<!\d)(?<!\d,)(\d{1,3}(?:,\d{3})+)(?!\d|,\d)")   # "10,905,426", "D500,396"; never inside "123,456789"
_JOIN = re.compile(r"\b(US|RE|PP|D|H|T|AI|X)[\s\-]+(?=(?:RE|PP|D|H|T|AI|X)?[\s\-]*\d)")
_SPLIT = re.compile(r"[\s,;|]+")
_KIND = re.compile(r"^(.*\d)(?:[ABEHPSU]\d?)$")
_KIND_ALONE = re.compile(r"^[ABEHPSU]\d?$")
_FILLER = frozenset({"US", "U.S.", "U.S", "PAT", "PAT.", "PATENT", "PATENTS", "NO", "NO.", "NOS", "NOS.", "NUMBER", "NUMBERS",
                     "AND", "&"})
_COMPONENT_SPLIT = re.compile(r"[,;\n\r]+")
_SEARCH_PARTS = re.compile(r'"([^"]*)"|([^\s"]+)')


def parse_patent_list(text):
    """(patent ids in the order given, the pieces that are not patent numbers) from pasted text: numbers separated by
    spaces, commas, semicolons or new lines, with or without US, thousands separators ("10,905,426"), hyphens
    ("US-10905426-B2") and a kind code (B1, B2, E, S, P2)."""
    t = (text or "").upper()
    t = _THOUSANDS.sub(lambda m: m.group(1).replace(",", ""), t)
    t = _JOIN.sub(lambda m: m.group(1), t)
    ids, unread = [], []
    for tok in _SPLIT.split(t):
        tok = tok.strip(".()[]{}\"'")
        if not tok or tok in _FILLER or _KIND_ALONE.match(tok):
            continue
        flat = tok.replace("-", "")
        m = _KIND.match(flat)
        pid = normalize_pid(flat) or (normalize_pid(m.group(1)) if m else None)
        if pid:
            ids.append(pid)
        else:
            unread.append(tok)
    return ids, unread


def component_key(text):
    """The claim tree term key of a component as a person writes it ("drive motors", "a filter bag"): the first noun phrase
    claimgraph's term finder reads, else the words themselves as a key; None when it holds no word."""
    t = " ".join((text or "").split())
    for m in cg.find_mentions(t, 0, len(t), bare="introduces"):
        if m.key:
            return m.key
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-/']*", t)
    return cg.normalize_key(words[:6]) if words else None


def search_terms(text):
    """The search's terms: each word a term, each "quoted phrase" a phrase (tokens as the index splits them), in order, once."""
    terms = []
    for m in _SEARCH_PARTS.finditer(text or ""):
        if m.group(1) is not None:
            got = [" ".join(tokens(m.group(1)))] if tokens(m.group(1)) else []
        else:
            got = tokens(m.group(2))
        for t in got:
            if t and t not in terms:
                terms.append(t)
    return terms


def _strings(value, name):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, list, tuple)):
        raise Refusal(f"'{name}' must be a string or a list of strings")
    items = list(value) if isinstance(value, (list, tuple)) else [value]
    if any(isinstance(x, bool) or not isinstance(x, (str, int)) for x in items):
        raise Refusal(f"'{name}' must be a string or a list of strings")
    return [str(x) for x in items]


def read_input(params):
    """The request's fields, read and bounded, or Refusal (400 ERR_Q)."""
    unknown = sorted(k for k in params if k not in FIELDS)
    if unknown:
        raise Refusal(f"Unknown field {unknown[0]!r}: the triage table takes {', '.join(FIELDS)}")
    output = params.get("output") or "json"
    if output not in OUTPUTS:
        raise Refusal("'output' must be json or csv")
    patents, search = _strings(params.get("patents"), "patents"), params.get("search")
    has_list = bool(patents) and any(x.strip() for x in patents)
    has_search = isinstance(search, str) and bool(search.strip())
    if search is not None and not isinstance(search, str):
        raise Refusal("'search' must be a string")
    if has_list == has_search:
        raise Refusal("Send 'patents' (up to 500 patent numbers) or 'search' (words to find in the claims), one of the two")
    req = {"output": output}
    if has_list:
        if params.get("limit") not in (None, ""):
            raise Refusal("'limit' goes with 'search'; a list is read whole")
        ids, unread, repeated = [], [], 0
        for item in patents:
            got, bad = parse_patent_list(item)
            unread += bad
            for pid in got:
                if pid in ids:
                    repeated += 1
                else:
                    ids.append(pid)
        if not ids:
            raise Refusal("No patent number could be read in 'patents'")
        if len(ids) > MAX_PATENTS:
            raise Refusal(f"At most {MAX_PATENTS} patent numbers in one table; this list has {len(ids)}")
        req.update(kind="list", ids=ids, unread=unread, repeated=repeated)
    else:
        if len(search) > MAX_SEARCH_CHARS:
            raise Refusal(f"'search' over {MAX_SEARCH_CHARS} characters")
        terms = search_terms(search)
        if not terms:
            raise Refusal("'search' has no words")
        if len(terms) > MAX_SEARCH_TERMS:
            raise Refusal(f"'search' takes at most {MAX_SEARCH_TERMS} words and quoted phrases")
        limit = params.get("limit")
        try:
            limit = SEARCH_LIMIT_DEFAULT if limit in (None, "") else int(limit)
        except (TypeError, ValueError):
            raise Refusal(f"'limit' must be a whole number from 1 to {MAX_PATENTS}") from None
        if isinstance(params.get("limit"), bool) or limit < 1 or limit > MAX_PATENTS:
            raise Refusal(f"'limit' must be a whole number from 1 to {MAX_PATENTS}")
        req.update(kind="search", search=search, terms=terms, limit=limit)
    asked, keys, unread_c = [], [], []
    for item in _strings(params.get("components"), "components") or []:
        for piece in _COMPONENT_SPLIT.split(item):
            piece = " ".join(piece.split())
            if not piece:
                continue
            if len(piece) > MAX_COMPONENT_CHARS:
                raise Refusal(f"A component is over {MAX_COMPONENT_CHARS} characters")
            k = component_key(piece)
            if not k:
                unread_c.append(piece)
            elif k not in keys:
                keys.append(k)
                asked.append(piece)
    if len(keys) > MAX_COMPONENTS:
        raise Refusal(f"At most {MAX_COMPONENTS} components in one table")
    req.update(components=list(zip(keys, asked)), unread_components=unread_c)
    return req


# ---------------------------------------------------------------- one patent's facts (cached)

def claim_rows(rows):
    t = lapse_structure._text
    return [cg.ClaimRow(t(p), t(seq), t(x), t(dep), t(num), t(ex)) for p, seq, x, dep, num, ex in rows]


def _quote(claim, start, end, span_text, id_name, part_id):
    text = claim["text"][start:end]
    if not text or text != span_text:
        raise _OffsetMismatch()
    return {"claim": claim["number"], "claim_id": claim["claim_id"], id_name: part_id, "start": start, "end": end, "text": text}


def patent_facts(pid, rows):
    """What the rows of one patent need, from its claim rows: the independent claims, every key its claims mention with the
    claims that mention it and its first mention, and the claim_parts. None when the tree or a quote fails its check."""
    graph = cg.build_patent(pid, claim_rows(rows))
    if cg.check_patent(graph):
        return None
    claims = graph["claims"]
    by_term = {t["term_id"]: t for c in claims for t in c["terms"]}
    keys = {}
    try:
        for c in claims:
            side = "i" if c["independent"] else "d"
            for t in c["terms"]:
                own = t["key"]
                if not own:
                    continue
                names = [own]
                ante = by_term.get(t.get("antecedent") or "")
                if ante is not None and ante["key"] and ante["key"] != own:
                    names.append(ante["key"])   # "the device" that refers back to "an electronic device"
                for k in names:
                    e = keys.setdefault(k, {"i": [], "d": [], "q": None, "r": None, "n": False})
                    if c["claim_id"] not in e[side]:
                        e[side].append(c["claim_id"])
                e = keys[own]
                e["n"] = e["n"] or t["role"] == "introduces"
                rank = [0 if c["independent"] else 1, c["sequence"], t["start"]]
                if e["r"] is None or rank < e["r"]:
                    e["r"] = rank
                    e["q"] = _quote(c, t["start"], t["end"], t["text"], "term_id", t["term_id"])
        claim_parts = []
        for c in claims:
            if not c["independent"]:
                continue
            spans = []
            if c["preamble"]:
                spans.append(("preamble", c["preamble"], c["preamble"]["unit_id"]))
            if c["transition"]:
                spans.append(("transition", c["transition"], c["transition"]["unit_id"]))
            spans += [(el["kind"], el, el["element_id"]) for el in c["elements"]]
            parts = []
            for kind, span, part_id in spans:
                item = dict(part=kind, **_quote(c, span["start"], span["end"], span["text"], "part_id", part_id))
                if span.get("label"):
                    item["label"] = span["label"]
                if span.get("opens_list"):
                    item["opens_list"] = True
                parts.append(item)
            claim_parts.append({"claim": c["number"], "claim_id": c["claim_id"], "category": c["category"], "parts": parts})
    except _OffsetMismatch:
        return None
    independent = [c for c in claims if c["independent"]]
    return {"claims": len(claims), "independent": [c["number"] for c in independent], "ni": len(independent),
            "numbers": {c["claim_id"]: c["number"] for c in claims},
            "keys": {k: {"i": e["i"], "d": e["d"], "q": e["q"], "n": e["n"]} for k, e in keys.items()},
            "claim_parts": claim_parts}


def facts_for(con, pid, versions):
    """(facts or None, note or None, from the cache?) for one patent; reads its claim rows only on a miss."""
    ck = (pid,) + tuple(versions) + (cg.STRUCTURE_VERSION,)
    hit = FACTS.get(ck)
    if hit is not None:
        got = json.loads(hit)
        return got["facts"], got["note"], True
    rows = lapse_structure._read(con, lapse_structure.CLAIMS_SQL, (pid, lapse_structure.MAX_CLAIMS + 1))
    facts, note = None, None
    if not rows:
        note = "no_claim_text"
    elif len(rows) > lapse_structure.MAX_CLAIMS:
        note = "work_cap"
    else:
        rows = [(p, seq, lapse_structure._text(x), dep, num, ex) for p, seq, x, dep, num, ex in rows]
        if sum(len(r[2]) for r in rows) > lapse_structure.MAX_TEXT_CHARS:
            note = "work_cap"
        else:
            facts = patent_facts(pid, rows)
            if facts is None:
                note = "offset_check"
                logger.error("triage: the claim tree of %s failed its offset check", pid)
    FACTS.put(ck, json.dumps({"facts": facts, "note": note}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return facts, note, False


# ---------------------------------------------------------------- positions and cells

def position(independent_with, independent, dependent_with):
    """Where the claims mention a component: the count of independent claims that do, of all, and whether a dependent one does."""
    if independent and independent_with >= independent:
        return "every_independent"
    if independent_with:
        return "some_independent"
    if dependent_with:
        return "only_dependent"
    return "absent"


def _heads(facts):
    heads = {}
    for k in facts["keys"]:
        heads.setdefault(k.rsplit(" ", 1)[-1], []).append(k)
    return heads


def cell(facts, comp, heads):
    """One component in one patent's claims: the position, the match, the keys it matched and the first mention."""
    keys = facts["keys"]
    partial = [k for k in heads.get(comp.rsplit(" ", 1)[-1], ()) if k != comp and (k.endswith(" " + comp) or comp.endswith(" " + k))]
    partial.sort(key=lambda k: (-min(len(k.split()), len(comp.split())), k))
    matched = ([comp] if comp in keys else []) + partial
    if not matched:
        return {"key": comp, "position": "absent", "match": None, "claim_keys": [], "independent_claims": [],
                "dependent_claims": [], "quote": None}
    ind, dep = set(), set()
    for k in matched:
        ind.update(keys[k]["i"])
        dep.update(keys[k]["d"])
    nums = facts["numbers"]
    return {"key": comp, "position": position(len(ind), facts["ni"], bool(dep)), "match": "exact" if comp in keys else "partial",
            "claim_keys": matched[:3], "independent_claims": sorted({nums[x] for x in ind}),
            "dependent_claims": sorted({nums[x] for x in dep}), "quote": keys[matched[0]]["q"]}


def own_components(facts):
    """The patent's own component keys (introduced by one of its claims), grouped by position, in order of first mention."""
    out = {p: [] for p in POSITIONS[:3]}
    for k, e in facts["keys"].items():
        if e["n"]:
            out[position(len(e["i"]), facts["ni"], bool(e["d"]))].append(k)
    return out


def doc_freqs(con, data_version, words):
    """{word: documents of titles and abstracts holding it}: the structured mode of lapse/similar's lookup in the
    vocabulary of fts_patents for the words not yet kept, then kept per data version (a word's count changes only with
    the file) in a bounded per-worker cache of DF_ENTRIES words. An answer of zeros only (a file without the vocabulary)
    is not kept."""
    from API.lapse_similar_structured import _doc_freqs

    out, need = {}, []
    with _DF_LOCK:
        for w in words:
            got = _DF.get((data_version, w))
            if got is None:
                need.append(w)
            else:
                _DF.move_to_end((data_version, w))
                out[w] = got
    if need:
        fresh = _doc_freqs(con, need)
        if any(fresh.values()):
            with _DF_LOCK:
                for w, n in fresh.items():
                    _DF[(data_version, w)] = n
                    _DF.move_to_end((data_version, w))
                while len(_DF) > DF_ENTRIES:
                    _DF.popitem(last=False)
        out.update(fresh)
    return out


def table_components(con, built, data_version=""):
    """[(key, patents introducing it)] when the request names no component: the TABLE_COMPONENTS keys the most patents of
    the table introduce (in at least two of them when the table has two or more), weighed by ln(N / df) of the key's rarest
    word in titles and abstracts; ties by the number of patents, then the key."""
    count = {}
    for facts in built:
        for k, e in facts["keys"].items():
            if e["n"] and any(len(w) >= 3 for w in tokens(k)):
                count[k] = count.get(k, 0) + 1
    need = 2 if len(built) >= 2 else 1
    cands = sorted((k for k, n in count.items() if n >= need), key=lambda k: (-count[k], k))[:TABLE_CANDIDATES]
    if not cands:
        return []
    from API.lapse_similar_structured import _total   # the weights of lane Y's structured mode

    total = _total(con)
    df = doc_freqs(con, data_version, sorted({w for k in cands for w in tokens(k)}))

    def weight(k):
        known = [df[w] for w in tokens(k) if df.get(w)]
        return math.log(max(total, 2) / max(min(known) if known else 1, 1))

    ranked = sorted(cands, key=lambda k: (-count[k] * weight(k), -count[k], k))
    return [(k, count[k]) for k in ranked[:TABLE_COMPONENTS]]


# ---------------------------------------------------------------- the search, the status, the titles

SEARCH_SQL = ("SELECT g.patent_id FROM txt.fts_g_claims JOIN txt.g_claims AS g ON g.rowid = fts_g_claims.rowid "
              "WHERE fts_g_claims MATCH ? ORDER BY fts_g_claims.rowid DESC")


def has_claims_index(con):
    try:
        return con.execute("SELECT 1 FROM txt.sqlite_master WHERE type = 'table' AND name = 'fts_g_claims'").fetchone() is not None
    except sqlite3.Error:
        return False


def search_patents(con, terms, limit, seconds=None):
    """(patent ids, more match, the search ran to its end) for claims holding every term: the FTS5 index of the claims read
    in rowid order from the end, the reverse of the order the long-text file loaded its rows (the bulk files year by year,
    oldest first, so recent grants tend to come first), each claim's patent once, stopping after limit + 1 patents. One
    read under its own deadline."""
    seconds = SEARCH_SECONDS if seconds is None else seconds
    expr = " AND ".join(fts_quote(t) for t in terms)
    deadline = time.monotonic() + seconds
    found, seen, complete = [], set(), True
    con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 100000)
    cur = None
    try:
        cur = con.execute(SEARCH_SQL, (expr,))
        for (pid,) in cur:
            pid = str(plain(pid))
            if pid not in seen:
                seen.add(pid)
                found.append(pid)
                if len(found) > limit:
                    break
    except sqlite3.OperationalError as e:
        if "interrupted" not in str(e):
            raise
        complete = False
    finally:
        if cur is not None:
            cur.close()
        con.set_progress_handler(None, 0)
    return found[:limit], len(found) > limit, complete


def _ids_sql(n):
    return ",".join("?" * n)


def titles_for(con, ids):
    out = {}
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        for pid, title, date in con.execute(f"SELECT patent_id, patent_title, patent_date FROM main.patents WHERE patent_id IN ({_ids_sql(len(part))})", part):
            out[str(pid)] = (title, date)
    return out


def statuses_for(con, cat, ids):
    """{patent_id: status} from the status engine for the ids the catalog or the snapshot has, or None when the engine or
    the catalog is not available. The fields and values of GET /api/v1/lapse/status/<patent_id>/."""
    expiry = lapse_group.expiry_module()
    if expiry is None or not cat:
        return None
    out = {}
    for pid, d in lapse_group.fetch_rows(con, cat, ids).items():
        rec = lapse_group.record_for(expiry, cat, pid, d)
        out[str(pid)] = {"status": rec["status"], "basis": rec["basis"], "reinstatement_possible": rec["reinstatement_possible"],
                         "reinstatement_basis": rec["reinstatement_basis"], "expiry_date_estimated": rec["expiry_date_estimated"],
                         "lapse_date": rec["lapse_date"], "reinstated_date": rec["reinstated_date"], "as_of": rec["as_of"]}
    return out


# ---------------------------------------------------------------- the table

def build_table(searcher, req, base, started=None):
    """(answer, {"built": n, "from_cache": n}) for a read request, or Refusal (501 without the long-text file or its index)."""
    started = time.monotonic() if started is None else started
    con = searcher.connection()
    data_version = searcher.data_version() or ""
    text_version = searcher.text_data_version()
    if not text_version:
        raise Refusal("Endpoint not implemented: the triage table reads the 'g_claims' data set, which is not in this snapshot; no "
                      "long-text file is in service (GET /api/v1/meta/health/ shows text_data_version)", "ERR_NOT_IMPLEMENTED", 501)
    cat = searcher.catalog_facts()
    as_of = (cat or {}).get("as_of") or _as_of(searcher)
    if req["kind"] == "search":
        if not has_claims_index(con):
            raise Refusal("Endpoint not implemented: a search reads the full-text index of the claims, which the long-text file in "
                          "service does not have", "ERR_NOT_IMPLEMENTED", 501)
        ids, more, done = search_patents(con, req["terms"], req["limit"])
        inp = {"kind": "search", "search": req["search"], "terms": req["terms"], "limit": req["limit"], "found": len(ids),
               "more": more, "complete": done}
    else:
        ids = req["ids"]
        inp = {"kind": "list", "patents_read": len(ids), "repeated": req["repeated"], "unread": req["unread"][:UNREAD_SHOWN],
               "unread_count": len(req["unread"])}
    inp["unread_components"] = req["unread_components"]
    titles = titles_for(con, ids)
    stats = statuses_for(con, cat, ids)
    versions = (data_version, text_version)
    rows, facts_of, cached = [], {}, 0
    for i, pid in enumerate(ids, 1):
        found = pid in titles or bool(stats and pid in stats)
        facts, note = None, None
        if not found:
            note = "not_in_data"
        else:
            ck = (pid,) + versions + (cg.STRUCTURE_VERSION,)
            if time.monotonic() - started > TIME_LIMIT_SECONDS and FACTS.get(ck) is None:
                note = "time_limit"
            else:
                try:
                    facts, note, hit = facts_for(con, pid, versions)
                    cached += hit
                except lapse_structure._Interrupted:
                    facts, note = None, "time_limit"
        title, date = titles.get(pid, (None, None))
        rows.append({"row": i, "patent_id": pid, "title": title, "patent_date": date,
                     "permalink": f"{base}/p/{pid}?v={data_version}", "found": found, "built": facts is not None, "note": note,
                     "status": (stats or {}).get(pid),
                     "claims": {"total": facts["claims"], "independent": facts["independent"]} if facts else None,
                     "cells": [], "components": None, "claim_parts": []})
        if facts is not None:
            facts_of[pid] = facts
    if req["components"]:
        comps = [{"key": k, "from": "request", "asked": asked} for k, asked in req["components"]]
    else:
        comps = [{"key": k, "from": "table", "asked": None}
                 for k, _n in table_components(con, list(facts_of.values()), data_version)]
    quotes = 0
    for r in rows:
        facts = facts_of.get(r["patent_id"])
        if facts is None:
            continue
        heads = _heads(facts)
        r["cells"] = [cell(facts, c["key"], heads) for c in comps]
        r["components"] = own_components(facts)
        r["claim_parts"] = facts["claim_parts"]
        quotes += sum(1 for x in r["cells"] if x["quote"]) + sum(len(cl["parts"]) for cl in r["claim_parts"])
    for j, c in enumerate(comps):
        c["patents"] = sum(1 for r in rows if r["cells"] and r["cells"][j]["position"] != "absent")
    built = len(facts_of)
    body = {"error": False, "data_version": data_version, "text_data_version": text_version, "as_of": as_of,
            "structure_version": cg.STRUCTURE_VERSION, "generator": cg.GENERATOR, "input": inp, "components": comps,
            "positions": list(POSITIONS), "complete": not any(r["note"] == "time_limit" for r in rows),
            "time_limit_seconds": TIME_LIMIT_SECONDS, "status_available": stats is not None,
            "counts": {"patents": len(rows), "found": sum(1 for r in rows if r["found"]), "built": built,
                       "not_built": sum(1 for r in rows if r["found"] and not r["built"])},
            "quotes": quotes, "export_columns": list(EXPORT_COLUMNS), "patents": rows}
    return body, {"built": built, "from_cache": cached}


# ---------------------------------------------------------------- the wording rule and the export

def _skeleton(value, quoted=False):
    if isinstance(value, dict):
        return {k: _skeleton(v, quoted or k in QUOTED_FIELDS) for k, v in value.items()}
    if isinstance(value, list):
        return [_skeleton(v, quoted) for v in value]
    if isinstance(value, str):
        return "" if quoted else value
    return value


def own_words(body):
    """PatentRef's own words in an answer: every field name and every value except the patent's, the request's and
    claimgraph's keys."""
    return json.dumps(_skeleton(body), ensure_ascii=False)


def wording_problems(text):
    """The banned strings in PatentRef's own words: the never-regress list of lapse_wording plus the structure layer's."""
    low = (text or "").lower()
    found = list(lapse_wording.check_banned(text))
    found += [w for w in BANNED_WORDS if w in low]
    if COVER_WORD.search(low):
        found.append("cover")
    return found


def _csv_value(v):
    if v is None:
        return ""
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, (list, tuple)):   # the status basis is a list of codes: one field, the codes joined by semicolons
        return ";".join(_csv_value(x) for x in v)
    return str(v)


def export_rows(body, marks=None):
    """The export's rows as dicts of EXPORT_COLUMNS: per patent one row of kind patent, one per component of the table, and
    one per part of each independent claim (preamble, transition, element, wherein_clause). marks: {part_id: "have" |
    "lacks"}, the page's own; the API's export has none."""
    marks = marks or {}
    for p in body["patents"]:
        st = p["status"] or {}
        base = {"patent_id": p["patent_id"], "title": p["title"], "status": st.get("status"), "status_basis": st.get("basis"),
                "reinstatement_possible": st.get("reinstatement_possible"), "as_of": st.get("as_of"), "note": p["note"]}
        yield dict(base, row_kind="patent")
        for c in p["cells"]:
            q = c["quote"] or {}
            yield dict(base, row_kind="component", component=c["key"], position=c["position"], match=c["match"],
                       claim=q.get("claim"), claim_id=q.get("claim_id"), part_id=q.get("term_id"), quote=q.get("text"),
                       start=q.get("start"), end=q.get("end"))
        for cl in p["claim_parts"]:
            for part in cl["parts"]:
                yield dict(base, row_kind=part["part"], claim=part["claim"], claim_id=part["claim_id"], part_id=part["part_id"],
                           label=part.get("label"), quote=part["text"], start=part["start"], end=part["end"],
                           mark=marks.get(part["part_id"]))


def to_csv(body, marks=None):
    """The export as CSV: RFC 4180 (comma, CRLF, a field quoted when it holds a quote, a comma or a line break), UTF-8."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(EXPORT_COLUMNS)
    for row in export_rows(body, marks):
        w.writerow([_csv_value(row.get(c)) for c in EXPORT_COLUMNS])
    return out.getvalue()


def encode(body):
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------- the route

class TriageView(APIView):
    """GET and POST /api/v1/structure/triage/ (gate S1.8L)."""

    def get_throttles(self):
        return super().get_throttles() + [TriageSlots()]

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        if response.status_code == 429 and getattr(getattr(request, "_request", request), "_lapse_triage_busy", False):
            response["X-Status-Reason"] = busy_reason()
        return response

    def get(self, request, format=None):
        params = {}
        for k in request.GET:
            vals = request.GET.getlist(k)
            params[k] = vals if len(vals) > 1 else vals[0]
        return self._answer(request, params)

    def post(self, request, format=None):
        try:
            data = request.data
        except ParseError as e:
            return _error(f"The body must be a JSON object: {e.detail}")
        if not isinstance(data, dict):
            return _error("The body must be a JSON object with 'patents' or 'search'")
        params = dict(data)
        for k in request.GET:
            params.setdefault(k, request.GET.get(k))
        return self._answer(request, params)

    def _answer(self, request, params):
        t0 = time.monotonic()
        try:
            req = read_input(params)
            body, info = build_table(get_searcher(), req, base_url(request), t0)
        except Refusal as e:
            return _error(e.reason, e.code, e.status)
        except lapse_structure._Interrupted:
            return _error(f"Triage table timed out: a read took over {lapse_structure.READ_SECONDS:.0f} s", "ERR_ES", 500)
        bad = wording_problems(own_words(body))
        if bad:   # a template defect must not leave the process
            logger.error("triage: wording check failed: %s", ", ".join(bad))
            return _error(f"wording check failed: {', '.join(bad)}", "ERR_ES", 500)
        if req["output"] == "csv":
            resp = HttpResponse(to_csv(body).encode("utf-8"), content_type="text/csv; charset=utf-8")
            resp["Content-Disposition"] = f'attachment; filename="patentref-triage-{body["data_version"]}.csv"'
        else:
            resp = HttpResponse(encode(body), content_type="application/json")
        whole = body["complete"] and body["input"].get("complete", True)
        resp["Cache-Control"] = CACHE_CONTROL if whole else "no-store"
        resp["X-Text-Data-Version"] = body["text_data_version"]
        resp["Server-Timing"] = (f'triage;desc="{info["built"]} built, {info["from_cache"]} from the cache";'
                                 f'dur={(time.monotonic() - t0) * 1000:.1f}')
        return resp
