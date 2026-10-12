"""The human-readable status sentence and the banned-strings check (PatentRef never-regress item 8,
skill `skills/verdict-wording/SKILL.md` in the patentref repo: the templates below are that skill's
Python implementation and the only place a status sentence is written).

Every sentence is a record fact with `as_of` and the data version. No sentence states a legal conclusion.
The USPTO record prints dates and events (filing, grant, term adjustment, a terminal disclaimer, the
maintenance-fee events, the withdrawn flag), not a term end: the term end is computed from those dates
(35 USC 154, `lapse.expiry`), so the sentences say "the term end computed from the dates in the USPTO
record", never that the record shows a term ended (2026-10-11 audit 2, finding G2). A fee lapse is an
event the maintenance-fee record does print, so that sentence names the record and the date of its entry.
Everything from TEMPLATES to plain is the reference block of the second fresh-agent wording review of 2026-10-11
(patentref ops/reviews/2026-10-11_lapse-sentence-templates_review-2.md), verbatim: the first review's templates with "a
date not yet passed", the expired_term clause on the USPTO 35 USC 156 list, and the reviewed strings for a patent on
that list (PTE_TEMPLATES, PTE_CONFLICT), for one whose reissue is on it (EXPIRED_TERM_REISSUED) and for a server with
no list file (EXPIRED_TERM_NO_LIST). A changed word reopens the review (verdict-wording rule 5).
`check_banned()` is the wording test's core: the verdict tool's responses are run through it before
they leave the process.
"""
import re

BANNED = ("free to use", "freedom to operate", "will survive", "you need a", "you should file", "we recommend",
          "crackpot", "impossible physics", chr(0x2014))
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")

TEMPLATES = {
    "active": "As of {as_of}, the USPTO record for US {id} shows no unresolved maintenance-fee lapse{reinstated_text}, and the term end computed from the dates in that record is {expiry_date} ({basis_text}), a date not yet passed (data version {v}).",
    "expired_term": "As of {as_of}, the term end computed from the dates in the USPTO record for US {id} is {expiry_date} ({basis_text}), a date that has passed, and the USPTO list of patent terms extended under 35 USC 156, updated through {pte_list_through}, does not name this patent (data version {v}).",
    "expired_fee_nonpayment": "As of {as_of}, the USPTO maintenance-fee record for US {id} has an entry dated {lapse_date} recording that the patent expired for nonpayment of a maintenance fee, and no reinstatement after it (data version {v}).",
    "withdrawn": "As of {as_of}, the USPTO record shows US {id} as withdrawn from issue (data version {v}).",
    "unknown": "As of {as_of}, the USPTO record for US {id} does not settle its status: no term end can be computed from the dates it holds (data version {v}).",
}
# expired_term when no copy of the 35 USC 156 list is loaded (PTE_156_FILE=none or no file)
EXPIRED_TERM_NO_LIST = "As of {as_of}, the term end computed from the dates in the USPTO record for US {id} is {expiry_date} ({basis_text}), a date that has passed; it does not count any patent term extension for regulatory review (35 USC 156), which this record does not hold (data version {v})."
# the two templates that state a computed term end need the date; without it the record does not settle the status
NEEDS_EXPIRY_DATE = ("active", "expired_term")

# A patent on the USPTO list of patent terms extended under 35 USC 156: the list prints the original expiration date and
# the period of extension, not the extended date, so the sentence gives both and the date they add up to.
PTE_TEMPLATES = {
    "active": "As of {as_of}, the USPTO record for US {id} shows no unresolved maintenance-fee lapse{reinstated_text}, and the USPTO list of patent terms extended under 35 USC 156 gives it an original expiration date of {pte_original} and an extension of {pte_period}, which puts its term end at {expiry_date}, a date not yet passed{record_later} (data version {v}).",
    "expired_term": "As of {as_of}, the USPTO list of patent terms extended under 35 USC 156 gives US {id} an original expiration date of {pte_original} and an extension of {pte_period}, which puts its term end at {expiry_date}, a date that has passed{record_later} (data version {v}).",
}
# expired_term for a patent whose reissue (not the patent itself) is on the 35 USC 156 list (35 USC 251, 252)
EXPIRED_TERM_REISSUED = "As of {as_of}, the term end computed from the dates in the USPTO record for US {id} is {expiry_date} ({basis_text}), a date that has passed, but the USPTO record shows it reissued as US {pte_reissue}, a patent the USPTO list of patent terms extended under 35 USC 156, updated through {pte_list_through}, names (data version {v})."
# the list's date is earlier than the record's own term and only one of the two has passed: status unknown
PTE_CONFLICT = "As of {as_of}, the USPTO sources for US {id} do not settle its status: the USPTO list of patent terms extended under 35 USC 156 gives it an original expiration date of {pte_original} and an extension of {pte_period}, which puts its term end at {pte_end}, {pte_end_state}, while the term end computed from the dates in the USPTO record, before any extension, is {record_end}, {record_end_state} (data version {v})."

TERM_TEXT = {"term_17y": "17 years from grant", "term_14y": "14 years from grant", "term_15y": "15 years from grant"}

# The bundle 4 wording review of 2026-10-12 (patentref ops/reviews/2026-10-12_bundle-4_wording_review.md, items 6 and 7):
# a list date read by hand names the cell as printed, and the record page's term-end row for a patent whose two USPTO term
# ends disagree on whether the term has passed (expiry_date_estimated null) gives both dates.
PRINTED_TEXT = "{date} (printed on the list as {printed})"
TERM_END_DISAGREE = "none: the dates on the USPTO 35 USC 156 list add up to {pte_end} and the record's own dates to {record_end}"


# Independent audit 4 of 2026-10-12, finding F3 (bundle 5, B10; patentref lapse/parents.py): a parent date the record
# contradicts and no other record dates, when that parent could be the earliest, leaves the record without a term end.
# These strings await the bundle 5 fresh-agent wording review (deploy step 0 of its handoff).
UNKNOWN_PARENT_DATE = "As of {as_of}, the USPTO record for US {id} does not settle its status: it gives parent application {parent_number} the filing date {parent_date}, which {parent_why}, and PatentRef holds no other USPTO record that gives that application's filing date (data version {v})."
# The coordinator's decision of 2026-10-12: when every filing date the record leaves possible for that parent (the row's
# own date when it is a possible filing date, and the year or series years its number names, or any day from 1978-06-01
# when the number names no possible year or a serial never reached in that year; never after the patent's own filing)
# gives the same status, that status is served with no term end (patentref lapse.expiry, parent_unsettled["settled"]), in
# these sentences, with PARENT_SPAN saying on which dates it holds (the bundle 5 wording review, step 0 items a and b).
SETTLED_PARENT = {
    "active": "As of {as_of}, the USPTO record for US {id} shows no unresolved maintenance-fee lapse{reinstated_text}; it gives parent application {parent_number} the filing date {parent_date}, which {parent_why}, and PatentRef holds no other USPTO record that gives that application's filing date, so no single term end can be computed, but {parent_span}, the term end computed from the record's dates has not passed (data version {v}).",
    "expired_term": "As of {as_of}, the USPTO record for US {id} gives parent application {parent_number} the filing date {parent_date}, which {parent_why}, and PatentRef holds no other USPTO record that gives that application's filing date, so no single term end can be computed, but {parent_span}, the term end computed from the record's dates has passed, and the USPTO list of patent terms extended under 35 USC 156, updated through {pte_list_through}, does not name this patent (data version {v}).",
    "expired_fee_nonpayment": "As of {as_of}, the USPTO maintenance-fee record for US {id} has an entry dated {lapse_date} recording that the patent expired for nonpayment of a maintenance fee, and no reinstatement after it; the record gives parent application {parent_number} the filing date {parent_date}, which {parent_why}, and PatentRef holds no other USPTO record that gives that application's filing date, so no single term end can be computed, but {parent_span}, the grace period of the unpaid fee ran out on or before the term end computed from the record's dates (data version {v}).",
}
# expired_term, settled that way, when no copy of the 35 USC 156 list is loaded
SETTLED_PARENT_EXPIRED_NO_LIST = "As of {as_of}, the USPTO record for US {id} gives parent application {parent_number} the filing date {parent_date}, which {parent_why}, and PatentRef holds no other USPTO record that gives that application's filing date, so no single term end can be computed, but {parent_span}, the term end computed from the record's dates has passed; it does not count any patent term extension for regulatory review (35 USC 156), which this record does not hold (data version {v})."
TERM_END_PARENT = "none: the USPTO record gives parent application {parent_number} the filing date {parent_date}, which {parent_why}"
PARENT_WHY = {
    "pct_year": "is not in the year that application's number names",
    "pct_before_1978": "is before 1978-06-01, the first day an international application could be filed",
    "us_series": "is outside the years in which applications of that serial number's series were filed",
    "us_before_1950": "is before 1950, earlier than PatentRef accepts for a parent application",
    "pct_no_year": "cannot be checked against that application's number, since the number names no possible filing year",
}


PARENT_SPAN = {
    "number_and_row": "on that date and on any filing date that application's number allows",
    "number": "on any filing date that application's number allows",
    "open": "on any day from 1978-06-01 to this patent's own filing",
}


def parent_number_text(number):
    """A US application number as the USPTO writes it (13/655,875); a PCT number as printed."""
    n = str(number or "")
    return f"{n[:2]}/{n[2:5]},{n[5:]}" if (len(n) == 8 and n.isdigit()) else n


def _parent_args(record):
    u = (record or {}).get("parent_unsettled") or {}
    return {"parent_number": parent_number_text(u.get("number")), "parent_date": u.get("date"),
            "parent_why": PARENT_WHY.get(u.get("why"), "does not fit that application's number"),
            "parent_span": PARENT_SPAN.get(u.get("span"), PARENT_SPAN["number"])}


def pte_original_text(row):
    """The list's original expiration date as shown: the date, and the cell as printed when the date was read by hand."""
    row = row or {}
    date = row.get("original_expiry")
    return PRINTED_TEXT.format(date=date, printed=row["printed"]) if (date and row.get("printed")) else date


def term_end_text(record):
    """The record page's term-end text when the engine gives no term end because the 35 USC 156 list and the record
    disagree on whether the term has passed, or because a parent date the record contradicts leaves the term unsettled
    (bundle 5, B10) (`record` is expiry.expiry_record output); None otherwise."""
    record = record or {}
    row = record.get("pte_156") or {}
    if record.get("expiry_date_estimated"):
        return None
    if record.get("parent_unsettled") and not row:
        return TERM_END_PARENT.format(**_parent_args(record))
    if not record.get("record_term_end") or not row.get("extended_expiry"):
        return None
    return TERM_END_DISAGREE.format(pte_end=row["extended_expiry"], record_end=record["record_term_end"])


def basis_text(basis, record=None, sentence=False):
    """The basis as a short clause. `record` (expiry.expiry_record output) supplies term_from_parent, disclaimer_applied,
    pte_156 and status. sentence=True gives the term part only, for the plain sentence ('20 years from the filing of a
    parent application on 2005-12-23, plus 129 days of patent term adjustment'); sentence=False glosses every code, for
    the record page's Basis row."""
    record = record or {}
    basis = basis or []
    listed = any(b.startswith("pte_156:") for b in basis)
    parts = []
    for b in basis:
        if b == "term_20y":
            parts.append(f"20 years from the filing of a parent application on {record['term_from_parent']}"
                         if record.get("term_from_parent") else "20 years from filing")
        elif b in TERM_TEXT:
            parts.append(TERM_TEXT[b])
        elif b.startswith("pta_days:") and b[9:] not in ("0", ""):
            parts.append(f"plus {b[9:]} days of patent term adjustment")
        elif b.startswith("fee_lapse:") and not sentence:
            parts.append(f"fee-lapse entry dated {b[10:]}")
        elif b.startswith("reinstated:") and not sentence:
            parts.append(f"reinstatement dated {b[11:]}")
        elif b.startswith("pte_156:") and not sentence:
            row = record.get("pte_156") or {}
            parts.append(f"on the USPTO list of patent terms extended under 35 USC 156: original expiration date "
                         f"{pte_original_text(row)} plus {row['period']}" if row.get("period")
                         else f"on the USPTO list of patent terms extended under 35 USC 156: {b[8:]} days of extension")
    if "terminal_disclaimer" in basis and not listed:
        if record.get("disclaimer_applied"):
            parts.append("cut to this date by a terminal disclaimer")
        elif not sentence or record.get("status") == "active":
            parts.append("not counting a terminal disclaimer on record, which can end the term earlier")
    return ", ".join(parts) if parts else "term rule"


def _state(d, as_of):
    return "a date that has passed" if d < as_of else "a date not yet passed"


def plain(record, patent_id, data_version):
    """One sentence from the template for the record's status (`record` is `expiry.expiry_record` output)."""
    status = record.get("status") or "unknown"
    row = record.get("pte_156") or {}
    settled = bool((record.get("parent_unsettled") or {}).get("settled")) and not row and status in SETTLED_PARENT
    if status in NEEDS_EXPIRY_DATE and not record.get("expiry_date_estimated") and not settled:
        status = "unknown"
    as_of, rend = record.get("as_of"), record.get("record_term_end")
    if settled:
        tpl = SETTLED_PARENT_EXPIRED_NO_LIST if (status == "expired_term" and not record.get("pte_list_through")) \
            else SETTLED_PARENT[status]
    elif status == "unknown" and row and rend:
        tpl = PTE_CONFLICT
    elif row and status in PTE_TEMPLATES:
        tpl = PTE_TEMPLATES[status]
    elif status == "expired_term" and record.get("pte_reissue") and record.get("pte_list_through"):
        tpl = EXPIRED_TERM_REISSUED
    elif status == "expired_term" and not record.get("pte_list_through"):
        tpl = EXPIRED_TERM_NO_LIST
    elif status == "unknown" and record.get("parent_unsettled") and not row:
        tpl = UNKNOWN_PARENT_DATE
    else:
        tpl = TEMPLATES.get(status, TEMPLATES["unknown"])
    pid = patent_id[2:] if patent_id.upper().startswith("US") else patent_id
    rd = record.get("reinstated_date")
    return tpl.format(as_of=as_of, id=pid, v=data_version, expiry_date=record.get("expiry_date_estimated"),
                      basis_text=basis_text(record.get("basis"), dict(record, status=status), sentence=True),
                      lapse_date=record.get("lapse_date"),
                      reinstated_text=f" (a reinstatement dated {rd} ended an earlier lapse)" if rd else "",
                      pte_original=pte_original_text(row), pte_period=row.get("period"),
                      pte_end=row.get("extended_expiry"), pte_end_state=_state(row.get("extended_expiry") or "", as_of or ""),
                      record_end=rend, record_end_state=_state(rend or "", as_of or ""),
                      record_later=(f"; the term end computed from the dates in the USPTO record, before any extension, is later, {rend}"
                                    if rend and row else ""),
                      pte_list_through=record.get("pte_list_through"),
                      pte_reissue=(record.get("pte_reissue") or "")[2:] if (record.get("pte_reissue") or "").startswith("US") else record.get("pte_reissue"),
                      **_parent_args(record))


def check_banned(text):
    """The banned strings found in `text` (case-insensitive), em-dashes and emoji included; empty when clean."""
    low = (text or "").lower()
    found = [b for b in BANNED if b in low]
    if _EMOJI.search(text or ""):
        found.append("emoji")
    if re.search(r'"confidence"\s*:', low):
        found.append("confidence")
    return found
