"""The human-readable status sentence and the banned-strings check (PatentRef never-regress item 8,
skill `skills/verdict-wording/SKILL.md` in the patentref repo: the templates below are that skill's
Python implementation and the only place a status sentence is written).

Every sentence is a record fact with `as_of` and the data version. No sentence states a legal conclusion.
The USPTO record prints dates and events (filing, grant, term adjustment, a terminal disclaimer, the
maintenance-fee events, the withdrawn flag), not a term end: the term end is computed from those dates
(35 USC 154, `lapse.expiry`), so the sentences say "the term end computed from the dates in the USPTO
record", never that the record shows a term ended (2026-10-11 audit 2, finding G2). A fee lapse is an
event the maintenance-fee record does print, so that sentence names the record and the event's date.
`check_banned()` is the wording test's core: the verdict tool's responses are run through it before
they leave the process.
"""
import re

BANNED = ("free to use", "freedom to operate", "will survive", "you need a", "you should file", "we recommend",
          "crackpot", "impossible physics", chr(0x2014))
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")

TEMPLATES = {
    "active": "As of {as_of}, the USPTO record for US {id} shows no unresolved maintenance-fee lapse, and the term end computed from the dates in that record is {expiry_date} ({basis_text}), a date still ahead (data version {v}).",
    "expired_term": "As of {as_of}, the term end computed from the dates in the USPTO record for US {id} is {expiry_date} ({basis_text}), a date that has passed (data version {v}).",
    "expired_fee_nonpayment": "As of {as_of}, the USPTO maintenance-fee record for US {id} shows an expiry for nonpayment of a maintenance fee dated {lapse_date}, and no later reinstatement (data version {v}).",
    "withdrawn": "As of {as_of}, the USPTO record shows US {id} as withdrawn (data version {v}).",
    "unknown": "As of {as_of}, the USPTO record for US {id} does not settle its status: no term end can be computed from the dates it holds (data version {v}).",
}
# the two templates that state a computed term end need the date; without it the record does not settle the status
NEEDS_EXPIRY_DATE = ("active", "expired_term")

BASIS_TEXT = {
    "term_20y": "20 years from filing", "term_17y": "17 years from grant", "term_14y": "14 years from grant",
    "term_15y": "15 years from grant", "terminal_disclaimer": "terminal disclaimer on record",
}


def basis_text(basis):
    """The basis list as a short clause: '20 years from filing plus 129 days of term adjustment, reinstated 2017-08-30'."""
    parts = []
    for b in basis or []:
        if b in BASIS_TEXT:
            parts.append(BASIS_TEXT[b])
        elif b.startswith("pta_days:") and b[9:] not in ("0", ""):
            parts.append(f"plus {b[9:]} days of term adjustment")
        elif b.startswith("reinstated:"):
            parts.append(f"reinstated {b[11:]}")
        elif b.startswith("fee_lapse:"):
            parts.append(f"fee lapse {b[10:]}")
    return ", ".join(parts) if parts else "term rule"


def plain(record, patent_id, data_version):
    """One sentence from the template for the record's status (`record` is `expiry.expiry_record` output)."""
    status = record.get("status") or "unknown"
    if status in NEEDS_EXPIRY_DATE and not record.get("expiry_date_estimated"):
        status = "unknown"
    tpl = TEMPLATES.get(status, TEMPLATES["unknown"])
    pid = patent_id[2:] if patent_id.upper().startswith("US") else patent_id
    return tpl.format(as_of=record.get("as_of"), id=pid, v=data_version, expiry_date=record.get("expiry_date_estimated"),
                      basis_text=basis_text(record.get("basis")), lapse_date=record.get("lapse_date"))


def check_banned(text):
    """The banned strings found in `text` (case-insensitive), em-dashes and emoji included; empty when clean."""
    low = (text or "").lower()
    found = [b for b in BANNED if b in low]
    if _EMOJI.search(text or ""):
        found.append("emoji")
    if re.search(r'"confidence"\s*:', low):
        found.append("confidence")
    return found
