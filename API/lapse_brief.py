"""PatentRef's structure layer, tier S0 (gate S0.5): a brief of one patent that fits a token budget, computed on read.

  GET /api/v1/structure/brief/<patent_id>/?max_tokens=<MIN_TOKENS to MAX_TOKENS, default DEFAULT_TOKENS>
      (the trailing slash is optional; a US prefix is read too)

A brief holds these, in this fixed order, as far as the budget goes:
  1. identity: patent_id, patent_title, patent_type, patent_date (the grant), filing_date, claim_count and
     independent_count, from the search file in service and the claim tree;
  2. status: the record GET /api/v1/lapse/status/<patent_id>/ answers, without its links: status, basis,
     expiry_date_estimated, lapse_date, reinstated_date, reinstatement_possible, reinstatement_basis, plain and as_of
     (null when no status catalog is in service);
  3. independent_claims: each independent claim in claim order, with its category, its preamble, its transition and its
     elements;
  4. dependent_claims: one line for each dependent claim: its number, the claims it depends on, and its text from its
     transition (from its body when it has none) to its end, cut at a space after at most LINE_MAX characters (cut: true);
  5. component_keys: the keys of the terms the claims introduce (a claim tree's term key: lower case, the last word
     singular), each with the numbers of the claims that introduce it, ordered by the first claim that introduces the key,
     independent claims first, then by its offset (the order release Y's structured similar mode reads a key's place in).
The items go in in that order: identity, status, each independent claim's head and then each of its elements, each
dependent line, each key. The first item that does not fit ends the brief, so the brief for a smaller budget is a prefix
of the brief for a larger one. truncated says whether anything was left out and omitted counts what was. tokens is the
size of the whole answer by the stated proxy TOKENIZER: the UTF-8 bytes of the JSON body as sent, divided by
BYTES_PER_TOKEN and rounded up; it is never over max_tokens.

Every quote (a preamble, a transition, an element, a dependent line) carries start and end, offsets into its claim's text
as published, and is checked against that text before the answer leaves: text[start:end] is the quote. Every claim and
element carries its anchor on the record page (API/lapse_permalinks.py): page + "#" + anchor.

Computed on read, never stored: the claim tree comes through release X's cache (lapse_permalinks.graph_for); the
prepared brief of a patent (every item before the cut, with its size) is kept in a bounded LRU per worker process
(CACHE_ENTRIES and CACHE_BYTES), keyed by the patent, the three data versions and the structure version. No model call
anywhere. Errors in the upstream shape, so RFC 9457 problem details come with Accept: application/problem+json, as on
release X's claim tree: 400 ERR_Q (not a patent number, max_tokens not an integer in range), 404 NOT_FOUND (a patent the
data in service does not have, or one without claim text), 500 ERR_ES (over the claim tree's work cap, a read over its
time, a check that failed), 501 ERR_NOT_IMPLEMENTED (no long-text file in service), and 503 ERR_ES when the status engine
is not available, as GET /api/v1/lapse/status/ answers. Keyed, throttled and metered like every /api/v1/ data path.
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

from API import lapse_claimgraph as cg
from API import lapse_group, lapse_permalinks, lapse_structure, lapse_wording
from API.lapse_views import _error, base_url, normalize_pid
from API.search import get_searcher

logger = logging.getLogger(__name__)

DEFAULT_TOKENS = 2000
MIN_TOKENS = 300               # the answer's fixed part (versions, counts, the page link) takes about 170 of them
MAX_TOKENS = 32000
BYTES_PER_TOKEN = 3
TOKENIZER = "ceil(utf8_bytes / 3)"   # the stated proxy: the UTF-8 bytes of the body divided by 3, rounded up
LINE_MAX = 240                 # characters of a dependent claim's line before it is cut at a space
CACHE_ENTRIES = 256            # prepared briefs kept per worker process
CACHE_BYTES = 16 * 1024 * 1024  # their encoded items' bytes per worker process (8 workers: 128 MB at most)
CACHE_ITEM_MAX = 2 * 1024 * 1024  # a prepared brief larger than this is not kept
CACHE_CONTROL = "public, max-age=300"
NULL = len(b"null")
SECTIONS = ("identity", "status", "independent_claims", "dependent_claims", "component_keys")
STATUS_FIELDS = ("status", "basis", "expiry_date_estimated", "lapse_date", "reinstated_date", "reinstatement_possible",
                 "reinstatement_basis", "plain", "as_of")
# The words PatentRef's own text in a brief never uses (every field name and every string PatentRef writes, the status
# record's included; the patent's own words, its title, its quoted claim text and its term keys, are left as printed).
# Matched anywhere in the lower-cased text, so "valid" stops "validity" too.
BANNED = ("risk", "clear", "safe", "free to use", "invalid", "valid", "scope", "breadth", "strength", "broad", "narrow",
          "infring", "covers", "freedom to operate", chr(0x2014))
PATENT_TEXT_FIELDS = ("patent_title", "text", "key")


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def tokens_of(n_bytes):
    """The stated proxy: n bytes of UTF-8 JSON are n / BYTES_PER_TOKEN tokens, rounded up."""
    return -(-n_bytes // BYTES_PER_TOKEN)


class Prepared:
    """Every item of one patent's brief before the cut, each with its encoded size."""

    __slots__ = ("identity", "identity_size", "status", "status_size", "claims", "dependents", "keys", "as_of", "size")

    def __init__(self, identity, status, claims, dependents, keys, as_of):
        self.identity, self.status, self.as_of = identity, status, as_of
        self.identity_size = len(encode(identity)) if identity is not None else 0
        self.status_size = len(encode(status)) if status is not None else 0
        # claims: [(the head with an empty element list, its size, [(element, its size)])]
        self.claims = [(h, len(encode(h)), [(e, len(encode(e))) for e in els]) for h, els in claims]
        self.dependents = [(d, len(encode(d))) for d in dependents]
        self.keys = [(k, len(encode(k))) for k in keys]
        self.size = (self.identity_size + self.status_size + sum(s + sum(x for _e, x in els) for _h, s, els in self.claims)
                     + sum(s for _d, s in self.dependents) + sum(s for _k, s in self.keys))

    def totals(self):
        return {"independent_claims": len(self.claims), "elements": sum(len(els) for _h, _s, els in self.claims),
                "dependent_claims": len(self.dependents), "component_keys": len(self.keys)}

    def full(self):
        """The whole brief before any cut, as the five sections of an answer."""
        return {"identity": self.identity, "status": self.status,
                "independent_claims": [dict(h, elements=[e for e, _x in els]) for h, _s, els in self.claims],
                "dependent_claims": [d for d, _s in self.dependents], "component_keys": [k for k, _s in self.keys]}


class PreparedCache:
    """A bounded LRU of prepared briefs, by entries and by bytes, safe for threads."""

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
        if value.size > self.item_max:
            return
        with self._lock:
            old = self._d.pop(key, None)
            if old is not None:
                self._bytes -= old.size
            self._d[key] = value
            self._bytes += value.size
            while self._d and (len(self._d) > self.entries or self._bytes > self.max_bytes):
                _, gone = self._d.popitem(last=False)
                self._bytes -= gone.size

    def clear(self):
        with self._lock:
            self._d.clear()
            self._bytes = 0

    def stats(self):
        with self._lock:
            return {"entries": len(self._d), "bytes": self._bytes}


CACHE = PreparedCache()


def _span(part):
    return None if not part else {"start": part["start"], "end": part["end"], "text": part["text"]}


def independent_item(claim):
    """(head, elements) of an independent claim: the head with its anchor, category, preamble and transition (quotes, or
    null) and element_count; each element with its anchor and its quote, and kind only when it is not a plain element."""
    head = {"number": claim["number"], "anchor": lapse_permalinks.claim_anchor(claim), "category": claim["category"],
            "preamble": _span(claim.get("preamble")), "transition": _span(claim.get("transition")),
            "element_count": len(claim["elements"]), "elements": []}
    els = []
    for el in claim["elements"]:
        item = {"anchor": lapse_permalinks.element_anchor(claim, el), "start": el["start"], "end": el["end"], "text": el["text"]}
        if el.get("kind") and el["kind"] != "element":
            item["kind"] = el["kind"]
        els.append(item)
    return head, els


def dependent_line(claim):
    """One line for a dependent claim: its text from its transition (from its body when it has none) to its end, without
    the whitespace at either end, cut at the last space within LINE_MAX characters (cut: true), with its offsets."""
    text = claim["text"]
    start = claim["transition"]["start"] if claim.get("transition") else int(claim.get("body_start") or 0)
    end = len(text.rstrip())
    while start < end and text[start].isspace():
        start += 1
    start = min(start, end)
    line = {"number": claim["number"], "anchor": lapse_permalinks.claim_anchor(claim), "depends_on": list(claim["depends_on"])}
    cut = False
    if end - start > LINE_MAX:
        stop = text.rfind(" ", start + 1, start + LINE_MAX + 1)
        end = stop if stop > start else start + LINE_MAX
        while end > start and text[end - 1].isspace():
            end -= 1
        cut = True
    line.update(start=start, end=end, text=text[start:end])
    if cut:
        line["cut"] = True
    return line


def component_keys(graph):
    """[{"key", "claims"}]: every key of a term the claims introduce, with the claims that introduce it, ordered by the
    first place a claim introduces it: independent claims first, then the claim number, then the offset."""
    first, claims_of = {}, {}
    for c in graph["claims"]:
        for t in c["terms"]:
            k = t.get("key")
            if t.get("role") != "introduces" or not k:
                continue
            rank = (0 if c["independent"] else 1, c["number"], t["start"])
            if k not in first or rank < first[k]:
                first[k] = rank
            nums = claims_of.setdefault(k, [])
            if c["number"] not in nums:
                nums.append(c["number"])
    return [{"key": k, "claims": sorted(claims_of[k])} for k in sorted(first, key=lambda k: (first[k], k))]


def identity_item(con, pid, graph):
    """The patent's identity from the search file in service (its PatentsView fields) and the claim tree's counts."""
    title = ptype = pdate = filing = None
    try:
        row = lapse_structure._read(con, "SELECT patent_title, patent_type, patent_date FROM main.patents WHERE patent_id = ?", (pid,))
        if row:
            title, ptype, pdate = row[0]
    except sqlite3.Error:
        pass
    try:
        row = lapse_structure._read(con, 'SELECT filing_date FROM main.patents__application WHERE "_pid" = ? ORDER BY "_ord" LIMIT 1', (pid,))
        if row:
            filing = row[0][0]
    except sqlite3.Error:  # a search file without the application rows
        pass
    claims = graph["claims"]
    return {"patent_id": pid, "patent_title": title, "patent_type": ptype, "patent_date": pdate, "filing_date": filing,
            "claim_count": len(claims), "independent_count": sum(1 for c in claims if c["independent"])}


def status_item(con, cat, pid, data_version, base):
    """The status record of GET /api/v1/lapse/status/<patent_id>/ without its links, or None when no status catalog is in
    service. Raises RuntimeError when the status engine is not available (the status path answers 503 then too)."""
    got = lapse_group.verdict_with_engine(con, cat, pid, data_version, base) if cat else None
    if not got:
        return None
    rec = got[0]
    return {k: rec.get(k) for k in STATUS_FIELDS}


def prepare(graph, identity, status, as_of):
    claims, dependents = [], []
    for c in graph["claims"]:
        if c["independent"]:
            claims.append(independent_item(c))
        else:
            dependents.append(dependent_line(c))
    return Prepared(identity, status, claims, dependents, component_keys(graph), as_of)


def quote_problems(prep, graph):
    """Every quote of a prepared brief against its claim's text: [] when text[start:end] is the quote for all of them and
    every element's anchor is one of its claim's."""
    texts = {lapse_permalinks.claim_anchor(c): c["text"] for c in graph["claims"]}
    bad = []

    def check(anchor, q):
        text = texts.get(anchor)
        if text is None or not (0 <= q["start"] <= q["end"] <= len(text)) or text[q["start"]:q["end"]] != q["text"]:
            bad.append(f"{anchor}: [{q['start']}, {q['end']})")

    for head, _s, els in prep.claims:
        for part in (head["preamble"], head["transition"]):
            if part:
                check(head["anchor"], part)
        for el, _x in els:
            check(head["anchor"], el)
            if not el["anchor"].startswith(head["anchor"] + "-e"):
                bad.append(f"{el['anchor']}: not an element of {head['anchor']}")
    for line, _s in prep.dependents:
        check(line["anchor"], line)
    return bad


def own_strings(obj, patent_text=False):
    """Every field name and every string PatentRef writes in a brief (or in any part of one); the patent's own words (its
    title, the quotes' text and the term keys) are left out."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from own_strings(v, patent_text=k in PATENT_TEXT_FIELDS)
    elif isinstance(obj, list):
        for v in obj:
            yield from own_strings(v, patent_text)
    elif isinstance(obj, str) and not patent_text:
        yield obj


def wording_problems(obj):
    """The banned words found in PatentRef's own text of a brief (BANNED and the never-regress check's strings)."""
    found = []
    for s in own_strings(obj):
        low = s.lower()
        found += [b for b in BANNED if b in low and b not in found]
        found += [b for b in lapse_wording.check_banned(s) if b not in found]
    return found


def cut(prep, head, max_tokens):
    """The answer for max_tokens: its fixed part, then the prepared items in order, as many as fit, the first that does not
    fit ending the brief. head names patent_id, data_version, text_data_version and page. Returns (answer, its bytes),
    never over max_tokens by the stated proxy, or (None, the smallest max_tokens the fixed part needs)."""
    budget = max_tokens * BYTES_PER_TOKEN
    totals = prep.totals()
    # tokens and omitted at their widest while the items are counted in: the answer can then only come out shorter
    body = {"error": False, "patent_id": head["patent_id"], "structure_version": cg.STRUCTURE_VERSION, "generator": cg.GENERATOR,
            "data_version": head["data_version"], "text_data_version": head["text_data_version"], "as_of": prep.as_of,
            "max_tokens": max_tokens, "tokens": max_tokens, "tokenizer": TOKENIZER, "truncated": False, "omitted": dict(totals),
            "page": head["page"], "identity": None, "status": None, "independent_claims": [], "dependent_claims": [],
            "component_keys": []}
    used = len(encode(body))
    if used > budget:
        return None, tokens_of(used)
    full = True

    def take(n):
        nonlocal used, full
        if not full or used + n > budget:
            full = False
            return False
        used += n
        return True

    if prep.identity is not None and take(prep.identity_size - NULL):
        body["identity"] = prep.identity
    if prep.status is not None and take(prep.status_size - NULL):
        body["status"] = prep.status
    n_claims = n_els = 0
    for claim_head, size, els in prep.claims:
        if not take(size + (1 if body["independent_claims"] else 0)):
            break
        claim = dict(claim_head, elements=[])
        body["independent_claims"].append(claim)
        n_claims += 1
        for el, esize in els:
            if not take(esize + (1 if claim["elements"] else 0)):
                break
            claim["elements"].append(el)
            n_els += 1
        if not full:
            break
    for section, items in (("dependent_claims", prep.dependents), ("component_keys", prep.keys)):
        for item, size in items:
            if not take(size + (1 if body[section] else 0)):
                break
            body[section].append(item)
    body["truncated"] = not full
    body["omitted"] = {"independent_claims": totals["independent_claims"] - n_claims, "elements": totals["elements"] - n_els,
                       "dependent_claims": totals["dependent_claims"] - len(body["dependent_claims"]),
                       "component_keys": totals["component_keys"] - len(body["component_keys"])}
    t = max_tokens
    while True:   # tokens names the answer's own size: a smaller number can shorten the answer, never lengthen it
        body["tokens"] = t
        data = encode(body)
        n = tokens_of(len(data))
        if n == t:
            break
        t = n
    if t > max_tokens:  # cannot happen by the counting above; an answer over its budget is never sent
        raise AssertionError(f"brief over its budget: {t} > {max_tokens}")
    return body, data


def parse_budget(raw):
    """(max_tokens, None) or (None, the 400 reason)."""
    if raw is None or raw == "":
        return DEFAULT_TOKENS, None
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None, "'max_tokens' must be an integer"
    if not MIN_TOKENS <= n <= MAX_TOKENS:
        return None, f"'max_tokens' must be {MIN_TOKENS} to {MAX_TOKENS}"
    return n, None


def _timed_out(pid):
    return _error(f"Brief timed out: reading the record of {pid} took over {lapse_structure.READ_SECONDS:.0f} s", "ERR_ES",
                  http.HTTP_500_INTERNAL_SERVER_ERROR)


def _tree_error(tree, pid, data_version, text_version):
    if tree.state == lapse_permalinks.UNKNOWN:
        return _error(f"Unknown patent_id: {pid!r} is not in the data in service ({data_version})", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
    if tree.state == lapse_permalinks.NO_CLAIMS:
        return _error(f"No claim text for patent_id {pid!r}: the long-text file in service ({text_version}) has no claims for it; it "
                      f"holds the claims of grants up to its last grant date (GET /api/v1/meta/health/)", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
    if tree.state == lapse_permalinks.OVER_CAP:
        return _error(f"The claims of {pid} are over this endpoint's work cap ({tree.detail})", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
    if tree.state == lapse_permalinks.TIMED_OUT:
        return _error(f"Brief timed out: {tree.detail}", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
    return _error(f"The claim tree of {pid} failed its offset check; nothing was served", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)


def prepared_for(searcher, pid, request):
    """(the prepared brief, "hit" or "miss", None) or (None, None, the error answer)."""
    con = searcher.connection()
    data_version = searcher.data_version() or ""
    text_version = searcher.text_data_version()
    cat = searcher.catalog_facts()
    key = (pid, data_version, text_version, (cat or {}).get("data_version"), cg.STRUCTURE_VERSION)
    prep = CACHE.get(key)
    if prep is not None:
        return prep, "hit", None
    tree = lapse_permalinks.graph_for(searcher, pid)
    if tree.state != lapse_permalinks.OK:
        return None, None, _tree_error(tree, pid, data_version, text_version)
    graph = tree.graph
    try:
        status = status_item(con, cat, pid, data_version, base_url(request))
        identity = identity_item(con, pid, graph)
    except RuntimeError as e:
        return None, None, _error(str(e), "ERR_ES", http.HTTP_503_SERVICE_UNAVAILABLE)
    except lapse_structure._Interrupted:
        return None, None, _timed_out(pid)
    prep = prepare(graph, identity, status, graph.get("as_of"))
    bad = quote_problems(prep, graph)
    if bad:  # a quote that does not hold must not leave the process
        logger.error("brief of %s failed its quote check: %s", pid, bad[:3])
        return None, None, _error(f"The brief of {pid} failed its offset check; nothing was served", "ERR_ES",
                                  http.HTTP_500_INTERNAL_SERVER_ERROR)
    words = wording_problems(prep.full())
    if words:  # never-regress 8, with the structure layer's words
        logger.error("brief of %s: wording check failed: %s", pid, ", ".join(words))
        return None, None, _error(f"wording check failed: {', '.join(words)}", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
    CACHE.put(key, prep)
    return prep, "miss", None


class BriefView(APIView):
    """GET /api/v1/structure/brief/<patent_id>/?max_tokens= (gate S0.5)."""

    def get(self, request, pk, format=None):
        t0 = time.perf_counter()
        pid = normalize_pid(pk)
        if not pid:
            return _error(f"Not a patent number: {pk!r}")
        max_tokens, why = parse_budget(request.GET.get("max_tokens"))
        if why:
            return _error(why)
        searcher = get_searcher()
        data_version = searcher.data_version() or ""
        text_version = searcher.text_data_version()
        if not text_version:
            return _error("Endpoint not implemented: the 'g_claims' data set is not in this snapshot; no long-text file is in "
                          "service (GET /api/v1/meta/health/ shows text_data_version)", "ERR_NOT_IMPLEMENTED", http.HTTP_501_NOT_IMPLEMENTED)
        prep, state, err = prepared_for(searcher, pid, request)
        if err is not None:
            return err
        head = {"patent_id": pid, "data_version": data_version, "text_data_version": text_version,
                "page": f"{base_url(request)}/p/{pid}?v={data_version}"}
        body, data = cut(prep, head, max_tokens)
        if body is None:
            return _error(f"'max_tokens' is too small for the fixed part of this answer; it needs at least {data}", "ERR_Q")
        resp = HttpResponse(data, content_type="application/json")
        resp["Cache-Control"] = CACHE_CONTROL
        resp["X-Text-Data-Version"] = text_version
        resp["Server-Timing"] = f'brief;desc="{state}";dur={(time.perf_counter() - t0) * 1000:.1f}'
        return resp
