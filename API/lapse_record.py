"""The record page's identity block and its wording check (release AD, the re-skin of 2026-11-05: the page opens with the
patent's number, title and identity, then its claims, then its status).

identity() reads the fields the build note names from the same snapshot GET /api/v1/patent/<id>/ answers from (the searcher's
own read, so the same columns, groups and order), never typed in: the grant date, the filing date and application number,
the type and kind, a prior publication the record lists, the assignees as printed with the PatentsView label beside them,
the inventors, the current CPC groups, the abstract and the two citation counts. Each item is shown only when the record
has it; a read that fails leaves the page as it was without the block (logged).

own_text() is the page's own words: the HTML with every element marked data-user-content="true" (the patent's title,
abstract, names and claim text), every script and every style left out. The record page runs lapse_wording.check_banned
over it before it answers, so the check covers everything the page writes and nothing the patent says.
"""
import json
import logging
import re
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

FIELDS = ["patent_id", "patent_title", "patent_date", "patent_type", "wipo_kind", "patent_abstract",
          "patent_num_us_patents_cited", "patent_num_times_cited_by_us_patents", "application", "assignees",
          "assignees.raw_assignee_organization", "assignees.raw_assignee_individual_name_first",
          "assignees.raw_assignee_individual_name_last", "assignees.disambiguated", "inventors", "cpc_current",
          "us_related_documents"]
MAX_NAMES = 20      # inventors and assignees shown; the rest counted
MAX_CPC = 24
TYPES = {"utility": "Utility", "design": "Design", "plant": "Plant", "reissue": "Reissue",
         "defensive publication": "Defensive publication", "statutory invention registration": "Statutory invention registration"}


def application_number_text(n):
    """17828213 or 17/828213 as the USPTO writes it, 17/828,213; anything else as printed."""
    s = str(n or "").strip()
    d = re.sub(r"[^0-9]", "", s)
    if len(d) == 8 and re.fullmatch(r"[0-9]{2}/?[0-9]{6}", s.replace(",", "")):
        return f"{d[:2]}/{d[2:5]},{d[5:]}"
    return s


def publication_text(number, kind=None):
    """A US pre-grant publication number 20230380318 as US 2023/0380318 (with its kind when the record gives it)."""
    s = re.sub(r"[^0-9A-Za-z]", "", str(number or ""))
    m = re.fullmatch(r"(?:US)?((?:19|20)[0-9]{2})([0-9]{7})(?:A[0-9])?", s)
    if not m:
        return None
    return f"US {m.group(1)}/{m.group(2)}" + (f" {kind}" if kind and re.fullmatch(r"A[0-9]", str(kind)) else "")


def _place(city, state, country):
    bits = [x for x in (city, state) if x]
    out = ", ".join(bits)
    if country:
        out = f"{out} ({country})" if out else f"({country})"
    return out


def _person(first, last):
    return " ".join(x for x in (first, last) if x)


def _more(items, cap):
    shown = items[:cap]
    return shown, max(0, len(items) - cap)


def identity(searcher, pid):
    """The record page's identity block for one patent, as a dict of display strings and lists (empty when the read fails or
    the snapshot has no row)."""
    try:
        ans = searcher.search("patents", {"terms": {"patent_id": [pid]}}, FIELDS, 1, None, None)
        hits = ans["hits"]["hits"]
    except Exception:  # noqa: BLE001  (the page shows its record without the block)
        logger.exception("record page %s: the identity block is not shown", pid)
        return {}
    if not hits:
        return {}
    src = hits[0].get("_source") or {}
    out = {}
    app = (src.get("application") or [{}])[0] or {}
    bits = []
    if src.get("patent_date"):
        bits.append(f"Granted {src['patent_date']}")
    if app.get("filing_date"):
        bits.append(f"Filed {app['filing_date']}")
    if app.get("application_id"):
        bits.append(f"Application {application_number_text(app['application_id'])}")
    if src.get("patent_type"):
        bits.append(TYPES.get(str(src["patent_type"]).lower(), str(src["patent_type"])))
    if src.get("wipo_kind"):
        bits.append(f"Kind {src['wipo_kind']}")
    for r in src.get("us_related_documents") or []:
        if (r.get("related_doc_type") or "") == "related-publication" and not r.get("related_doc_kind"):
            pub = publication_text(r.get("related_doc_number"), r.get("wipo_kind"))
            if pub:
                bits.append(f"Prior publication {pub}" + (f", {r['related_doc_published_date']}" if r.get("related_doc_published_date") else ""))
                break
    out["line"] = " · ".join(bits)
    assignees = []
    for a in src.get("assignees") or []:
        printed = a.get("raw_assignee_organization") or _person(a.get("raw_assignee_individual_name_first"), a.get("raw_assignee_individual_name_last"))
        label = a.get("assignee_organization") or _person(a.get("assignee_individual_name_first"), a.get("assignee_individual_name_last"))
        dis = a.get("disambiguated")
        if not printed and dis is False:
            printed = label   # a name taken as printed from the weekly grant XML (no PatentsView row yet)
        place = _place(a.get("assignee_city"), a.get("assignee_state"), a.get("assignee_country"))
        assignees.append({"printed": printed or "", "place": place,
                          "label": label if (label and dis is not False and label != printed) else ""})
    out["assignees"], out["assignees_more"] = _more([a for a in assignees if a["printed"] or a["label"]], MAX_NAMES)
    out["assignee_printed"] = any(a["printed"] for a in out["assignees"])
    inventors = []
    for i in src.get("inventors") or []:
        name = _person(i.get("inventor_name_first"), i.get("inventor_name_last"))
        if name:
            place = _place(i.get("inventor_city"), i.get("inventor_state"), i.get("inventor_country"))
            inventors.append(f"{name}, {place}" if place else name)
    out["inventors"], out["inventors_more"] = _more(inventors, MAX_NAMES)
    cpc = []
    for c in src.get("cpc_current") or []:
        g = c.get("cpc_group_id")
        if g and g not in cpc:
            cpc.append(g)
    out["cpc"], out["cpc_more"] = _more(cpc, MAX_CPC)
    out["abstract"] = src.get("patent_abstract") or ""
    for k, name in (("cited", "patent_num_us_patents_cited"), ("cited_by", "patent_num_times_cited_by_us_patents")):
        v = src.get(name)
        try:
            out[k] = int(v) if v is not None and str(v) != "" else None
        except (TypeError, ValueError):
            out[k] = None
    return out


def json_word(v):
    """A record value as the record's JSON prints it: false, true, null, or the string itself."""
    if isinstance(v, bool) or v is None:
        return json.dumps(v)
    return str(v)


def elsewhere(pid, family):
    """The rail's outbound links: USPTO Patent Center, USPTO Global Dossier (when the family block has its address), Google
    Patents and Lens, each the patent's own page or search there."""
    links = [("USPTO Patent Center", f"https://patentcenter.uspto.gov/applications?patentNumber={pid}")]
    if family and family.get("global_dossier_url"):
        links.append(("USPTO Global Dossier", family["global_dossier_url"]))
    links.append(("Google Patents", f"https://patents.google.com/patent/US{pid}"))
    links.append(("Lens", f"https://www.lens.org/lens/search/patent/list?q=US{pid}"))
    return links


VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Own(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.out = [], []

    def handle_starttag(self, tag, attrs):
        if tag in VOID:
            return
        skip = bool(self.stack and self.stack[-1][1]) or tag in ("script", "style") or dict(attrs).get("data-user-content") == "true"
        self.stack.append((tag, skip))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if not (self.stack and self.stack[-1][1]):
            self.out.append(data)


def own_text(page):
    """The page's own words: its text without the data-user-content elements, scripts and styles."""
    p = _Own()
    p.feed(page or "")
    p.close()
    return " ".join(p.out)
