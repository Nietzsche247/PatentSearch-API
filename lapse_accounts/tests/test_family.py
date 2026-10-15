"""The family block of the record page (PatentRef checklist 1.9): API/lapse_family.py reads the catalog's family tables
(written there by the patentref refresh build from the loader's staged file) and record.html shows the members, the
Global Dossier link and the Google credit line, in the block and in the footer."""
import sqlite3

from django.template.loader import render_to_string

from API import lapse_family, lapse_wording


def _con(tmp_path, with_tables=True):
    cat = tmp_path / "catalog.db"
    c = sqlite3.connect(str(cat))
    c.execute("CREATE TABLE catalog(id TEXT PRIMARY KEY)")
    if with_tables:
        c.executescript("""
            CREATE TABLE family_us(patent_id TEXT PRIMARY KEY, family_id TEXT, application_number TEXT, global_dossier_url TEXT, publication_number TEXT, kind TEXT);
            CREATE TABLE family_pub(family_id TEXT, office TEXT, number TEXT, kind TEXT, pub_date INTEGER, publication_number TEXT);
            CREATE TABLE family_meta(k TEXT PRIMARY KEY, v TEXT);
        """)
        c.executemany("INSERT INTO family_us VALUES (?,?,?,?,?,?)", [
            ("4310730", "F1", "US-6060492-A", "https://globaldossier.uspto.gov/result/application/US/06060492/1", "US-4310730-A", "A"),
            ("5000000", None, "US-7000000-A", "https://globaldossier.uspto.gov/result/application/US/07000000/1", "US-5000000-A", "A")])
        c.executemany("INSERT INTO family_pub VALUES (?,?,?,?,?,?)", [
            ("F1", "GB", "2050000", "A", 19810107, "GB-2050000-A"), ("F1", "DE", "3030000", "A1", 19810301, "DE-3030000-A1")])
        c.executemany("INSERT INTO family_meta VALUES (?,?)", [("credit", lapse_family.CREDIT), ("source", "patents-public-data.patents.publications"),
                                                               ("license", "CC-BY-4.0"), ("retrieved_at", "2026-10-15T19:40:00Z")])
    c.commit()
    c.close()
    con = sqlite3.connect(":memory:")
    con.execute("ATTACH DATABASE ? AS cat", (f"file:{cat}?mode=ro",))
    return con


def test_family_for(tmp_path):
    con = _con(tmp_path)
    f = lapse_family.family_for(con, "4310730")
    assert [m["office"] for m in f["members"]] == ["DE", "GB"] and f["members"][0] == {"office": "DE", "number": "3030000", "kind": "A1", "published": "1981-03-01"}
    assert f["global_dossier_url"].endswith("/US/06060492/1") and f["credit"] == lapse_family.CREDIT and f["retrieved_at"] == "2026-10-15"
    lone = lapse_family.family_for(con, "5000000")
    assert lone["members"] == [] and lone["global_dossier_url"]
    assert lapse_family.family_for(con, "9999999") is None
    bare = tmp_path / "bare"
    bare.mkdir()
    assert lapse_family.family_for(_con(bare, with_tables=False), "4310730") is None  # a catalog from before 1.9


def _page(family):
    rec = {"plain": "As of 2026-10-13, a sentence (data version 20261013.3).", "status": "expired_term", "basis": ["term_20y"],
           "permalink": "https://patentref.io/p/4310730?v=20261013.3", "as_of": "2026-10-13", "data_version": "20261013.3",
           "reinstatement_possible": False, "reinstatement_basis": "35 USC 154: term ended"}
    lines = ["Patent data from the USPTO.", lapse_family.CREDIT] if family else ["Patent data from the USPTO."]
    return render_to_string("lapse_accounts/record.html", {"rec": rec, "pid": "4310730", "title": None, "superseded": None,
                                                           "data_version": "20261013.3", "as_of": "2026-10-13", "footer_lines": lines,
                                                           "family": family, "basis_text": "", "term_end_text": "", "status_json": "{}"})


def test_record_page_shows_the_family_block_and_the_credit(tmp_path):
    html = _page(lapse_family.family_for(_con(tmp_path), "4310730"))
    assert '<h2 id="family">Family outside the US</h2>' in html and "<td>DE</td><td>3030000</td><td>A1</td><td>1981-03-01</td>" in html
    assert 'href="https://globaldossier.uspto.gov/result/application/US/06060492/1"' in html
    assert html.count(lapse_family.CREDIT) == 2  # in the block and in the footer
    assert "Their legal status is not shown here." in html
    assert not lapse_wording.check_banned(html)
    assert "Family outside the US" not in _page(None) and lapse_family.CREDIT not in _page(None)


def test_record_page_with_no_member_outside_the_us(tmp_path):
    html = _page(lapse_family.family_for(_con(tmp_path), "5000000"))
    assert "Google Patents Public Datasets lists no publication outside the US in this patent's family (read 2026-10-15)." in html
    assert "Global Dossier" in html and lapse_family.CREDIT in html
