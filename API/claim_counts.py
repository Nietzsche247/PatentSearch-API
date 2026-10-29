"""Claim counts of one patent (the structure layer, gate S0.6): counts in the spirit of the USPTO Patent Claims Research
Dataset (the USPTO Office of the Chief Economist's claims files for grants of 1976 to 2014, in BigQuery as
patents-public-data.uspto_oce_claims), computed from the claim tree claimgraph builds from the claims in service, for
every grant the long-text file holds.

Pure functions, standard library only, no Django, no model call. GET /api/v1/structure/counts/<patent_id>/
(API/lapse_structure_counts.py) and the full-corpus snapshot (lapse_tools/claims_counts_snapshot.py) both call
counts_from_rows(), so a number in the snapshot is the number the endpoint answers for the same files.

Every field is a count, or the least, mean or greatest of counts; PATENT_FIELDS and CLAIM_FIELDS give each one's
definition and the USPTO variable it follows, and the docs page and the snapshot's data dictionary are written from them.
Words and characters are counted after a claim's number prefix ("1. "), as the USPTO dataset's documentation says it
counts only the language after the numeral. A word is a run of characters between white space that holds at least one
letter or digit, so a comma or a bracket the long text sets off with spaces ("claim 1 , wherein", "( 3 )") is not a word.
Characters are counted with each run of white space as one space and no space before a comma, semicolon, colon, full stop
or closing bracket, or after an opening bracket, so the long text's spacing of punctuation does not change the count.
Both rules were checked against the USPTO files on 1,000 random grants of 1976 to 2014 (lane Z-B's handoff of
2026-10-29). A claim whose whole text is a cancellation note ("2. (canceled)") is counted in cancelled_claims and left out
of every other count, as the USPTO dataset leaves cancelled claims out.
"""
import re
import zlib

from API.lapse_claimgraph import GENERATOR, STRUCTURE_VERSION, ClaimRow, build_patent, validate_patent

COUNTS_VERSION = "1"

_CANCEL = re.compile(r"[(\[]?\s*(?:cancell?ed|deleted)\s*[)\]]?[\s.]*", re.I)
_CONSISTING = re.compile(r"\bconsisting\b", re.I)
_OR = re.compile(r"\bor\b", re.I)
_SELECTED_FROM = re.compile(r"\bselected\s+from\b", re.I)
_HAS_LETTER_OR_DIGIT = re.compile(r"[^\W_]")
_SPACE_BEFORE = re.compile(r" (?=[,;:.)\]])")
_SPACE_AFTER = re.compile(r"(?<=[(\[]) ")


def words_of(body):
    """Words of a claim body: runs of characters between white space with at least one letter or digit."""
    return sum(1 for t in body.split() if _HAS_LETTER_OR_DIGIT.search(t))


def characters_of(body):
    """Characters of a claim body with its spacing made regular: each run of white space as one space, none before a comma,
    semicolon, colon, full stop or closing bracket, none after an opening bracket."""
    s = " ".join(body.split())
    return len(_SPACE_AFTER.sub("", _SPACE_BEFORE.sub("", s)))


# (name, JSON type, definition, the USPTO Patent Claims Research Dataset variable it follows or None). Patent level, in the
# order of the answer's counts object and of the snapshot's columns after patent_id.
PATENT_FIELDS = (
    ("claims", "integer",
     "Claims of the patent in the long-text file, a claim whose whole text is a cancellation note left out. It is "
     "independent_claims plus dependent_claims.",
     "pat_clm_ct plus pat_dep_clm_ct (document_stats)"),
    ("independent_claims", "integer",
     "Claims with no dependency in the claim tree: no claim named in the preamble, and none in the dependent field of the "
     "long text either. A claim that names another claim only in its body (\"a system that performs the method of claim "
     "1\") counts here.",
     "pat_clm_ct, which counts a claim as independent only when its text names no other claim anywhere; that count is "
     "claims minus claims_citing_other_claims"),
    ("dependent_claims", "integer",
     "Claims with a dependency in the claim tree: parent_claims of 1 or more.",
     "pat_dep_clm_ct (see claims_citing_other_claims)"),
    ("claims_citing_other_claims", "integer",
     "Claims whose text names at least one other claim, in the preamble or in the body. This is the rule the USPTO dataset "
     "uses to call a claim dependent.",
     "pat_dep_clm_ct"),
    ("multiple_dependent_claims", "integer",
     "Claims that depend on more than one claim.",
     None),
    ("cancelled_claims", "integer",
     "Claims whose whole text is a cancellation note, such as \"(canceled)\"; they are left out of every other count.",
     "none for grants; the USPTO dataset leaves cancelled claims out of its counts"),
    ("words", "integer",
     "Words of the claims, each claim counted after its number prefix. A word is a run of characters between white space "
     "with at least one letter or digit: a comma or bracket set off by spaces is not a word.",
     "the sum of word_ct over the claims (claims_stats); for grants from 2002 on the USPTO files also count the claim's "
     "number (\"1.\") as a word when their claim text starts with it"),
    ("words_first_claim", "integer",
     "Words of the first claim in claim order; null when the patent has no claim.",
     "word_ct of the first claim (claims_stats)"),
    ("words_independent_total", "integer",
     "Words of the independent claims together.",
     "pat_wrd_ct"),
    ("words_independent_min", "integer",
     "Words of the shortest independent claim; null without an independent claim.",
     "pat_wrd_min (the length of the shortest independent claim)"),
    ("words_independent_mean", "number",
     "Mean words per independent claim, to two decimals; null without an independent claim.",
     "pat_wrd_avg"),
    ("words_independent_max", "integer",
     "Words of the longest independent claim; null without an independent claim.",
     None),
    ("words_dependent_total", "integer",
     "Words of the dependent claims together, each counted on its own text: the words of the claims it refers to are not "
     "added.",
     "pat_dep_wrd_ct, which also counts a dependent claim's own words only"),
    ("words_dependent_min", "integer",
     "Words of the shortest dependent claim; null without a dependent claim.",
     "pat_dep_wrd_min"),
    ("words_dependent_mean", "number",
     "Mean words per dependent claim, to two decimals; null without a dependent claim.",
     "pat_dep_wrd_avg"),
    ("characters", "integer",
     "Characters of the claims, each claim counted after its number prefix with its spacing made regular: each run of "
     "white space counts as one space, and no space counts before a comma, semicolon, colon, full stop or closing bracket "
     "or after an opening bracket.",
     "the sum of char_ct over the claims (claims_stats; the USPTO codebook does not define char_ct further, and for grants "
     "from 2002 on it also counts the claim's number and its full stop when the claim text starts with them)"),
    ("elements_independent_min", "integer",
     "Elements of the independent claim with the fewest. An element is a part of the body after the transition word, split "
     "at semicolons and colons as the claim tree splits it; wherein clauses are not elements. Null without an independent "
     "claim.",
     None),
    ("elements_independent_mean", "number",
     "Mean elements per independent claim, to two decimals; null without an independent claim.",
     None),
    ("elements_independent_max", "integer",
     "Elements of the independent claim with the most; null without an independent claim.",
     None),
    ("dependency_depth_max", "integer",
     "The longest chain of dependencies: 0 when every claim is independent, 1 when every dependent claim depends directly "
     "on an independent claim, 2 when one depends on a claim at depth 1, and so on.",
     None),
    ("consisting_words", "integer",
     "Times the word \"consisting\" appears in the claims.",
     "the sum of cns_ct over the claims"),
    ("or_words", "integer",
     "Times the word \"or\" appears in the claims.",
     "the sum of or_ct over the claims"),
    ("selected_from_phrases", "integer",
     "Times the words \"selected from\" appear together in the claims.",
     "the sum of sf_ct over the claims"),
)

# Claim level, in the order of each entry of the answer's claim_counts list.
CLAIM_FIELDS = (
    ("number", "integer", "The claim's number, as the claim tree gives it.", "claim_no"),
    ("sequence", "integer", "The claim's place in the long text, from 0.", None),
    ("words", "integer", "Words of the claim after its number prefix (see the patent's words).", "word_ct"),
    ("characters", "integer", "Characters of the claim after its number prefix, its spacing made regular (see the patent's "
     "characters).", "char_ct"),
    ("elements", "integer", "Elements of the claim (see elements_independent_min).", None),
    ("wherein_clauses", "integer", "Wherein clauses of the claim, the parts of the body the claim tree reads as one.", None),
    ("parent_claims", "integer", "Claims this claim depends on in the claim tree; 0 for an independent claim.", None),
    ("cited_claims", "integer", "Other claims the claim's text names, in the preamble or the body.",
     "the claims in dependencies (claims_fulltext); ind_flg is 1 when there are none"),
    ("dependency_depth", "integer", "The claim's place in the longest chain of dependencies above it: 0 for an independent "
     "claim, 1 for a claim that depends on one, and so on.", None),
    ("consisting_words", "integer", "Times the word \"consisting\" appears in the claim.", "cns_ct"),
    ("or_words", "integer", "Times the word \"or\" appears in the claim.", "or_ct"),
    ("selected_from_phrases", "integer", "Times the words \"selected from\" appear together in the claim.", "sf_ct"),
    ("cancelled", "integer", "1 when the claim's whole text is a cancellation note, else 0; a cancelled claim is in no "
     "patent count but cancelled_claims.", None),
)

PATENT_FIELD_NAMES = tuple(f[0] for f in PATENT_FIELDS)
CLAIM_FIELD_NAMES = tuple(f[0] for f in CLAIM_FIELDS)
TSV_COLUMNS = ("patent_id",) + PATENT_FIELD_NAMES


def text_of(v):
    """A stored value as text: the long-text file's zlib-compressed BLOB decompressed, None as "", anything else as str."""
    if v is None:
        return ""
    if isinstance(v, (bytes, memoryview)):
        b = bytes(v)
        try:
            return zlib.decompress(b).decode("utf-8", errors="replace")
        except zlib.error:
            return b.decode("utf-8", errors="replace")
    return str(v)


def claim_rows(rows):
    """txt.g_claims rows (patent_id, claim_sequence, claim_text, claim_dependent, claim_number, exemplary) as claimgraph's
    ClaimRow, every field a string as in the g_claims TSV files (the claim tree's own reading)."""
    return [ClaimRow(text_of(p), text_of(seq), text_of(t), text_of(dep), text_of(num), text_of(ex))
            for p, seq, t, dep, num, ex in rows]


def _depths(claims):
    """Each claim's dependency depth, by its index in claims: 0 with no parent, else 1 plus the deepest parent present.
    A parent that is missing, the claim itself, or a claim on the chain being walked (a cycle) adds nothing. Iterative,
    so a chain of hundreds of claims needs no deep recursion."""
    by = {}
    for c in claims:
        by.setdefault(c["number"], c)
    parents = {n: [t for t in c["depends_on"] if t != n and t in by] for n, c in by.items()}
    depth, state = {}, {}
    for root in by:
        if root in depth:
            continue
        state[root] = 1
        stack = [(root, iter(parents[root]))]
        while stack:
            n, it = stack[-1]
            for p in it:
                if state.get(p):
                    continue
                state[p] = 1
                stack.append((p, iter(parents[p])))
                break
            else:
                stack.pop()
                done = [depth[p] for p in parents[n] if p in depth]
                depth[n] = (1 + max(done, default=0)) if by[n]["depends_on"] else 0
                state[n] = 2
    out = []
    for c in claims:
        if by.get(c["number"]) is c:
            out.append(depth[c["number"]])
        else:   # a repeated claim number: its own parents, as found above
            done = [depth[t] for t in c["depends_on"] if t != c["number"] and t in depth]
            out.append((1 + max(done, default=0)) if c["depends_on"] else 0)
    return out


def claim_counts(claim, depth):
    """The counts of one claim of a claim tree (claimgraph's claim object)."""
    text = claim.get("text") or ""
    body = text[claim.get("body_start") or 0:]
    kept = body.strip()
    cited = {t for r in claim.get("claim_refs") or [] if r.get("in") != "prefix"
             for t in (r.get("targets") or []) if t != claim["number"]}
    elements = claim.get("elements") or []
    return {
        "number": claim["number"],
        "sequence": claim["sequence"],
        "words": words_of(body),
        "characters": characters_of(body),
        "elements": sum(1 for e in elements if e.get("kind") == "element"),
        "wherein_clauses": sum(1 for e in elements if e.get("kind") == "wherein_clause"),
        "parent_claims": len(claim.get("depends_on") or []),
        "cited_claims": len(cited),
        "dependency_depth": depth,
        "consisting_words": len(_CONSISTING.findall(body)),
        "or_words": len(_OR.findall(body)),
        "selected_from_phrases": len(_SELECTED_FROM.findall(body)),
        "cancelled": 1 if kept and _CANCEL.fullmatch(kept) else 0,
    }


def _mean(xs):
    return round(sum(xs) / len(xs), 2) if xs else None


def patent_counts(per_claim):
    """The patent's counts from its claims' counts, in PATENT_FIELDS order."""
    kept = [c for c in per_claim if not c["cancelled"]]
    ind = [c for c in kept if c["parent_claims"] == 0]
    dep = [c for c in kept if c["parent_claims"] > 0]
    iw, dw, ie = [c["words"] for c in ind], [c["words"] for c in dep], [c["elements"] for c in ind]
    out = {
        "claims": len(kept),
        "independent_claims": len(ind),
        "dependent_claims": len(dep),
        "claims_citing_other_claims": sum(1 for c in kept if c["cited_claims"] > 0),
        "multiple_dependent_claims": sum(1 for c in kept if c["parent_claims"] > 1),
        "cancelled_claims": len(per_claim) - len(kept),
        "words": sum(c["words"] for c in kept),
        "words_first_claim": kept[0]["words"] if kept else None,
        "words_independent_total": sum(iw),
        "words_independent_min": min(iw) if iw else None,
        "words_independent_mean": _mean(iw),
        "words_independent_max": max(iw) if iw else None,
        "words_dependent_total": sum(dw),
        "words_dependent_min": min(dw) if dw else None,
        "words_dependent_mean": _mean(dw),
        "characters": sum(c["characters"] for c in kept),
        "elements_independent_min": min(ie) if ie else None,
        "elements_independent_mean": _mean(ie),
        "elements_independent_max": max(ie) if ie else None,
        "dependency_depth_max": max((c["dependency_depth"] for c in kept), default=0),
        "consisting_words": sum(c["consisting_words"] for c in kept),
        "or_words": sum(c["or_words"] for c in kept),
        "selected_from_phrases": sum(c["selected_from_phrases"] for c in kept),
    }
    assert tuple(out) == PATENT_FIELD_NAMES
    return out


def counts_of_graph(graph):
    """(patent counts, claim counts) of a claim tree (claimgraph.build_patent's patent object)."""
    claims = graph["claims"]
    per_claim = [claim_counts(c, d) for c, d in zip(claims, _depths(claims))]
    return patent_counts(per_claim), per_claim


def counts_from_rows(patent_id, rows):
    """The counts of one patent from its txt.g_claims rows: (patent counts, claim counts, []), or (None, None, problems)
    when the claim tree fails claimgraph's own check (no count of a tree that fails it is ever given out)."""
    graph = build_patent(patent_id, claim_rows(rows))
    problems = validate_patent(graph)
    if problems:
        return None, None, problems
    pc, cc = counts_of_graph(graph)
    return pc, cc, []


def tsv_value(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def tsv_line(patent_id, counts):
    """One snapshot row: patent_id and the patent counts in TSV_COLUMNS order, null as an empty cell, means to two decimals."""
    return "\t".join([patent_id] + [tsv_value(counts[k]) for k in PATENT_FIELD_NAMES]) + "\n"


def dictionary_rows():
    """The data dictionary: (column, level, type, definition, USPTO variable) for every column of the snapshot file, then
    for every field of a claim's counts (the endpoint's claim_counts)."""
    rows = [("patent_id", "patent", "string", "The patent number as PatentsView prints it: 10000000, D872010, RE31177, PP36776.",
             "pat_no")]
    rows += [(n, "patent", t, d, u or "") for n, t, d, u in PATENT_FIELDS]
    rows += [(n, "claim", t, d, u or "") for n, t, d, u in CLAIM_FIELDS]
    return rows


__all__ = ["COUNTS_VERSION", "STRUCTURE_VERSION", "GENERATOR", "PATENT_FIELDS", "CLAIM_FIELDS", "PATENT_FIELD_NAMES",
           "CLAIM_FIELD_NAMES", "TSV_COLUMNS", "text_of", "claim_rows", "claim_counts", "patent_counts", "counts_of_graph",
           "counts_from_rows", "tsv_value", "tsv_line", "dictionary_rows"]
