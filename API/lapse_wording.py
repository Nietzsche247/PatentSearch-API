"""The human-readable status sentence and the banned-strings check (PatentRef never-regress item 8,
skill `skills/verdict-wording/SKILL.md` in the patentref repo: the templates below are that skill's
Python implementation and the only place a status sentence is written).

Every sentence is a record fact with `as_of` and the data version. No sentence states a legal conclusion.
The USPTO record prints dates and events (filing, grant, term adjustment, a terminal disclaimer, the
maintenance-fee events, the withdrawn flag), not a term end: the term end is computed from those dates
(35 USC 154, `lapse.expiry`), so the sentences say "the term end computed from the dates in the USPTO
record", never that the record shows a term ended (2026-10-11 audit 2, finding G2). A fee lapse is an
event the maintenance-fee record does print, so that sentence names the record and the date of its entry.
TEMPLATES, TERM_TEXT, basis_text and plain are the replacement text of the fresh-agent wording review of 2026-10-11
(patentref ops/reviews/2026-10-11_lapse-sentence-templates_review.md section 2.3), verbatim; PTE_TEMPLATES and the
pte_156 gloss are builder wording added after it for patents on the USPTO 35 USC 156 list, awaiting their own review.
`check_banned()` is the wording test's core: the verdict tool's responses are run through it before
they leave the process.
"""
import re

BANNED = ("free to use", "freedom to operate", "will survive", "you need a", "you should file", "we recommend",
          "crackpot", "impossible physics", chr(0x2014))
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")

TEMPLATES = {
    "active": "As of {as_of}, the USPTO record for US {id} shows no unresolved maintenance-fee lapse{reinstated_text}, and the term end computed from the dates in that record is {expiry_date} ({basis_text}), a date still ahead (data version {v}).",
    "expired_term": "As of {as_of}, the term end computed from the dates in the USPTO record for US {id} is {expiry_date} ({basis_text}), a date that has passed; it does not count any patent term extension for regulatory review (35 USC 156), which this record does not hold (data version {v}).",
    "expired_fee_nonpayment": "As of {as_of}, the USPTO maintenance-fee record for US {id} has an entry dated {lapse_date} recording that the patent expired for nonpayment of a maintenance fee, and no reinstatement after it (data version {v}).",
    "withdrawn": "As of {as_of}, the USPTO record shows US {id} as withdrawn from issue (data version {v}).",
    "unknown": "As of {as_of}, the USPTO record for US {id} does not settle its status: no term end can be computed from the dates it holds (data version {v}).",
}
# the two templates that state a computed term end need the date; without it the record does not settle the status
NEEDS_EXPIRY_DATE = ("active", "expired_term")

# A patent on the USPTO list of patent terms extended under 35 USC 156 (lapse/pte156.py in the patentref repo): the
# term end is the list's, so these two replace active and expired_term, and expired_term's 156 clause, which says the
# extension is not counted, is not used. Builder wording of 2026-10-11, outside the review of that day: it needs its
# own fresh-agent review (verdict-wording rule 5) before it ships.
PTE_TEMPLATES = {
    "active": "As of {as_of}, the USPTO record for US {id} shows no unresolved maintenance-fee lapse{reinstated_text}, and the USPTO list of patent terms extended under 35 USC 156 gives its term end as {expiry_date} (the original expiration date {pte_original} plus {pte_days} days of extension), a date still ahead (data version {v}).",
    "expired_term": "As of {as_of}, the USPTO list of patent terms extended under 35 USC 156 gives the term end of US {id} as {expiry_date} (the original expiration date {pte_original} plus {pte_days} days of extension), a date that has passed (data version {v}).",
}

TERM_TEXT = {"term_17y": "17 years from grant", "term_14y": "14 years from grant", "term_15y": "15 years from grant"}


def basis_text(basis, record=None, sentence=False):
    """The basis as a short clause. `record` (expiry.expiry_record output) supplies term_from_parent, disclaimer_applied
    and status. sentence=True gives the term part only, for the plain sentence ('20 years from the filing of a parent
    application on 2005-12-23, plus 129 days of patent term adjustment'); sentence=False glosses every code, for the
    record page's Basis row."""
    record = record or {}
    basis = basis or []
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
        elif b.startswith("pte_156:") and not sentence:  # builder wording, outside the 2026-10-11 review (PTE_TEMPLATES)
            parts.append(f"extended by {b[8:]} days under 35 USC 156 (USPTO list of extended patent terms)")
    if "terminal_disclaimer" in basis:
        if record.get("disclaimer_applied"):
            parts.append("cut to this date by a terminal disclaimer")
        elif not sentence or record.get("status") == "active":
            parts.append("not counting a terminal disclaimer on record, which can end the term earlier")
    return ", ".join(parts) if parts else "term rule"


def plain(record, patent_id, data_version):
    """One sentence from the template for the record's status (`record` is `expiry.expiry_record` output)."""
    status = record.get("status") or "unknown"
    if status in NEEDS_EXPIRY_DATE and not record.get("expiry_date_estimated"):
        status = "unknown"
    pte = record.get("pte_156") if status in PTE_TEMPLATES else None
    tpl = PTE_TEMPLATES[status] if pte else TEMPLATES.get(status, TEMPLATES["unknown"])
    pid = patent_id[2:] if patent_id.upper().startswith("US") else patent_id
    rd = record.get("reinstated_date")
    return tpl.format(as_of=record.get("as_of"), id=pid, v=data_version, expiry_date=record.get("expiry_date_estimated"),
                      basis_text=basis_text(record.get("basis"), dict(record, status=status), sentence=True),
                      lapse_date=record.get("lapse_date"),
                      reinstated_text=f" (a reinstatement dated {rd} ended an earlier lapse)" if rd else "",
                      pte_original=(pte or {}).get("original_expiry"), pte_days=(pte or {}).get("extension_days"))


def check_banned(text):
    """The banned strings found in `text` (case-insensitive), em-dashes and emoji included; empty when clean."""
    low = (text or "").lower()
    found = [b for b in BANNED if b in low]
    if _EMOJI.search(text or ""):
        found.append("emoji")
    if re.search(r'"confidence"\s*:', low):
        found.append("confidence")
    return found
