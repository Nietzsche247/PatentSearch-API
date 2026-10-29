"""The structured mode of GET /api/v1/lapse/similar (lane Y; the structure layer spec's S1.3, on S0 terms only).

  GET /api/v1/lapse/similar?text=<string>&n=20&mode=structured

Without `mode`, or with mode=text, the path answers exactly as before (API/lapse_views.SimilarView, FTS5 bm25 over
title and abstract). mode=structured ranks patents by the claim terms they share with the text:

1. The text's term keys: the noun phrases claimgraph's term finder (claimgraph.terms.find_mentions, the heuristic behind
   the term mentions of a claim tree) reads in the text, each normalized the way a claim tree's term key is (lower case,
   the last word singular). A key whose rarest word is in more than KEY_MAX_DF of the served patents' titles and
   abstracts says little and is dropped (the text mode's cap, the same document frequencies), unless fewer than MIN_KEYS
   are left, when the rarest are kept up to that many; at most the MAX_KEYS rarest. A key's weight is ln(N / df) of its
   rarest word (N patents, df at least 1).
2. Candidates from the FTS5 index the text mode reads (fts_patents, title and abstract): the text mode's own query, the
   rarest input tokens, deeper (CAND_TEXT); and the key phrases as FTS5 phrases (CAND_KEYS), both by bm25, ties by
   patent_id.
3. Each candidate's claims, read from the long-text file in service (txt.g_claims through its (patent_id,
   claim_sequence) index, as GET /api/v1/structure/claims/ reads them) and parsed by claimgraph, computed on read; the
   tree must pass claimgraph's validator. Only the keys of the terms the claims introduce are kept, in a bounded
   per-worker LRU (KEY_CACHE_ENTRIES patents); nothing is stored. A candidate without claim text, or over the claim tree's
   work cap, is left out.
4. Score: for each of the text's keys, 1 when a term a claim introduces has the same key, 0.5 when one of the two keys
   ends with the other as whole words, else 0, times the key's weight; summed over the patent and divided by the weights'
   sum, so 1 means every key found. Patents with score 0 are left out. Order: score, then the number of exact keys, then
   patent_id. Each neighbour lists its shared terms with where the claim text has them: the claim's number and the
   mention's text, start and end in that claim's text (text[start:end], as in the claim tree; the first independent
   claim that introduces the term, else the first claim).

Deterministic code only: regular expressions, the FTS5 index and claimgraph; no model call. The score describes shared
wording in the published text and nothing more.
"""
import math
import threading
import time
from collections import OrderedDict

from rest_framework import status as http
from rest_framework.response import Response

from API import lapse_structure
from API.lapse_claimgraph import claimgraph, find_mentions
from API.lapse_views import _as_of, _error, select_terms
from API.search_sqlite import fts_quote, tokens

METHOD = "claim_term_keys"
MODES = ("text", "structured")
MAX_INPUT_KEYS = 32      # distinct keys read from the text, in order of appearance, before the frequency cap
MAX_KEYS = 12            # the rarest of them that are matched
MIN_KEYS = 3             # when fewer pass the frequency cap, the rarest are kept up to this many (the text mode's rule)
KEY_MAX_DF = 0.02        # a key whose rarest word is in more than this share of patents is dropped (the text mode's cap)
PHRASE_WORD_MAX_DF = 0.05   # a key goes into the phrase query only when no word of it is in more than this share
CAND_TEXT = 40           # candidates from the text mode's own query
CAND_KEYS = 40           # candidates from the key phrase query
MAX_PHRASES = 8          # key phrases in that query
EXACT, PARTIAL = 1.0, 0.5
KEY_CACHE_ENTRIES = 4096

SQL = ('SELECT p.patent_id, p.patent_title, p.patent_date, bm25(fts_patents, 0.0, 1.0, 1.0) AS s '
       'FROM fts_patents JOIN patents p ON p.rowid = fts_patents.rowid '
       'WHERE fts_patents MATCH ? AND p.withdrawn = 0 ORDER BY s ASC, p.patent_id ASC LIMIT ?')


class KeyCache:
    """A bounded LRU of per-patent claim term keys, safe for threads."""

    def __init__(self, entries=KEY_CACHE_ENTRIES):
        self.entries = entries
        self._d = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            v = self._d.get(key)
            if v is not None:
                self._d.move_to_end(key)
            return v

    def put(self, key, value):
        with self._lock:
            self._d[key] = value
            self._d.move_to_end(key)
            while len(self._d) > self.entries:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()

    def __len__(self):
        with self._lock:
            return len(self._d)


CACHE = KeyCache()


def input_keys(text):
    """The text's term keys in order of first appearance (claimgraph's term finder over the whole text)."""
    keys = []
    for m in find_mentions(text, 0, len(text), bare="introduces"):
        k = m.key
        if k and k not in keys and any(len(w) >= 3 for w in tokens(k)):
            keys.append(k)
            if len(keys) >= MAX_INPUT_KEYS:
                break
    return keys


def _total(con):
    try:
        row = con.execute("SELECT n FROM _lapse_value_stats WHERE tbl = 'patents' AND col = '*'").fetchone()
        if row and row[0]:
            return int(row[0])
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(con.execute("SELECT count(*) FROM patents").fetchone()[0])
    except Exception:  # noqa: BLE001
        return 0


def _doc_freqs(con, words):
    df = {}
    try:
        for w in words:
            if w not in df:
                row = con.execute("SELECT doc FROM temp.fts_patents_vocab WHERE term = ?", (w,)).fetchone()
                df[w] = int(row[0]) if row else 0
    except Exception:  # noqa: BLE001  (no vocab table: every word counts as rare)
        return {w: 0 for w in words}
    return df


def weigh_keys(con, keys):
    """([(key, weight, rarest word's df, words, commonest word's df)] rarest first, N): the keys under the frequency cap
    (at least MIN_KEYS of the rarest), at most MAX_KEYS."""
    total = _total(con)
    words = {k: [w for w in tokens(k)] for k in keys}
    df = _doc_freqs(con, [w for ws in words.values() for w in ws])
    cap = total * KEY_MAX_DF if total else None
    every = []
    for k in keys:
        ws = words[k]
        if not ws:
            continue
        known = [df[w] for w in ws if df[w] > 0]
        rare = min(known) if known else 0
        weight = math.log(max(total, 2) / max(rare, 1))
        every.append((k, round(weight, 4), rare, ws, max(df[w] for w in ws)))
    every.sort(key=lambda x: (x[2], x[0]))
    out = [x for x in every if cap is None or x[2] <= cap]
    if len(out) < MIN_KEYS:   # as the text mode keeps its rarest tokens: a text of common words still gets its rarest keys
        out = every[:max(len(out), min(MIN_KEYS, len(every)))]
    return out[:MAX_KEYS], total


def _text_terms(text):
    terms = []
    for t in tokens(text):
        if len(t) > 1 and t not in terms:
            terms.append(t)
        if len(terms) >= 48:
            break
    return terms


def candidates(searcher, con, text, weighed, total):
    """(ordered candidate rows {pid: (title, date)}, the text mode's terms, the count from each query)."""
    keep = select_terms(con, _text_terms(text))
    seen = OrderedDict()
    n_text = n_keys = 0
    if keep:
        match = "{patent_title patent_abstract}: " + " OR ".join(fts_quote(t) for t in keep)
        for pid, title, date, _s in searcher._run(SQL, (match, CAND_TEXT)):
            n_text += 1
            seen.setdefault(pid, (title, date))
    cap = total * PHRASE_WORD_MAX_DF if total else None
    phrases = [k for k, _w, _r, _ws, top in weighed if cap is None or top <= cap][:MAX_PHRASES]
    if phrases:
        match = "{patent_title patent_abstract}: " + " OR ".join(fts_quote(k) for k in phrases)
        for pid, title, date, _s in searcher._run(SQL, (match, CAND_KEYS)):
            n_keys += 1
            seen.setdefault(pid, (title, date))
    return seen, keep, n_text, n_keys, phrases


def patent_keys(pid, rows):
    """{key: location} of the terms the patent's claims introduce, or None when the tree fails its check. The location is
    the first independent claim that introduces the key, else the first claim: {"claim", "text", "start", "end"}."""
    graph = claimgraph.build_patent(pid, lapse_structure.claim_rows(rows))
    if claimgraph.validate_patent(graph):
        return None
    best = {}
    for c in graph["claims"]:
        for t in c["terms"]:
            if t["role"] != "introduces" or not t["key"]:
                continue
            rank = (0 if c["independent"] else 1, c["number"], t["start"])
            if t["key"] not in best or rank < best[t["key"]][0]:
                best[t["key"]] = (rank, {"claim": c["number"], "text": t["text"], "start": t["start"], "end": t["end"]})
    return {k: v[1] for k, v in best.items()}


def keys_for(con, pid, data_version, text_version):
    """The candidate's claim term keys from the cache or computed now; None without claim text or over the work cap."""
    ck = (pid, data_version, text_version, claimgraph.STRUCTURE_VERSION)
    hit = CACHE.get(ck)
    if hit is not None:
        return hit or None
    rows = lapse_structure._read(con, lapse_structure.CLAIMS_SQL, (pid, lapse_structure.MAX_CLAIMS + 1))
    keys = {}
    if rows and len(rows) <= lapse_structure.MAX_CLAIMS:
        rows = [(p, seq, lapse_structure._text(t), dep, num, ex) for p, seq, t, dep, num, ex in rows]
        if sum(len(r[2]) for r in rows) <= lapse_structure.MAX_TEXT_CHARS:
            keys = patent_keys(pid, rows) or {}
    CACHE.put(ck, keys)   # {} remembers "nothing to match" as well
    return keys or None


def match_key(q, pkeys, by_head):
    """('exact' | 'partial', the patent's key) for one of the text's keys, or None."""
    if q in pkeys:
        return "exact", q
    head = q.rsplit(" ", 1)[-1]
    found = None
    for k in by_head.get(head, ()):
        if k.endswith(" " + q) or q.endswith(" " + k):
            shared = min(len(k.split()), len(q.split()))
            cand = (-shared, k)
            if found is None or cand < found:
                found = cand
    return ("partial", found[1]) if found else None


def score(weighed, pkeys):
    by_head = {}
    for k in pkeys:
        by_head.setdefault(k.rsplit(" ", 1)[-1], []).append(k)
    total_w = sum(w for _k, w, *_ in weighed) or 1.0
    got, exact, shared = 0.0, 0, []
    for q, w, *_ in weighed:
        m = match_key(q, pkeys, by_head)
        if not m:
            continue
        kind, k = m
        got += w * (EXACT if kind == "exact" else PARTIAL)
        exact += kind == "exact"
        shared.append({"key": q, "match": kind, "claim_key": k, **pkeys[k]})
    return round(got / total_w, 4), exact, shared


def structured_answer(searcher, text, n):
    """The structured answer as a Response (200), or the error Response (501 without a long-text file, 500 ERR_ES when a
    read of claims runs over its time)."""
    t0 = time.perf_counter()
    con = searcher.connection()
    data_version = searcher.data_version() or ""
    text_version = searcher.text_data_version()
    if not text_version:
        return _error("Endpoint not implemented: mode=structured reads the 'g_claims' data set, which is not in this snapshot; no "
                      "long-text file is in service (GET /api/v1/meta/health/ shows text_data_version)", "ERR_NOT_IMPLEMENTED",
                      http.HTTP_501_NOT_IMPLEMENTED)
    cat = searcher.catalog_facts() or {}
    as_of = cat.get("as_of") or _as_of(searcher)
    weighed, total = weigh_keys(con, input_keys(text))
    seen, keep, n_text, n_keys, phrases = ({}, [], 0, 0, [])
    if weighed:
        seen, keep, n_text, n_keys, phrases = candidates(searcher, con, text, weighed, total)
    scored, built = [], 0
    try:
        for pid, (title, date) in seen.items():
            pkeys = keys_for(con, pid, data_version, text_version)
            if not pkeys:
                continue
            built += 1
            s, exact, shared = score(weighed, pkeys)
            if s > 0:
                scored.append((-s, -exact, pid, title, date, s, shared))
    except lapse_structure._Interrupted:
        return _error(f"Structured similar timed out: a read of claims took over {lapse_structure.READ_SECONDS:.0f} s", "ERR_ES",
                      http.HTTP_500_INTERNAL_SERVER_ERROR)
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    neighbors = [{"rank": i + 1, "patent_id": pid, "patent_title": title, "patent_date": date, "score": s, "shared_terms": shared}
                 for i, (_a, _b, pid, title, date, s, shared) in enumerate(scored[:n])]
    body = {"error": False, "data_version": data_version, "text_data_version": text_version, "as_of": as_of, "method": METHOD,
            "mode": "structured", "structure_version": claimgraph.STRUCTURE_VERSION, "generator": lapse_structure.GENERATOR, "n": n,
            "terms": keep, "term_keys": [{"key": k, "weight": w} for k, w, *_ in weighed], "key_phrases": phrases,
            "candidates": {"title_abstract": n_text, "key_phrases": n_keys, "with_claim_text": built}, "neighbors": neighbors}
    resp = Response(body)
    resp["X-Text-Data-Version"] = text_version
    resp["Server-Timing"] = f'similar;desc="structured";dur={(time.perf_counter() - t0) * 1000:.1f}'
    return resp
