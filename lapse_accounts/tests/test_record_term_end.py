"""The record page's term-end wording (release AD, the coordinator's item of 2026-11-10): the term-end sentence of some record
pages in service contradicted itself. The active sentence of a patent whose record shows a terminal disclaimer without
a date read "the term end computed from the dates in that record is 2041-03-25 (20 years from ..., not counting a terminal
disclaimer on record, which can end the term earlier), a date not yet passed": it called the date the term end and said in
the same breath that the term can end earlier (US 12630350 and US 12744489 among the pages checked on 2026-11-10; the
others checked are term_20y with or without a parent date and term adjustment). A dated disclaimer that set the date read "(20 years from filing,
plus 100 days of patent term adjustment, cut to this date by a terminal disclaimer)", giving the 20-year rule as the origin
of a date the disclaimer set.

Each case below runs the status engine (lapse.expiry from the patentref checkout) and the fork's wording, and checks the
sentence, the Basis row's gloss and the record page's term-end row: undated and dated disclaimers (one that set the date,
one whose date is later, and, after the audit of 2026-11-11, one dated on or before the grant date, one with no grant date
and one with no computed date), a date from the 35 USC 156 list, term adjustment, a parent date, a fee lapse with the term
still ahead, a fee lapse that came
before a term that has since passed, a fee lapse after the term end, a reinstatement, and a sweep over their combinations
that no sentence names a disclaimer inside the date's parenthesis unless the disclaimer set the date, and that "not yet
passed" and "has passed" agree with the date and the as-of.

  python -m pytest lapse_accounts/tests/test_record_term_end.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import itertools
import re

import pytest

from API import lapse_group, lapse_wording

AS_OF = "2026-11-03"
V = "20261103.2"
TD = "This patent is subject to a terminal disclaimer."


@pytest.fixture(scope="module")
def expiry():
    mod = lapse_group.expiry_module()
    if mod is None:
        pytest.fail("the status engine lapse.expiry is not importable (set LAPSE_REPO_DIR)")
    return mod


def rec_of(expiry, pid, **kw):
    kw.setdefault("as_of", AS_OF)
    kw.setdefault("pte", None)
    return expiry.expiry_record(patent_id="US" + pid, **kw)


def served(rec):
    """The served record's fields the page reads (lapse_group.verdict_with_engine copies them)."""
    return {k: rec.get(k) for k in ("status", "basis", "expiry_date_estimated", "lapse_date", "reinstated_date")}


def paren(sentence, date):
    """The parenthesis that follows the date in a sentence."""
    m = re.search(re.escape(date) + r" \(([^()]*)\)", sentence)
    return m.group(1) if m else None


def test_two_pages_in_service_with_an_undated_disclaimer(expiry):
    # US 12630350: a continuation of a 2021-03-25 application, a terminal disclaimer without a date, no adjustment
    rec = rec_of(expiry, "12630350", filing_date="2023-03-15", grant_date="2026-05-19", terminal_disclaimer=True,
                 parent_filing_date="2021-03-25")
    assert rec["status"] == "active" and rec["expiry_date_estimated"] == "2041-03-25"
    s = lapse_wording.plain(rec, "US12630350", V)
    assert s == ("As of 2026-11-03, the USPTO record for US 12630350 shows no unresolved maintenance-fee lapse, and the term end "
                 "computed from the dates in that record, leaving out a terminal disclaimer it shows without a date, is 2041-03-25 "
                 "(20 years from the filing of a parent application on 2021-03-25), a date not yet passed; that disclaimer can only "
                 "make the term end earlier (data version 20261103.2).")
    assert "which can end the term earlier" not in s and "disclaimer" not in paren(s, "2041-03-25")
    row = lapse_wording.term_end_row(served(rec), rec)
    assert row["date"] == "2041-03-25" and row["label"] == "Computed from the dates in the USPTO record"
    assert row["note"] == "This date leaves out a terminal disclaimer on record that has no date; that disclaimer can only make the term end earlier."
    # US 12744489: the same with 158 days of term adjustment and a 2014-01-28 parent
    rec = rec_of(expiry, "12744489", filing_date="2019-06-20", grant_date="2026-09-22", terminal_disclaimer=True, pta_days=158,
                 parent_filing_date="2014-01-28")
    s = lapse_wording.plain(rec, "US12744489", V)
    assert rec["expiry_date_estimated"] == "2034-07-05"
    assert ("leaving out a terminal disclaimer it shows without a date, is 2034-07-05 (20 years from the filing of a parent application "
            "on 2014-01-28, plus 158 days of patent term adjustment), a date not yet passed; that disclaimer can only make the term end earlier") in s
    assert lapse_wording.basis_text(rec["basis"], rec) == (
        "20 years from the filing of a parent application on 2014-01-28, plus 158 days of patent term adjustment, a terminal disclaimer "
        "on record without a date, which can only make the term end earlier than the date computed from the other dates")


def test_term_adjustment_alone_is_unchanged(expiry):
    # the canvas's US 12514139, in the reviewed words (the data of 2026-10-27)
    rec = rec_of(expiry, "12514139", filing_date="2022-05-31", grant_date="2026-01-06", pta_days=470, as_of="2026-10-27")
    assert lapse_wording.plain(rec, "US12514139", "20261027.2") == (
        "As of 2026-10-27, the USPTO record for US 12514139 shows no unresolved maintenance-fee lapse, and the term end computed from "
        "the dates in that record is 2043-09-13 (20 years from filing, plus 470 days of patent term adjustment), a date not yet passed "
        "(data version 20261027.2).")
    row = lapse_wording.term_end_row(served(rec), rec)
    assert row == {"date": "2043-09-13", "label": "Computed from the dates in the USPTO record", "note": "", "text": ""}


def test_a_dated_disclaimer_that_set_the_date(expiry):
    rec = rec_of(expiry, "X2", filing_date="2010-05-01", grant_date="2014-02-04", pta_days=100, terminal_disclaimer=True,
                 disclaimer_date="2028-01-15")
    assert rec["disclaimer_applied"] == "2028-01-15" and rec["expiry_date_estimated"] == "2028-01-15"
    s = lapse_wording.plain(rec, "USX2", V)
    assert paren(s, "2028-01-15") == ("the date a terminal disclaimer on record sets, no later than 20 years from filing, plus 100 days "
                                      "of patent term adjustment")
    assert "cut to this date" not in s
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == "A terminal disclaimer on record sets this date."
    assert lapse_wording.basis_text(rec["basis"], rec) == paren(s, "2028-01-15")


def test_a_dated_disclaimer_with_a_later_date_changes_nothing(expiry):
    cat = {"columns": [], "as_of": AS_OF}
    d = {"filing_date": "2012-01-10", "grant_date": "2014-05-06", "term_disclaimer_raw": TD, "disclaimer_date_raw": "2050-01-01"}
    rec = lapse_group.record_for(expiry, cat, "7777777", d)
    assert rec["status"] == "active" and rec["expiry_date_estimated"] == "2032-01-10" and rec["disclaimer_applied"] is None
    assert rec["disclaimer_dated"] == "2050-01-01" and not lapse_wording.undated_disclaimer(rec)
    s = lapse_wording.plain(rec, "US7777777", V)
    assert "disclaimer" not in s and "is 2032-01-10 (20 years from filing), a date not yet passed" in s
    assert lapse_wording.basis_text(rec["basis"], rec) == (
        "20 years from filing, a terminal disclaimer on record dated 2050-01-01, later than the date computed from the other dates")
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == ""


def test_a_fee_lapse_with_the_term_still_ahead(expiry):
    rec = rec_of(expiry, "8087108", filing_date="2007-05-25", grant_date="2012-01-03", fee_status="lapsed", lapse_date="2016-01-03",
                 pta_days=918)
    assert rec["status"] == "expired_fee_nonpayment" and rec["expiry_date_estimated"] > AS_OF
    s = lapse_wording.plain(rec, "US8087108", V)
    assert "term end" not in s and "entry dated 2016-01-03" in s
    row = lapse_wording.term_end_row(served(rec), rec)
    assert row["date"] == rec["expiry_date_estimated"]
    assert row["note"] == ("The maintenance-fee entry dated 2016-01-03 records an expiry for nonpayment; the date in this row is "
                           "computed without the maintenance-fee record.")
    assert lapse_wording.fee_lapse_row(served(rec)) == "2016-01-03"


def test_a_fee_lapse_before_a_term_that_has_since_passed(expiry):
    rec = rec_of(expiry, "5500000", filing_date="1995-07-01", grant_date="1998-01-06", fee_status="lapsed", lapse_date="2001-07-10")
    assert rec["status"] == "expired_fee_nonpayment" and rec["expiry_date_estimated"] < AS_OF
    assert "the lapse predates the term end" in rec["reinstatement_basis"]
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == (
        "The maintenance-fee entry dated 2001-07-10 records an expiry for nonpayment; the date in this row is computed without the "
        "maintenance-fee record.")


def test_a_fee_lapse_after_the_term_end(expiry):
    # the grace period ran out after the computed term end: the term ended first (independent audit 3, F1)
    rec = rec_of(expiry, "6000001", filing_date="1999-01-04", grant_date="2007-02-06", fee_status="lapsed", lapse_date="2019-03-11")
    assert rec["status"] == "expired_term" and rec["basis"][0].startswith("fee_lapse:")
    s = lapse_wording.plain(rec, "US6000001", V)
    assert "a date that has passed" in s and "nonpayment" not in s
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == ""


def test_a_reinstatement(expiry):
    rec = rec_of(expiry, "8800000", filing_date="2012-05-01", grant_date="2014-09-16", fee_status="lapsed", lapse_date="2018-10-01",
                 reinstated_date="2019-10-01", pta_days=12)
    assert rec["status"] == "active" and rec["reinstated_date"] == "2019-10-01"
    s = lapse_wording.plain(rec, "US8800000", V)
    assert "(a reinstatement dated 2019-10-01 ended an earlier lapse)" in s and "a date not yet passed" in s
    assert lapse_wording.fee_lapse_row(served(rec)) == "2018-10-01, ended by a reinstatement dated 2019-10-01"
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == ""
    # an undated disclaimer with the reinstatement: both clauses, in their places
    rec = rec_of(expiry, "8800001", filing_date="2012-05-01", grant_date="2014-09-16", fee_status="lapsed", lapse_date="2018-10-01",
                 reinstated_date="2019-10-01", terminal_disclaimer=True)
    s = lapse_wording.plain(rec, "US8800001", V)
    assert ("shows no unresolved maintenance-fee lapse (a reinstatement dated 2019-10-01 ended an earlier lapse), and the term end computed "
            "from the dates in that record, leaving out a terminal disclaimer it shows without a date, is 2032-05-01 (20 years from filing)") in s


def test_an_undated_disclaimer_on_a_term_that_has_passed(expiry):
    rec = rec_of(expiry, "11086507", filing_date="2020-07-21", grant_date="2021-08-10", terminal_disclaimer=True,
                 parent_filing_date="2005-12-23")
    s = lapse_wording.plain(rec, "US11086507", V)
    assert rec["status"] == "expired_term" and "disclaimer" not in s and "a date that has passed" in s
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == TERM_NOTE_UNDATED


TERM_NOTE_UNDATED = lapse_wording.TERM_END_NOTES["undated_disclaimer"]
UNUSED = ", which this computation does not use"


def test_a_dated_disclaimer_on_or_before_the_grant_date(expiry):
    # the audit of 2026-11-11 (W1): US D366608, a disclaimer dated 1995-08-21 and the grant 1996-01-30. The engine leaves out a
    # disclaimer dated on or before the grant, so its date is not later than the computed date and the row must not say so
    cat = {"columns": [], "as_of": AS_OF}
    d = {"filing_date": "1994-03-14", "grant_date": "1996-01-30", "term_disclaimer_raw": TD, "disclaimer_date_raw": "1995-08-21"}
    rec = lapse_group.record_for(expiry, cat, "D366608", d)
    assert rec["status"] == "expired_term" and rec["expiry_date_estimated"] == "2010-01-30" and rec["disclaimer_applied"] is None
    assert rec["disclaimer_dated"] == "1995-08-21" and not lapse_wording.disclaimer_later(rec)
    assert lapse_wording.basis_text(rec["basis"], rec) == "14 years from grant, a terminal disclaimer on record dated 1995-08-21" + UNUSED
    row = lapse_wording.term_end_row(served(rec), rec)
    assert row["note"] == "This date does not use a terminal disclaimer on record dated 1995-08-21."
    assert row["label"] == "Computed from the dates in the USPTO record"
    s = lapse_wording.plain(rec, "USD366608", V)
    assert "later than" not in s and "disclaimer" not in s and "a date that has passed" in s
    # a utility patent whose disclaimer is dated on its grant date: the same
    d = {"filing_date": "2010-05-01", "grant_date": "2014-02-04", "term_disclaimer_raw": TD, "disclaimer_date_raw": "2014-02-04"}
    rec = lapse_group.record_for(expiry, cat, "7777778", d)
    assert rec["expiry_date_estimated"] == "2030-05-01" and rec["disclaimer_applied"] is None and not lapse_wording.disclaimer_later(rec)
    assert lapse_wording.basis_text(rec["basis"], rec) == "20 years from filing, a terminal disclaimer on record dated 2014-02-04" + UNUSED
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == "This date does not use a terminal disclaimer on record dated 2014-02-04."


def test_a_dated_disclaimer_with_no_grant_date(expiry):
    # no grant date: the engine cannot place the disclaimer against the grant and leaves it out, whatever its date
    cat = {"columns": [], "as_of": AS_OF}
    d = {"filing_date": "2015-04-01", "term_disclaimer_raw": TD, "disclaimer_date_raw": "2030-01-01"}
    rec = lapse_group.record_for(expiry, cat, "7777779", d)
    assert rec["status"] == "active" and rec["expiry_date_estimated"] == "2035-04-01" and rec["disclaimer_applied"] is None
    assert rec["disclaimer_dated"] == "2030-01-01" and not lapse_wording.disclaimer_later(rec)
    assert lapse_wording.basis_text(rec["basis"], rec) == "20 years from filing, a terminal disclaimer on record dated 2030-01-01" + UNUSED
    assert lapse_wording.term_end_row(served(rec), rec)["note"] == "This date does not use a terminal disclaimer on record dated 2030-01-01."
    s = lapse_wording.plain(rec, "US7777779", V)
    assert "later than" not in s and "disclaimer" not in s and "is 2035-04-01 (20 years from filing), a date not yet passed" in s


def test_a_dated_disclaimer_with_no_computed_date(expiry):
    # no filing date for a utility patent: no term end is computed, so nothing is later than it
    cat = {"columns": [], "as_of": AS_OF}
    d = {"grant_date": "2012-05-01", "term_disclaimer_raw": TD, "disclaimer_date_raw": "2028-01-01"}
    rec = lapse_group.record_for(expiry, cat, "7777780", d)
    assert rec["expiry_date_estimated"] is None and rec["disclaimer_dated"] == "2028-01-01" and not lapse_wording.disclaimer_later(rec)
    assert lapse_wording.basis_text(rec["basis"], rec) == "a terminal disclaimer on record dated 2028-01-01" + UNUSED
    row = lapse_wording.term_end_row(served(rec), rec)
    assert row["date"] is None and row["note"] == "" and row["text"] == "not derivable from this record"
    assert "later than" not in lapse_wording.plain(rec, "US7777780", V)


def test_a_date_from_the_35_usc_156_list(expiry):
    # the audit of 2026-11-11 (W2): the date is the list's, so the label names the list, not the record's dates
    pte = {"id": "US7205302", "original_expiry": "2023-04-04", "period": "1306 days", "extension_days": 1306, "extended_expiry": "2026-10-31"}
    rec = rec_of(expiry, "7205302", filing_date="2001-01-10", grant_date="2007-04-17", pte=pte, as_of="2026-10-06")
    assert rec["status"] == "active" and rec["expiry_date_estimated"] == "2026-10-31" and rec["pte_156"]
    row = lapse_wording.term_end_row(served(rec), rec)
    assert row == {"date": "2026-10-31", "label": "From the dates on the USPTO list of patent terms extended under 35 USC 156",
                   "note": "", "text": ""}
    assert "35 USC 156" in lapse_wording.plain(rec, "US7205302", "20261006.1")
    # the same patent without the list: the record's own label
    rec = rec_of(expiry, "7205302", filing_date="2001-01-10", grant_date="2007-04-17", as_of="2026-10-06")
    assert lapse_wording.term_end_row(served(rec), rec)["label"] == "Computed from the dates in the USPTO record"


def test_no_combination_contradicts_itself(expiry):
    """Every combination of a disclaimer (none, undated, dated and setting the date, dated later), term adjustment, a parent
    date, a fee lapse (none, before the term end, after it), a reinstatement and two as-of dates: the date's parenthesis names a
    disclaimer only when the disclaimer set the date; "not yet passed" and "has passed" agree with the date; an active sentence
    with an undated disclaimer says the date leaves it out; the term-end row carries a note wherever the status is a fee lapse.
    After the audit of 2026-11-11: a disclaimer dated before the grant (dated_early) too, and the Basis row says "later than"
    only when the disclaimer's date is after the date shown."""
    n = 0
    for td, pta, parent, fee, rein, as_of in itertools.product(
            ("none", "undated", "dated_set", "dated_later", "dated_early"), (0, 300), (None, "2011-02-01"), ("none", "early", "late"),
            (False, True),
            ("2026-11-03", "2034-06-01")):
        kw = dict(filing_date="2013-03-01", grant_date="2015-06-02", pta_days=pta, parent_filing_date=parent, as_of=as_of)
        if td != "none":
            kw["terminal_disclaimer"] = True
        if td == "dated_set":
            kw["disclaimer_date"] = "2030-01-15"
        if td == "dated_later":
            kw["disclaimer_date"] = "2050-01-15"
        if td == "dated_early":
            kw["disclaimer_date"] = "2014-08-20"   # before the grant of 2015-06-02
        if fee != "none":
            kw.update(fee_status="lapsed", lapse_date="2019-06-03" if fee == "early" else "2034-03-01")
            if rein:
                kw["reinstated_date"] = "2020-01-06" if fee == "early" else "2034-05-01"
        rec = rec_of(expiry, "9000000", **kw)
        if td in ("dated_later", "dated_early"):
            assert rec["disclaimer_applied"] is None, (kw, rec)
            rec["disclaimer_dated"] = kw["disclaimer_date"]   # as lapse_group.record_for adds it
        s = lapse_wording.plain(rec, "US9000000", V)
        date = rec["expiry_date_estimated"]
        assert lapse_wording.check_banned(s) == [] and "can end the term earlier" not in s and "cut to this date" not in s
        if date and date in s:
            par = paren(s, date) or ""
            assert ("disclaimer" not in par) or par.startswith("the date a terminal disclaimer on record sets"), (kw, s)
            assert ("not yet passed" in s) == (date >= as_of), (kw, s)
        if rec["status"] == "active" and lapse_wording.undated_disclaimer(rec):
            assert "leaving out a terminal disclaimer it shows without a date" in s, (kw, s)
        row = lapse_wording.term_end_row(served(rec), rec)
        if rec["status"] == "expired_fee_nonpayment" and date:
            assert row["note"].startswith("The maintenance-fee entry dated"), (kw, row)
        gloss = lapse_wording.basis_text(rec["basis"], rec)
        later = bool(date and kw.get("disclaimer_date") and kw["disclaimer_date"] > date and not rec.get("disclaimer_applied"))
        assert ("later than the date computed" in gloss) == later, (kw, gloss)
        if td == "dated_early":
            assert gloss.endswith("a terminal disclaimer on record dated 2014-08-20" + UNUSED), (kw, gloss)
            if date and rec["status"] != "expired_fee_nonpayment":
                assert row["note"] == "This date does not use a terminal disclaimer on record dated 2014-08-20.", (kw, row)
        n += 1
    assert n == 5 * 2 * 2 * 3 * 2 * 2
