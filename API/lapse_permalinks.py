"""PatentRef's structure layer, tier S0 (gate S0.4): a link to each claim and each element on the record page /p/<patent_id>.

The record page (API/lapse_views.RecordPage, template lapse_accounts/record.html) shows the patent's claims as the
long-text file in service prints them, laid out by the claim tree release X serves (API/lapse_structure.py): each claim
under its own heading, its text up to its first element on one line, then one line per element. Every claim and every
element carries an anchor, an HTML id made only from the claim tree's own unit ids and the element's offsets:

  claim n                     c<n>                      (a claim whose number repeats in the data: c<n>-s<claim_sequence>)
  element k of claim n        c<n>-e<k>-<start>-<end>   (k: the element's place in the claim; start, end: its offsets in
                                                         the claim text, text[start:end] being the element)

claimgraph is deterministic, so the same claim rows give the same tree and the same anchors on every build of the same
data_version and text_data_version, in every worker and after every restart. A changed text or a new structure_version
can move an element, and its anchor with it. Only the browser sees the part of a link after #, so a link to an anchor
the page does not have lands, through the page's small script, on the claim the anchor names, with a short note; when
the page has no such claim, on the claims heading with the note. ANCHOR_PATTERN and the script's pattern are the same
(a test checks).

The claim text is the patent's, left as printed and escaped; the page adds headings, the dependency line and the links.
The claims come through release X's cache of claim trees (lapse_structure.CACHE, keyed by the patent, both data versions
and the structure version), so the record page, the claim tree endpoint and the brief share one bounded cache and the
same reads, work caps and check. Nothing is stored. A claim set the page cannot show (no long-text file in service, a
grant after the file's last grant date, over the work cap, a read over its time) leaves the record as it was, with one
line saying so.
"""
import html
import json
import logging
import re
from dataclasses import dataclass

from django.utils.safestring import mark_safe

from API import lapse_claimgraph as cg
from API import lapse_structure
from API.lapse_views import _as_of

logger = logging.getLogger(__name__)

ANCHOR_PATTERN = r"^c([0-9]+)(?:-s([0-9]+))?(?:-e([0-9]+)-([0-9]+)-([0-9]+))?$"
ANCHOR_RE = re.compile(ANCHOR_PATTERN)

# what graph_for found
OK = "ok"                        # the claim tree
NO_TEXT_FILE = "no_text_file"    # no long-text file in service
UNKNOWN = "unknown"              # the data in service does not have the patent
NO_CLAIMS = "no_claims"          # the patent without claim text in the long-text file (a grant after its last grant date)
OVER_CAP = "over_cap"            # over release X's work cap (claim rows or characters)
TIMED_OUT = "timed_out"          # a read over release X's READ_SECONDS
FAILED = "failed"                # the tree failed claimgraph's check (never served)

# the record page's claims section: what it shows for each of the above
PAGE_STATES = {OK: "ok", NO_TEXT_FILE: "no_text_file", UNKNOWN: "no_claims", NO_CLAIMS: "no_claims"}


@dataclass
class Tree:
    state: str
    graph: dict = None
    detail: str = ""


def claim_anchor(claim):
    """c<n>, or c<n>-s<sequence> for a claim whose number repeats: the claim id without its US<patent_id>- prefix."""
    cid = claim["claim_id"]
    i = cid.find("-c")
    return cid[i + 1:] if i >= 0 else f"c{claim['number']}"


def element_place(el):
    """k of the element id US<patent_id>-c<n>-e<k>: the element's place in its claim, from 1."""
    return el["element_id"].rsplit("-e", 1)[-1]


def element_anchor(claim, el):
    """c<n>-e<k>-<start>-<end>: the claim's anchor, the element's place in the claim and its offsets in the claim text."""
    return f"{claim_anchor(claim)}-e{element_place(el)}-{el['start']}-{el['end']}"


def graph_for(searcher, pid):
    """The claim tree of one patent as a Tree: the tree GET /api/v1/structure/claims/<patent_id>/ serves, from release X's
    cache, else read and built now with release X's reads, caps and check, and put in that cache."""
    con = searcher.connection()
    data_version = searcher.data_version() or ""
    text_version = searcher.text_data_version()
    if not text_version:
        return Tree(NO_TEXT_FILE)
    key = (pid, data_version, text_version, cg.STRUCTURE_VERSION)
    hit = lapse_structure.CACHE.get(key)
    if hit is not None:
        return Tree(OK, json.loads(hit))
    try:
        rows = lapse_structure._read(con, lapse_structure.CLAIMS_SQL, (pid, lapse_structure.MAX_CLAIMS + 1))
        known = bool(rows) or bool(lapse_structure._read(con, lapse_structure.PATENT_SQL, (pid,)))
    except lapse_structure._Interrupted:
        return Tree(TIMED_OUT, detail=f"reading the claims of {pid} took over {lapse_structure.READ_SECONDS:.0f} s")
    if not rows:
        return Tree(NO_CLAIMS if known else UNKNOWN)
    if len(rows) > lapse_structure.MAX_CLAIMS:
        return Tree(OVER_CAP, detail=f"{lapse_structure.MAX_CLAIMS} claim rows")
    rows = [(p, seq, lapse_structure._text(t), dep, num, ex) for p, seq, t, dep, num, ex in rows]
    chars = sum(len(r[2]) for r in rows)
    if chars > lapse_structure.MAX_TEXT_CHARS:
        return Tree(OVER_CAP, detail=f"{chars:,} characters; at most {lapse_structure.MAX_TEXT_CHARS:,}")
    cat = searcher.catalog_facts() or {}
    as_of = cat.get("as_of") or _as_of(searcher)
    body, problems = lapse_structure.tree_answer(pid, rows, data_version, text_version, as_of)
    if body is None:
        logger.error("claim tree of %s failed its check: %s", pid, problems[:3])
        return Tree(FAILED)
    lapse_structure.CACHE.put(key, lapse_structure.encode(body))
    return Tree(OK, body)


def _line_start(text, prev_end, el):
    """Where an element's line starts on the page: at its label ("a.", "(b)") when the text just before the element ends
    with it, else at the element itself."""
    label = el.get("label")
    if label:
        gap = text[prev_end:el["start"]]
        i = gap.rfind(label)
        if i >= 0 and not gap[i + len(label):].strip():
            return prev_end + i
    return el["start"]


def layout(claim):
    """(lead, lines): the claim text cut for the page. lead runs up to the first element's line; each line is one element
    with its anchor, its place k, what precedes it on its line (head: its label), its text (text[start:end] exactly) and
    what follows it up to the next line (tail: the separator, as "; and "). lead and every line's head, text and tail,
    joined, are the claim text exactly. Raises ValueError for elements that overlap (claimgraph's check refuses those,
    so a served tree has none)."""
    text = claim["text"]
    els = sorted(claim["elements"], key=lambda e: (e["start"], e["end"]))
    if not els:
        return text, []
    starts, prev = [], 0
    for el in els:
        if el["start"] < prev:
            raise ValueError(f"{claim['claim_id']}: elements overlap")
        starts.append(_line_start(text, prev, el))
        prev = el["end"]
    lines = []
    for i, el in enumerate(els):
        stop = starts[i + 1] if i + 1 < len(els) else len(text)
        lines.append({"anchor": element_anchor(claim, el), "k": element_place(el), "head": text[starts[i]:el["start"]],
                      "text": text[el["start"]:el["end"]], "tail": text[el["end"]:stop]})
    return text[:starts[0]], lines


def dependency_text(claim):
    deps = claim.get("depends_on") or []
    if not deps:
        return "Independent claim."
    if len(deps) == 1:
        return f"Depends on claim {deps[0]}."
    return "Depends on claims " + ", ".join(str(d) for d in deps[:-1]) + f" and {deps[-1]}."


def tree_of(graph):
    """The record page's claim tree (release AD, the re-skin of 2026-11-05): [(independent claim, [(dependent, depth), ...]),
    ...] in claim order, each dependent under the independent claim its dependency chain reaches (by its first claim
    reference at each step), in claim order, with its depth (1: it refers to the independent claim itself). A claim whose
    chain reaches no independent claim on the page (a reference to a number the data does not have, or a loop) stands at the
    top as a group of its own, with its dependency line."""
    claims = graph["claims"]
    by_num = {}
    for c in claims:
        by_num.setdefault(c["number"], c)

    def root(c):
        seen, depth, cur = set(), 0, c
        while (cur.get("depends_on") or []) and not cur.get("independent"):
            if id(cur) in seen:
                return None, 0
            seen.add(id(cur))
            nxt = by_num.get(cur["depends_on"][0])
            if nxt is None or nxt is cur:
                return None, 0
            cur, depth = nxt, depth + 1
        return cur, depth

    groups, index = [], {}
    for c in claims:
        r, depth = (c, 0) if (c.get("independent") or not (c.get("depends_on") or [])) else root(c)
        if r is c or r is None:
            index[id(c)] = len(groups)
            groups.append((c, []))
            continue
        if id(r) not in index:   # an independent claim that comes after its dependent in the data: its group starts there
            index[id(r)] = len(groups)
            groups.append((r, []))
        groups[index[id(r)]][1].append((c, depth))
    seen_top, out = set(), []
    for top, deps in groups:   # a root reached before its own place in the data is listed once, where its group began
        if id(top) in seen_top:
            continue
        seen_top.add(id(top))
        out.append((top, deps))
    return out


def _numbers_text(nums):
    """2 to 9; 2, 4 and 7; 2, 3, 5 to 9 and 21: runs of three or more as a span, the rest listed."""
    nums = sorted(set(nums))
    pieces, i = [], 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        if j - i >= 2:
            pieces.append(f"{nums[i]} to {nums[j]}")
        else:
            pieces.extend(str(n) for n in nums[i:j + 1])
        i = j + 1
    return pieces[0] if len(pieces) == 1 else ", ".join(pieces[:-1]) + " and " + pieces[-1]


def deps_summary(top, deps):
    """The line that opens a claim's dependents: "8 dependent claims: 2 to 9 (2, 4, 6, 7, 9 on claim 1; 3 on 2; 5 on 4; 8
    on 7)". The parenthesis groups them by the claims they refer to, the claim at the top first; it is left out past six
    groups, where the nested claims say it better."""
    n = len(deps)
    head = f"{n} dependent claim{'s' if n != 1 else ''}: {_numbers_text([c['number'] for c, _ in deps])}"
    groups = {}
    for c, _ in deps:
        groups.setdefault(tuple(c.get("depends_on") or []), []).append(c["number"])
    order = sorted(groups, key=lambda k: (k != (top["number"],), k))
    if not order or len(order) > 6:
        return head
    bits = []
    for i, k in enumerate(order):
        targets = str(k[0]) if len(k) == 1 else ", ".join(str(t) for t in k[:-1]) + " and " + str(k[-1])
        word = ("claims " if len(k) > 1 else "claim ") if i == 0 else ""
        bits.append(", ".join(str(x) for x in groups[k]) + f" on {word}{targets}")
    return f"{head} ({'; '.join(bits)})"


def _claim_html(c, depth, copy):
    """One claim: its heading row, its text up to its first element (.claim-lead, the claim number in it as printed), and one
    row per element (.el-line, the element itself in .el-text, its trailing separator in .el-sep), with the element's link,
    its offsets and, on an independent claim, a copy button the page script shows. lead and every .el-line, joined, are the
    claim text exactly."""
    e = html.escape
    a = e(claim_anchor(c))
    n = c["number"]
    lead, lines = layout(c)
    head = f"Claim {n}" + (" (this number appears more than once in the data)" if "-s" in a else "")
    tag = "div"   # every claim a div.claim with its anchor as id (an outside checker of the claim text reads these hooks)
    cls = "claim top" if depth == 0 else f"claim dep d{min(depth, 4)}"
    parts = [f'<{tag} class="{cls}" id="{a}"><div class="claim-head"><h3><a href="#{a}">{e(head)}</a></h3>'
             f'<p class="claim-dep">{e(dependency_text(c))}</p><span class="claim-id" aria-hidden="true">#{a}</span></div>']
    if lead:
        body = int(c.get("body_start") or 0)
        no = lead[:body] if 0 < body <= len(lead) and lead[:body].strip().rstrip(".").isdigit() else ""
        num = '<span class="claim-no">' + e(no) + "</span>" if no else ""
        parts.append('<p class="claim-lead">' + num + e(lead[len(no):]) + "</p>")
    if lines:
        parts.append('<ol class="claim-els">')
        for ln in lines:
            x = e(ln["anchor"])
            m = ANCHOR_RE.match(ln["anchor"])
            off = f"{m.group(4)}–{m.group(5)}" if m and m.group(4) else ""
            btn = (f'<button class="el-copy" type="button" hidden aria-label="Copy claim {n}, element {e(ln["k"])} with its position">'
                   f"Copy with position</button>") if copy else ""
            parts.append(f'<li id="{x}"><a class="el-link" href="#{x}"><span class="vh">Claim {n}, element </span>e{e(ln["k"])}</a>'
                         f'<span class="el-line">{e(ln["head"])}<span class="el-text">{e(ln["text"])}</span>'
                         f'<span class="el-sep">{e(ln["tail"])}</span></span><span class="el-off">{off}</span>{btn}</li>')
        parts.append("</ol>")
    return parts, tag


def render_claims(graph):
    """The claims as HTML for the record page, as a tree (release AD, the re-skin of 2026-11-05): each independent claim in
    a box of its own (div.claim.top), its dependent claims under it in a collapsed details element (its summary names them and the
    claims they refer to), each dependent indented by its depth in the chain. Every text from the claim tree goes through
    html.escape; every claim and element gets its anchor as its id. Inside a claim the only text is the heading, the
    dependency line, the anchor, the link labels, the offsets, the copy button and the claim text itself (.claim-lead and
    each .el-line), so the claim text reads back from the page exactly."""
    e = html.escape
    out = []
    for top, deps in tree_of(graph):
        parts, tag = _claim_html(top, 0, copy=True)
        if deps:
            parts.append(f'<details class="deps"><summary>{e(deps_summary(top, deps))}</summary><div class="deps-list">')
            for c, depth in deps:
                sub, subtag = _claim_html(c, depth, copy=False)
                parts.extend(sub)
                parts.append(f"</{subtag}>")
            parts.append("</div></details>")
        parts.append(f"</{tag}>")
        out.append("".join(parts))
    return "\n".join(out)


def render_printed(graph):
    """The claims as printed, for the page's As printed view: one paragraph a claim, its text as the long-text file prints it
    (the claim number included), escaped. No ids: the anchors are the tree's, and the page script shows the tree when a
    link names one."""
    e = html.escape
    return "\n".join(f'<p class="cp">{e(c["text"])}</p>' for c in graph["claims"])


def page_claims(searcher, pid):
    """The record page's claims section: {"state", "html", "printed", "count", "independent", "text_data_version",
    "structure_version"}, state ok, no_text_file, no_claims or not_shown. Never raises: the record page stays as it was
    when its claims cannot be shown."""
    out = {"state": "not_shown", "html": "", "printed": "", "count": 0, "independent": "", "text_data_version": None,
           "structure_version": cg.STRUCTURE_VERSION}
    try:
        tree = graph_for(searcher, pid)
        out["text_data_version"] = searcher.text_data_version()
        out["state"] = PAGE_STATES.get(tree.state, "not_shown")
        if tree.state == OK:
            out["html"] = mark_safe(render_claims(tree.graph))  # every text in it went through html.escape
            out["printed"] = mark_safe(render_printed(tree.graph))
            out["count"] = len(tree.graph["claims"])
            ind = [top["number"] for top, _ in tree_of(tree.graph) if top.get("independent") or not top.get("depends_on")]
            out["independent"] = ("independent " + _numbers_text(ind)) if ind else ""
    except Exception:  # noqa: BLE001  (a claim set the page cannot lay out must not take the record down)
        logger.exception("record page %s: the claims are not shown", pid)
        out.update(state="not_shown", html="", printed="", count=0, independent="")
    return out
