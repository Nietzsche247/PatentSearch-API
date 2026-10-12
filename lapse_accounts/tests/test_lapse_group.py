"""The `lapse` group on /api/v1/patent/ and PatentRef's own paths (checklist 2.9 and 2.12).

A four-row search database (patents, patents__application, patents__us_term_of_grant, fts_patents) and a
four-row Lapse catalog in the loader layout, attached read-only through LAPSE_CATALOG_PATH. The status engine
is `lapse.expiry` from a patentref checkout: LAPSE_REPO_DIR when set, else the first of a sibling checkout named
lapse, patentref or pr, or /srv/lapse (the box), that holds lapse/expiry.py; with none the tests fail and say so.
Reuses conftest.py (temporary Django DB, operator key via the account endpoints).

  python -m pytest lapse_accounts/tests/test_lapse_group.py -q -p no:cacheprovider
"""
import json
import os
import sqlite3
from pathlib import Path

import pytest
from django.conf import settings

from API import lapse_wording
from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "lapse_tools" / "schemas" / "check_patent_status.schema.json").read_text(encoding="utf-8"))


def find_repo_dir():
    """The patentref checkout whose lapse/expiry.py the status engine imports (API/lapse_group.py)."""
    if os.environ.get("LAPSE_REPO_DIR"):
        return os.environ["LAPSE_REPO_DIR"]
    for cand in (ROOT.parent / "lapse", ROOT.parent / "patentref", ROOT.parent / "pr", Path("/srv/lapse")):
        if (cand / "lapse" / "expiry.py").is_file():
            return str(cand)
    return None


REPO_DIR = find_repo_dir()

PATENTS = [
    # id, title, date, type, withdrawn, filing_date, term_extension, term_disclaimer, disclaimer_date
    ("10000364", "Resin composition for an optical lens", "2018-06-19", "utility", 0, "2015-03-20", "573", "", ""),
    ("6671888", "Method of drying a coated web", "2004-01-06", "utility", 0, "2002-04-01", "129", "", ""),
    ("8087108", "Pool cleaning head with a brush", "2012-01-03", "utility", 0, "2007-05-25", "918", "", ""),
    ("4890344", "Fastening device for a lamp", "1990-01-02", "utility", 0, "1989-01-31", "0", "", "2006-05-16"),
    # a continuation (the wording review of 2026-10-11, finding 1): its own filing 2020-07-21, the earliest parent 2005-12-23
    ("11086507", "Vehicle data processing", "2021-08-10", "utility", 0, "2020-07-21", "", "This patent is subject to a terminal disclaimer.", ""),
    # reissued as RE47739, which is on the USPTO 35 USC 156 list (review 2 of 2026-10-11): its sentence names the reissue
    ("7208489", "Pyrimidine derivatives", "2007-04-24", "utility", 0, "2005-01-28", "", "This patent is subject to a terminal disclaimer.", ""),
    # a parent's international application filed as related-publication, parent-pct-document (independent audit 3, F2)
    ("6459001", "Process for preparing a sulfonamide", "2002-10-01", "utility", 0, "2001-01-25", "", "", ""),
    # parent dates the record contradicts (independent audit 4, F3; bundle 5): a PCT parent dated in another year than its
    # number names, whose national stage gives the right date; a US parent dated 1920 whose granted patent's record gives
    # the right date; and a PCT parent dated 1914 that no other record dates
    ("9527819", "Implant delivery system", "2016-12-20", "utility", 0, "2014-12-03", "", "", ""),
    ("9087778", "Wireless sensor node", "2015-07-21", "utility", 0, "2014-04-11", "", "", ""),
    ("6419129", "Fiber optic connector", "2002-07-16", "utility", 0, "1997-07-07", "", "", ""),
]
# patents__pct_data pct_application rows of other patents (the parents' national stages)
PCT_DATA = [
    # _pid, _ord, published_filed_date, pct_102_date, pct_371_date, application_kind, pct_doc_number, pct_doc_type
    ("8367672", 0, "2005-11-02", None, "2008-07-22", "00", "PCT/US2005/039541", "pct_application"),
]
# patents__application rows of patents that are not fixture patents (a parent granted on 13/655,875)
APPLICATIONS = [("8746538", 0, "13655875", "2012-10-19")]
# patents__us_related_documents rows, as PatentsView and the weekly-grant parser write them: the term counts from the earliest
# continuation, division or continuation-in-part parent-doc; a parent's grant date, a child and a provisional never count
RELATED = [
    # _pid, _ord, related_doc_type, related_doc_kind, related_doc_number, related_doc_published_date
    ("11086507", 0, "continuation", "parent-doc", "16132213", "2018-09-14"),
    ("11086507", 1, "continuation", "parent-grant-document", "10078439", "2003-01-01"),
    ("11086507", 2, "continuation", "child-doc", "16132213", None),
    ("11086507", 3, "continuation", "parent-doc", "11322549", "2005-12-23"),
    ("11086507", 4, "us-provisional-application", None, "60123456", "2004-06-01"),
    ("11086507", 5, "continuation", "parent-doc", "12477075", "2009-06-02"),
    ("10000364", 0, "related-publication", None, "20160266243", "2016-09-15"),
    ("7208489", 0, "continuation", "parent-doc", "10345778", "2003-01-16"),
    # the reissue's own rows (RE47739 is not a fixture patent; the map reads its rows by _pid)
    ("RE47739", 0, "reissue", "child-doc", "16048143", None),
    ("RE47739", 1, "reissue", "parent-grant-document", "7208489", "2007-04-24"),
    ("RE47739", 2, "reissue", "parent-doc", "11046126", "2005-01-28"),
    ("6459001", 0, "division", "child-doc", "09769377", None),
    ("6459001", 1, "division", "parent-grant-document", "6221907", None),
    ("6459001", 2, "related-publication", "parent-pct-document", "PCT/FR97/00487", "1997-03-20"),
    ("6459001", 3, "related-publication", None, "WO97/36000", "1997-10-02"),
    ("9527819", 0, "division", "parent-pct-document", "PCT/US2005/039541", "2008-07-22"),
    ("9527819", 1, "division", "parent-doc", "13491986", "2012-06-08"),
    ("9087778", 0, "division", "parent-doc", "13655875", "1920-02-10"),
    ("9087778", 1, "division", "parent-grant-document", "8746538", None),
    ("6419129", 0, "related-publication", "parent-pct-document", "PCT/US94/07514", "1914-06-29"),
    ("6419129", 1, "continuation-in-part", "parent-doc", "08253143", "1994-06-02"),
]
CATALOG = [
    # id, filing, grant, fee_status, lapse_date, reinstated_date, prior, prior_pct, fwd_early, fwd_late, fwd_cites, fwd_recent
    ("US10000364", "2015-03-20", "2018-06-19", None, None, None, None, None, 3, 0, 3, 1),
    ("US6671888", "2002-04-01", "2004-01-06", None, None, "2017-08-30", 1.5, 88.2, 2, 0, 2, 0),
    ("US8087108", "2007-05-25", "2012-01-03", "lapsed", "2016-01-03", None, None, None, 1, 0, 1, 0),
    ("US4890344", "1989-01-31", "1990-01-02", None, None, None, None, None, 0, 0, 0, 0),
    ("US11086507", "2020-07-21", "2021-08-10", None, None, None, None, None, 0, 0, 0, 0),
    ("US7208489", "2005-01-28", "2007-04-24", None, None, None, None, None, 0, 0, 0, 0),
    ("US6459001", "2001-01-25", "2002-10-01", None, None, None, None, None, 0, 0, 0, 0),
    ("US9527819", "2014-12-03", "2016-12-20", None, None, None, None, None, 0, 0, 0, 0),
    ("US9087778", "2014-04-11", "2015-07-21", None, None, None, None, None, 0, 0, 0, 0),
    ("US6419129", "1997-07-07", "2002-07-16", None, None, None, None, None, 0, 0, 0, 0),
]


def build_search_db(path):
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.execute("INSERT INTO _lapse_build VALUES ('data_version', '20261006.2')")
    con.execute("INSERT INTO _lapse_build VALUES ('release', '20261006')")
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    for field, t, kw in [("patent_id", "keyword", 0), ("patent_zero_prefix", "keyword", 0), ("patent_title", "text", 1),
                         ("patent_date", "date", 0), ("patent_type", "keyword", 0), ("withdrawn", "boolean", 0)]:
        con.execute("INSERT INTO _lapse_fields VALUES ('patents','',?,?,?)", (field, t, kw))
    con.execute("INSERT INTO _lapse_fields VALUES ('patents','application','filing_date','date',0)")
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, patent_type TEXT, withdrawn INT, patent_abstract TEXT, gov_interest_statement TEXT)")
    con.execute('CREATE TABLE patents__application(_pid TEXT NOT NULL, _ord INTEGER, application_id TEXT, filing_date TEXT)')
    con.execute('CREATE TABLE patents__us_term_of_grant(_pid TEXT NOT NULL, _ord INTEGER, term_grant TEXT, term_extension TEXT, term_disclaimer TEXT, disclaimer_date TEXT)')
    con.execute('CREATE TABLE patents__us_related_documents(_pid TEXT NOT NULL, _ord INTEGER, related_doc_type TEXT, related_doc_kind TEXT, '
                'related_doc_number TEXT, published_country TEXT, related_doc_published_date TEXT, related_doc_status TEXT, '
                'related_doc_sequence INTEGER, wipo_kind TEXT)')
    con.executemany("INSERT INTO patents__us_related_documents VALUES (?,?,?,?,?,'US',?,NULL,?,NULL)",
                    [(pid, o, t, k, n, d, o) for pid, o, t, k, n, d in RELATED])
    con.execute('CREATE TABLE patents__pct_data(_pid TEXT NOT NULL, _ord INTEGER, published_filed_date TEXT, pct_102_date TEXT, '
                'pct_371_date TEXT, application_kind TEXT, pct_doc_number TEXT, pct_doc_type TEXT)')
    con.executemany("INSERT INTO patents__pct_data VALUES (?,?,?,?,?,?,?,?)", PCT_DATA)
    con.executemany("INSERT INTO patents__application VALUES (?,?,?,?)", APPLICATIONS)
    for pid, title, pdate, ptype, wd, fdate, ext, td, dd in PATENTS:
        con.execute("INSERT INTO patents VALUES (?,?,?,?,?,?,?,?)", (pid, pid, title, pdate, ptype, wd, title + " abstract text", None))
        con.execute("INSERT INTO patents__application VALUES (?,?,?,?)", (pid, 0, "a" + pid, fdate))
        con.execute("INSERT INTO patents__us_term_of_grant VALUES (?,?,?,?,?,?)", (pid, 0, "", ext, td, dd))
    con.execute("CREATE VIRTUAL TABLE fts_patents USING fts5(gov_interest_statement, patent_abstract, patent_title, content='patents', content_rowid='rowid', tokenize=\"unicode61 remove_diacritics 0 tokenchars '_'\")")
    con.execute("INSERT INTO fts_patents(fts_patents) VALUES ('rebuild')")
    con.commit()
    con.close()


def build_catalog(path, fee_rule=True):
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    cols = ("id TEXT NOT NULL, filing_date TEXT, grant_date TEXT, fee_status TEXT, lapse_date TEXT, prior REAL, prior_pct REAL, "
            "fwd_early INTEGER, fwd_late INTEGER, fwd_cites INTEGER, fwd_recent INTEGER, grant_year INTEGER")
    if fee_rule:
        cols += ", reinstated_date TEXT, pta_days INTEGER, term_disclaimer INTEGER, disclaimer_date TEXT"
    con.execute(f"CREATE TABLE catalog({cols})")
    con.execute("CREATE UNIQUE INDEX catalog_id ON catalog(id)")
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [("data_version", "20261006.2"), ("release", "20261006")]
                    + ([("fee_rule", "lapse:EXP.;reinstatement:EXPX,PMFG")] if fee_rule else []))
    for pid, f, g, fs, ld, rd, pr, pp, fe, fl, fc, fr in CATALOG:
        if fee_rule:
            con.execute("INSERT INTO catalog(id, filing_date, grant_date, fee_status, lapse_date, prior, prior_pct, fwd_early, fwd_late, fwd_cites, fwd_recent, grant_year, reinstated_date) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (pid, f, g, fs, ld, pr, pp, fe, fl, fc, fr, int(g[:4]), rd))
        else:  # the EXPX-only loader: the PMFG revival still reads lapsed, no reinstated_date column
            fs2, ld2 = ("lapsed", "2016-01-29") if pid == "US6671888" else (fs, ld)
            con.execute("INSERT INTO catalog(id, filing_date, grant_date, fee_status, lapse_date, prior, prior_pct, fwd_early, fwd_late, fwd_cites, fwd_recent, grant_year) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (pid, f, g, fs2, ld2, pr, pp, fe, fl, fc, fr, int(g[:4])))
    con.commit()
    con.close()


@pytest.fixture
def lapse_env():
    search = str(_TMP / "lapse_search.db")
    catalog = str(_TMP / "lapse_catalog.db")
    build_search_db(search)
    build_catalog(catalog)
    old = dict(settings.LAPSE_SQLITE)
    old_cat = getattr(settings, "LAPSE_CATALOG_PATH", None)
    settings.LAPSE_SQLITE["path"] = search
    settings.LAPSE_CATALOG_PATH = catalog
    if not REPO_DIR or not (Path(REPO_DIR) / "lapse" / "expiry.py").is_file():
        pytest.fail("the status engine lapse.expiry lives in the patentref repo: set LAPSE_REPO_DIR to a patentref "
                    f"checkout (found none beside {ROOT} or at /srv/lapse)", pytrace=False)
    settings.LAPSE_REPO_DIR = REPO_DIR
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    try:
        yield {"search": search, "catalog": catalog}
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        settings.LAPSE_CATALOG_PATH = old_cat
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()


def _patent(client, key, q, f=None, s=None):
    params = {"q": json.dumps(q)}
    if f is not None:
        params["f"] = json.dumps(f)
    if s is not None:
        params["s"] = json.dumps(s)
    return client.get("/api/v1/patent/", params, HTTP_X_API_KEY=key)


# ------------------------------------------------------------------ 2.9

def test_default_f_has_no_lapse_key_and_the_group_appears_only_when_named(client, user_key, lapse_env):
    _, key, _ = user_key
    r = _patent(client, key, {"patent_id": "6671888"})
    assert r.status_code == 200
    row = r.json()["patents"][0]
    assert set(row) == {"patent_id", "patent_title", "patent_date"}
    r = _patent(client, key, {"patent_id": "6671888"}, ["patent_id", "patent_title", "application", "lapse"])
    assert r.status_code == 200, r.content
    row = r.json()["patents"][0]
    assert set(row) == {"patent_id", "patent_title", "application", "lapse"}
    assert len(row["lapse"]) == 1
    g = row["lapse"][0]
    assert list(g) == ["expiry_status", "expiry_date_estimated", "expiry_basis", "reinstatement_possible", "fee_status", "lapse_date",
                       "reinstated_date", "prior", "prior_pct", "fwd_early", "fwd_late", "sleeper_flag", "as_of", "data_version"]
    # revived by PMFG, term plus 129 PTA days ended 2022-08-08: expired_term with reinstated: in the basis
    assert g["expiry_status"] == "expired_term" and g["expiry_date_estimated"] == "2022-08-08"
    assert g["expiry_basis"] == ["term_20y", "pta_days:129", "reinstated:2017-08-30"]
    assert g["reinstatement_possible"] is False and g["fee_status"] is None and g["reinstated_date"] == "2017-08-30"
    assert g["prior"] == 1.5 and g["prior_pct"] == 88.2 and g["fwd_early"] == 2 and g["fwd_late"] == 0
    assert g["sleeper_flag"] is True and g["as_of"] == "2026-10-06" and g["data_version"] == "20261006.2"
    assert "confidence" not in json.dumps(row) and "too_good" not in json.dumps(row)


def test_group_values_per_status(client, user_key, lapse_env):
    _, key, _ = user_key
    r = _patent(client, key, {"_or": [{"patent_id": "10000364"}, {"patent_id": "8087108"}, {"patent_id": "4890344"}]}, ["patent_id", "lapse"])
    assert r.status_code == 200, r.content
    by = {p["patent_id"]: p["lapse"][0] for p in r.json()["patents"]}
    a = by["10000364"]
    assert a["expiry_status"] == "active" and a["expiry_basis"] == ["term_20y", "pta_days:573"] and a["expiry_date_estimated"] == "2036-10-13"
    assert a["reinstatement_possible"] is False and a["sleeper_flag"] is False
    f = by["8087108"]
    assert f["expiry_status"] == "expired_fee_nonpayment" and f["expiry_basis"][0] == "fee_lapse:2016-01-03"
    assert f["reinstatement_possible"] == "unknown" and f["lapse_date"] == "2016-01-03" and f["sleeper_flag"] is True
    t = by["4890344"]
    assert t["expiry_status"] == "expired_term" and "terminal_disclaimer" in t["expiry_basis"] and t["expiry_date_estimated"] == "2006-05-16"


def test_subfield_selection_and_the_group_on_the_detail_route(client, user_key, lapse_env):
    _, key, _ = user_key
    r = _patent(client, key, {"patent_id": "8087108"}, ["patent_id", "lapse.expiry_status", "lapse.lapse_date"])
    assert r.status_code == 200, r.content
    row = r.json()["patents"][0]
    assert row["lapse"] == [{"expiry_status": "expired_fee_nonpayment", "lapse_date": "2016-01-03"}]
    r = client.get("/api/v1/patent/8087108/", {"f": json.dumps(["patent_id", "lapse"])}, HTTP_X_API_KEY=key)
    assert r.status_code == 200, r.content
    assert r.json()["patents"][0]["lapse"][0]["expiry_status"] == "expired_fee_nonpayment"


def test_q_and_s_on_the_group_answer_400_err_q(client, user_key, lapse_env):
    _, key, _ = user_key
    r = _patent(client, key, {"lapse.expiry_status": "active"}, ["patent_id"])
    assert r.status_code == 400 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_Q" and "lapse" in r["X-Status-Reason"]
    r = _patent(client, key, {"patent_id": "8087108"}, ["patent_id"], [{"lapse": "asc"}])
    assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q"


def test_contract_bodies_are_unchanged_without_a_catalog(client, user_key, lapse_env):
    """No catalog configured (or the file gone): v1 bodies are the same and the group is simply absent."""
    _, key, _ = user_key
    settings.LAPSE_CATALOG_PATH = str(_TMP / "no_such_catalog.db")
    LapseSQLiteSearch._local.__dict__.clear()
    r = _patent(client, key, {"patent_id": "8087108"})
    assert r.status_code == 200 and set(r.json()["patents"][0]) == {"patent_id", "patent_title", "patent_date"}
    r = _patent(client, key, {"patent_id": "8087108"}, ["patent_id", "lapse"])
    assert r.status_code == 200 and "lapse" not in r.json()["patents"][0]


def test_legacy_catalog_shape_is_served_too(client, user_key, lapse_env):
    """The EXPX-only catalog (no reinstated_date column, no fee_rule stamp): the group still serves; the PMFG
    revival reads as its catalog says until the next build (documented in the 1.7 handoff)."""
    _, key, _ = user_key
    build_catalog(lapse_env["catalog"], fee_rule=False)
    LapseSQLiteSearch._local.__dict__.clear()
    r = _patent(client, key, {"patent_id": "6671888"}, ["patent_id", "lapse"])
    assert r.status_code == 200, r.content
    g = r.json()["patents"][0]["lapse"][0]
    assert g["expiry_status"] == "expired_fee_nonpayment" and g["lapse_date"] == "2016-01-29" and g["reinstated_date"] is None


# ------------------------------------------------------------------ 2.12

def test_status_endpoint_needs_a_key_and_validates_against_the_schema(client, user_key, lapse_env):
    jsonschema = pytest.importorskip("jsonschema")

    _, key, _ = user_key
    r = client.get("/api/v1/lapse/status/8087108/")
    assert r.status_code == 403
    for path in ("/api/v1/lapse/status/8087108/", "/api/v1/status/8087108/", "/api/v1/lapse/status/US8087108"):
        r = client.get(path, HTTP_X_API_KEY=key)
        assert r.status_code == 200, r.content
        body = r.json()
        jsonschema.validate(body, SCHEMA)
        assert body["status"] == "expired_fee_nonpayment" and body["basis"][0] == "fee_lapse:2016-01-03"
        assert body["reinstatement_possible"] == "unknown" and body["reinstatement_basis"].startswith("37 CFR 1.378")
        assert body["as_of"] == "2026-10-06" and body["data_version"] == "20261006.2"
        assert body["permalink"].endswith("/p/8087108?v=20261006.2")
        assert body["plain"] == review_sentence("8087108")
        assert [n["audience"] for n in body["next_steps"]].count("human") == 1
        assert lapse_wording.check_banned(json.dumps(body)) == []
        assert r["X-Data-Version"] == "20261006.2"
    r = client.get("/api/v1/lapse/status/6671888/", HTTP_X_API_KEY=key)
    body = r.json()
    jsonschema.validate(body, SCHEMA)
    assert body["status"] == "expired_term" and body["basis"][-1] == "reinstated:2017-08-30" and body["reinstatement_possible"] is False
    # the term end is computed from the record's dates, not printed by it (2026-10-11 audit 2, finding G2); the
    # reinstatement stays out of the term parenthesis (wording review 2026-10-11, finding 5)
    assert body["plain"] == review_sentence("6671888")
    assert "shows its term ended" not in body["plain"] and "reinstate" not in body["plain"]

    r = client.get("/api/v1/lapse/status/10000364/", HTTP_X_API_KEY=key)
    body = r.json()
    jsonschema.validate(body, SCHEMA)
    assert body["status"] == "active" and body["reinstatement_basis"] == "not lapsed"


def test_status_endpoint_errors_and_versions(client, user_key, lapse_env, tmp_path):
    _, key, _ = user_key
    assert client.get("/api/v1/lapse/status/99999999/", HTTP_X_API_KEY=key).status_code == 404
    r = client.get("/api/v1/lapse/status/not-an-id/", HTTP_X_API_KEY=key)
    assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q"
    # a version that never existed: 404; a superseded one: the record with a superseded object, never a 404
    r = client.get("/api/v1/lapse/status/8087108/", {"v": "19990101.1"}, HTTP_X_API_KEY=key)
    assert r.status_code == 404 and r["X-Status-Reason-Code"] == "NOT_FOUND"
    (tmp_path / "refresh_versions.json").write_text(json.dumps({"builds": [{"data_version": "20260929.1", "status": "READY"},
                                                                           {"data_version": "20261006.2", "status": "READY"}]}))
    old = settings.LAPSE_STATUS_PATHS
    settings.LAPSE_STATUS_PATHS = dict(old, lapse_data=str(tmp_path), api_dir=str(tmp_path))
    try:
        r = client.get("/api/v1/lapse/status/8087108/", {"v": "20260929.1"}, HTTP_X_API_KEY=key)
        assert r.status_code == 200, r.content
        body = r.json()
        assert body["superseded"] == {"requested": "20260929.1", "current": "20261006.2",
                                      "note": "the requested data version has been superseded; this record is from the current version",
                                      "snapshot_archive": "https://patentref.io/LICENSES.md"}
        r = client.get("/api/v1/lapse/status/8087108/", {"v": "20261006.2"}, HTTP_X_API_KEY=key)
        assert "superseded" not in r.json()
        page = client.get("/p/8087108", {"v": "20260929.1"})
        assert page.status_code == 200 and "has been superseded" in page.content.decode()
    finally:
        settings.LAPSE_STATUS_PATHS = old


def test_record_page_without_a_key(client, lapse_env):
    r = client.get("/p/8087108")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/html")
    html = r.content.decode()
    assert review_sentence("8087108") in html
    assert "37 CFR 1.378" in html  # the petition line shows for a fee lapse only (wording review 2026-10-11, section 4)
    # a continuation: the term counts from the earliest parent-doc (2005-12-23), not the parent's grant date (2003-01-01),
    # the provisional (2004-06-01) or its own filing (2020-07-21); wording review 2026-10-11, finding 1 (the record page
    # needs no key, so the Free key's monthly cap of the test settings is not spent)
    import html as html_mod

    page = html_mod.unescape(client.get("/p/11086507").content.decode())
    assert review_sentence("11086507") in page
    assert '"status": "expired_term"' in page and '"expiry_date_estimated": "2025-12-23"' in page
    assert '"basis": [\n  "term_20y",\n  "terminal_disclaimer"\n ]' in page
    assert "term_from_parent" not in page and "disclaimer_applied" not in page and "pte_156\"" not in page  # wording-only keys stay out
    assert "37 CFR 1.378" not in page  # the petition line shows for a fee lapse only (wording review 2026-10-11, section 4)
    assert "20 years from the filing of a parent application on 2005-12-23" in page  # the Basis row names the same origin
    assert 'data-user-content="true"' in html and "Pool cleaning head" in html
    assert lapse_wording.check_banned(html.split('data-user-content="true"')[0]) == []
    assert client.get("/p/99999999").status_code == 404
    assert client.get("/p/8087108", {"v": "19990101.1"}).status_code == 404


def test_similar_endpoint(client, user_key, lapse_env):
    _, key, _ = user_key
    assert client.get("/api/v1/lapse/similar", {"text": "pool brush"}).status_code == 403
    r = client.get("/api/v1/lapse/similar", {"text": "a cleaning head with a brush for a pool", "n": 3}, HTTP_X_API_KEY=key)
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["error"] is False and body["method"] == "fts5_bm25_title_abstract" and body["n"] == 3
    assert body["data_version"] == "20261006.2" and body["as_of"] == "2026-10-06" and isinstance(body["terms"], list)
    assert body["neighbors"][0]["patent_id"] == "8087108" and body["neighbors"][0]["rank"] == 1
    assert set(body["neighbors"][0]) == {"rank", "patent_id", "patent_title", "patent_date", "score"}
    assert body["neighbors"][0]["score"] > 0
    r2 = client.get("/api/v1/lapse/similar", {"text": "a cleaning head with a brush for a pool", "n": 3}, HTTP_X_API_KEY=key)
    assert r2.json()["neighbors"] == body["neighbors"]  # deterministic
    r = client.get("/api/v1/lapse/similar", {"text": ""}, HTTP_X_API_KEY=key)
    assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q"
    r = client.get("/api/v1/lapse/similar", {"text": "x", "n": 21}, HTTP_X_API_KEY=key)
    assert r.status_code == 400
    r = client.get("/api/v1/lapse/similar", {"text": "x" * 4001}, HTTP_X_API_KEY=key)
    assert r.status_code == 400


# The sentences of the two fresh-agent wording reviews of 2026-10-11, verbatim, as_of 2026-10-06 and data version
# 20261006.5: the first review's section 3 (patentref ops/reviews/2026-10-11_lapse-sentence-templates_review.md) with the
# second review's changes ("a date not yet passed", the expired_term clause on the 35 USC 156 list), and the second
# review's "Rendered output" (ops/reviews/2026-10-11_lapse-sentence-templates_review-2.md). A changed word here reopens
# the reviews (rule 5).
REVIEW_SENTENCES = {
    "10000000": "As of 2026-10-06, the USPTO record for US 10000000 shows no unresolved maintenance-fee lapse, and the term end computed from the dates in that record is 2036-05-13 (20 years from filing, plus 430 days of patent term adjustment), a date not yet passed (data version 20261006.5).",
    "5000000": "As of 2026-10-06, the term end computed from the dates in the USPTO record for US 5000000 is 2009-05-15 (20 years from filing), a date that has passed, and the USPTO list of patent terms extended under 35 USC 156, updated through 2026-09-08, does not name this patent (data version 20261006.5).",
    "6671888": "As of 2026-10-06, the term end computed from the dates in the USPTO record for US 6671888 is 2022-08-08 (20 years from filing, plus 129 days of patent term adjustment), a date that has passed, and the USPTO list of patent terms extended under 35 USC 156, updated through 2026-09-08, does not name this patent (data version 20261006.5).",
    "8087108": "As of 2026-10-06, the USPTO maintenance-fee record for US 8087108 has an entry dated 2016-01-03 recording that the patent expired for nonpayment of a maintenance fee, and no reinstatement after it (data version 20261006.5).",
    "10000953": "As of 2026-10-06, the USPTO record shows US 10000953 as withdrawn from issue (data version 20261006.5).",
    "RE31177": "As of 2026-10-06, the USPTO record for US RE31177 does not settle its status: no term end can be computed from the dates it holds (data version 20261006.5).",
    "11086507": "As of 2026-10-06, the term end computed from the dates in the USPTO record for US 11086507 is 2025-12-23 (20 years from the filing of a parent application on 2005-12-23), a date that has passed, and the USPTO list of patent terms extended under 35 USC 156, updated through 2026-09-08, does not name this patent (data version 20261006.5).",
    "6936612": "As of 2026-10-06, the term end computed from the dates in the USPTO record for US 6936612 is 2023-01-22 (20 years from filing, plus 6 days of patent term adjustment), a date that has passed, and the USPTO list of patent terms extended under 35 USC 156, updated through 2026-09-08, does not name this patent (data version 20261006.5).",
    "RE47739": "As of 2026-10-06, the USPTO record for US RE47739 shows no unresolved maintenance-fee lapse, and the USPTO list of patent terms extended under 35 USC 156 gives it an original expiration date of 2023-01-16 and an extension of 1509 days, which puts its term end at 2027-03-05, a date not yet passed (data version 20261006.5).",
    "7205302": "As of 2026-10-06, the USPTO record for US 7205302 shows no unresolved maintenance-fee lapse, and the USPTO list of patent terms extended under 35 USC 156 gives it an original expiration date of 2023-04-04 and an extension of 1306 days, which puts its term end at 2026-10-31, a date not yet passed (data version 20261006.5).",
    "6500829": "As of 2026-10-06, the USPTO list of patent terms extended under 35 USC 156 gives US 6500829 an original expiration date of 2019-12-31 and an extension of 797 days, which puts its term end at 2022-03-07, a date that has passed (data version 20261006.5).",
    "8158616": "As of 2026-10-06, the USPTO sources for US 8158616 do not settle its status: the USPTO list of patent terms extended under 35 USC 156 gives it an original expiration date of 2023-06-08 and an extension of 723 days, which puts its term end at 2025-05-31, a date that has passed, while the term end computed from the dates in the USPTO record, before any extension, is 2030-06-08, a date not yet passed (data version 20261006.5).",
    "4338317": "As of 2026-10-06, the USPTO list of patent terms extended under 35 USC 156 gives US 4338317 an original expiration date of 1997-07-06 and an extension of 2 years, which puts its term end at 1999-07-06, a date that has passed; the term end computed from the dates in the USPTO record, before any extension, is later, 2001-03-16 (data version 20261006.5).",
    "7208489": "As of 2026-10-06, the term end computed from the dates in the USPTO record for US 7208489 is 2023-01-16 (20 years from the filing of a parent application on 2003-01-16), a date that has passed, but the USPTO record shows it reissued as US RE47739, a patent the USPTO list of patent terms extended under 35 USC 156, updated through 2026-09-08, names (data version 20261006.5).",
    "7515981": "As of 2026-10-06, the USPTO record for US 7515981 shows no unresolved maintenance-fee lapse, and the term end computed from the dates in that record is 2026-10-06 (20 years from filing), a date not yet passed (data version 20261006.5).",
    "X1": "As of 2026-10-06, the USPTO record for US X1 shows no unresolved maintenance-fee lapse, and the term end computed from the dates in that record is 2029-03-22 (20 years from the filing of a parent application on 2009-02-10, plus 40 days of patent term adjustment, not counting a terminal disclaimer on record, which can end the term earlier), a date not yet passed (data version 20261006.5).",
    "X2": "As of 2026-10-06, the USPTO record for US X2 shows no unresolved maintenance-fee lapse, and the term end computed from the dates in that record is 2028-01-15 (20 years from filing, plus 100 days of patent term adjustment, cut to this date by a terminal disclaimer), a date not yet passed (data version 20261006.5).",
    "X3": "As of 2026-10-06, the USPTO record for US X3 shows no unresolved maintenance-fee lapse (a reinstatement dated 2019-10-01 ended an earlier lapse), and the term end computed from the dates in that record is 2032-05-13 (20 years from filing, plus 12 days of patent term adjustment), a date not yet passed (data version 20261006.5).",
}
# the engine's inputs for each (the live snapshot's values the reviews used; X1 to X3 are the first review's made-up dates);
# the list rows come from the patentref checkout's data/pte156/ file
REVIEW_INPUTS = {
    "10000000": dict(filing_date="2015-03-10", grant_date="2018-06-19", pta_days=430),
    "5000000": dict(filing_date="1989-05-15", grant_date="1991-03-19"),
    "6671888": dict(filing_date="2002-04-01", grant_date="2004-01-06", reinstated_date="2017-08-30", pta_days=129),
    "8087108": dict(filing_date="2007-05-25", grant_date="2012-01-03", fee_status="lapsed", lapse_date="2016-01-03", pta_days=918),
    "10000953": dict(filing_date="2016-01-15", grant_date="2018-06-19", withdrawn=True),
    "RE31177": dict(filing_date="1982-04-12", grant_date="1983-03-08"),
    "11086507": dict(filing_date="2020-07-21", grant_date="2021-08-10", terminal_disclaimer=True, parent_filing_date="2005-12-23"),
    "6936612": dict(filing_date="2003-01-16", grant_date="2005-08-30", pta_days=6),
    "RE47739": dict(filing_date="2018-07-27", grant_date="2019-11-26", terminal_disclaimer=True),
    "7205302": dict(filing_date="2002-04-25", grant_date="2007-04-17", pta_days=344),
    "6500829": dict(filing_date="1995-04-18", grant_date="2002-12-31", parent_filing_date="1987-09-02"),
    "8158616": dict(filing_date="2009-03-10", grant_date="2012-04-17", pta_days=455),
    "4338317": dict(filing_date="1981-03-16", grant_date="1982-07-06"),
    "7208489": dict(filing_date="2005-01-28", grant_date="2007-04-24", terminal_disclaimer=True, parent_filing_date="2003-01-16"),
    "7515981": dict(filing_date="2006-10-06", grant_date="2009-04-07"),
    "X1": dict(filing_date="2012-03-01", grant_date="2015-06-02", pta_days=40, terminal_disclaimer=True, parent_filing_date="2009-02-10"),
    "X2": dict(filing_date="2010-05-01", grant_date="2014-02-04", pta_days=100, terminal_disclaimer=True, disclaimer_date="2028-01-15"),
    "X3": dict(filing_date="2012-05-01", grant_date="2014-09-16", reinstated_date="2019-10-01", pta_days=12),
}
# what the fork's lapse group adds to the engine record (API/lapse_group.listed_reissues): the listed reissue of a patent
REISSUED = {"7208489": "USRE47739"}


def review_sentence(pid, data_version="20261006.2"):
    """The review's sentence for a fixture patent, in the fixture's data version."""
    return REVIEW_SENTENCES[pid].replace("(data version 20261006.5)", f"(data version {data_version})")


def _engine():
    from API import lapse_group

    expiry = lapse_group.expiry_module()
    assert expiry is not None, "the status engine lapse.expiry is not importable (set LAPSE_REPO_DIR)"
    return expiry


def test_review_sentences_render_byte_for_byte(lapse_env):
    """Each sentence of the 2026-10-11 wording review, from the engine and the templates, character for character."""
    expiry = _engine()
    for pid, kw in REVIEW_INPUTS.items():
        rec = expiry.expiry_record(patent_id="US" + pid, as_of="2026-10-06", **kw)
        if pid in REISSUED:
            rec["pte_reissue"] = REISSUED[pid]
        got = lapse_wording.plain(rec, "US" + pid, "20261006.5")
        assert got == REVIEW_SENTENCES[pid], (pid, got)
        assert lapse_wording.check_banned(got) == []
    # the record page's Basis row glosses every code, the fee events and an undated disclaimer included
    rec = expiry.expiry_record(patent_id="US6671888", as_of="2026-10-06", **REVIEW_INPUTS["6671888"])
    assert lapse_wording.basis_text(rec["basis"], rec) == "20 years from filing, plus 129 days of patent term adjustment, reinstatement dated 2017-08-30"
    rec = expiry.expiry_record(patent_id="US11086507", as_of="2026-10-06", **REVIEW_INPUTS["11086507"])
    assert lapse_wording.basis_text(rec["basis"], rec) == ("20 years from the filing of a parent application on 2005-12-23, not counting a "
                                                           "terminal disclaimer on record, which can end the term earlier")
    rec = expiry.expiry_record(patent_id="US8087108", as_of="2026-10-06", **REVIEW_INPUTS["8087108"])
    assert lapse_wording.basis_text(rec["basis"], rec).startswith("fee-lapse entry dated 2016-01-03, 20 years from filing")
    # withdrawn from issue: no term at all (review section 4)
    rec = expiry.expiry_record(patent_id="US10000953", as_of="2026-10-06", **REVIEW_INPUTS["10000953"])
    assert rec["expiry_date_estimated"] is None and rec["basis"] == []
    # a listed patent's Basis row: the list's original date and period, and no undated-disclaimer clause (review 2)
    rec = expiry.expiry_record(patent_id="USRE47739", as_of="2026-10-06", **REVIEW_INPUTS["RE47739"])
    assert lapse_wording.basis_text(rec["basis"], rec) == ("on the USPTO list of patent terms extended under 35 USC 156: original "
                                                           "expiration date 2023-01-16 plus 1509 days")
    # the list and the record disagree on whether the term has passed: unknown, no date (US 8158616)
    rec = expiry.expiry_record(patent_id="US8158616", as_of="2026-10-06", **REVIEW_INPUTS["8158616"])
    assert rec["status"] == "unknown" and rec["expiry_date_estimated"] is None and rec["record_term_end"] == "2030-06-08"


def test_pte_156_list_wording(lapse_env):
    """A patent on the USPTO 35 USC 156 list: the list's original date and its own period text, and the date they add up
    to (review 2 of 2026-10-11); the expired_term sentence of a patent not on it names the list and its update date, and
    falls back to the first review's clause when no list file is loaded."""
    expiry = _engine()
    rec = expiry.expiry_record(patent_id="USRE47739", as_of="2028-01-03", **REVIEW_INPUTS["RE47739"])
    assert lapse_wording.plain(rec, "USRE47739", "20280101.1") == (
        "As of 2028-01-03, the USPTO list of patent terms extended under 35 USC 156 gives US RE47739 an original expiration date of "
        "2023-01-16 and an extension of 1509 days, which puts its term end at 2027-03-05, a date that has passed (data version 20280101.1).")
    # the period as the list prints it, years included (US 5607690: "5 years", 1,826 days)
    rec = expiry.expiry_record("1995-12-27", "1997-03-04", patent_id="US5607690", as_of="2026-10-06")
    assert rec["pte_156"]["period"] == "5 years" and "an extension of 5 years, which puts its term end at 2019-04-13" in (
        lapse_wording.plain(rec, "US5607690", "v"))
    rec = expiry.expiry_record(patent_id="US5000000", as_of="2026-10-06", **REVIEW_INPUTS["5000000"])
    assert lapse_wording.plain(dict(rec, pte_list_through=None), "US5000000", "20261006.5") == (
        "As of 2026-10-06, the term end computed from the dates in the USPTO record for US 5000000 is 2009-05-15 (20 years from "
        "filing), a date that has passed; it does not count any patent term extension for regulatory review (35 USC 156), which "
        "this record does not hold (data version 20261006.5).")
    rec = expiry.expiry_record(patent_id="USRE47739", as_of="2026-10-06", **REVIEW_INPUTS["RE47739"])
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(rec["basis"], SCHEMA["properties"]["basis"])


def test_listed_reissue_names_the_patent_it_reissued(client, lapse_env):
    """The fork maps each listed reissue back to the patent it reissued (RE47739 to 7208489, from the reissue's own
    parent-grant-document row) and the record page of that patent names the reissue; no answer carries the key."""
    import html as html_mod

    from API import lapse_group
    from API.search_sqlite import LapseSQLiteSearch

    searcher = LapseSQLiteSearch.from_django_settings()
    con = searcher.connection()
    assert lapse_group.expiry_module() is not None
    assert lapse_group.listed_reissues(con, searcher.catalog_facts()).get("7208489") == "USRE47739"
    page = html_mod.unescape(client.get("/p/7208489").content.decode())
    assert review_sentence("7208489") in page
    assert "pte_reissue" not in page and "record_term_end" not in page


def test_wording_templates_and_banned_check():
    assert lapse_wording.check_banned("This patent is free to use") == ["free to use"]
    assert lapse_wording.check_banned("record facts " + chr(0x2014) + " and more") == [chr(0x2014)]
    assert lapse_wording.check_banned('{"confidence": 0.9}') == ["confidence"]
    assert lapse_wording.check_banned("") == []
    unknown = REVIEW_SENTENCES["RE31177"]
    # a status that states a computed term end falls back to "does not settle" when the date is missing
    assert lapse_wording.plain({"status": "active", "as_of": "2026-10-06", "basis": []}, "USRE31177", "20261006.5") == unknown
    for t in (list(lapse_wording.TEMPLATES.values()) + list(lapse_wording.PTE_TEMPLATES.values())
              + [lapse_wording.EXPIRED_TERM_NO_LIST, lapse_wording.EXPIRED_TERM_REISSUED, lapse_wording.PTE_CONFLICT,
                 lapse_wording.UNKNOWN_PARENT_DATE]):
        assert lapse_wording.check_banned(t) == [] and "{as_of}" in t and "{v}" in t
        # finding G2: a term end is computed from the record's dates; no sentence may say the record shows it, and the 156
        # list prints an original date and a period, never a term end (review 2)
        assert "term ended" not in t and "shows its term" not in t and "gives its term end" not in t
        assert "still ahead" not in t
    for t in lapse_wording.TEMPLATES.values():
        if "{expiry_date}" in t:
            assert "the term end computed from the dates in" in t


def test_similar_term_selection_uses_document_frequencies(lapse_env):
    from API import lapse_views
    from API.search_sqlite import LapseSQLiteSearch

    con = LapseSQLiteSearch.from_django_settings().connection()
    assert con.execute("SELECT name FROM temp.sqlite_master WHERE name = 'fts_patents_vocab'").fetchone()
    # 'lens' is in one title, 'a' in several, 'zzz' in none: known tokens only, rarest first, at least three kept
    assert lapse_views.select_terms(con, ["a", "lens", "zzz", "web", "composition"]) == ["composition", "lens", "web"]
    assert lapse_views.select_terms(con, []) == []


def test_parent_pct_filed_as_related_publication_counts(lapse_env):
    """Independent audit 3 of 2026-10-12, F2: a parent's international application filed as related-publication,
    parent-pct-document is a date the term counts from (US 6459001: PCT/FR97/00487 filed 1997-03-20, so 2017-03-20, not
    20 years from its own 2001 filing); a related-publication row of another kind is not."""
    from API import lapse_group
    from API.search_sqlite import LapseSQLiteSearch

    searcher = LapseSQLiteSearch.from_django_settings()
    con = searcher.connection()
    cat = searcher.catalog_facts()
    expiry = lapse_group.expiry_module()
    d = lapse_group.fetch_rows(con, cat, ["6459001"])["6459001"]
    assert d["parent_filing_date"] == "1997-03-20"
    rec = lapse_group.record_for(expiry, cat, "6459001", d)
    assert rec["expiry_date_estimated"] == "2017-03-20" and rec["term_from_parent"] == "1997-03-20" and rec["status"] == "expired_term"
    assert lapse_group.fetch_rows(con, cat, ["10000364"])["10000364"].get("parent_filing_date") is None  # its related-publication row


def test_bundle4_wording_review_read_date_and_disputed_term_end(lapse_env):
    """The bundle 4 wording review of 2026-10-12 (patentref ops/reviews/2026-10-12_bundle-4_wording_review.md): a list date
    read by hand names the cell as printed (item 6, US 7511131), and while the 35 USC 156 list and the record disagree on
    whether the term has passed the record page's term-end row gives both dates (item 7, US 9580461 and the unknown
    patents of review 2); the engine gives no term end then, whatever the status (item 3)."""
    from django.template.loader import render_to_string

    expiry = _engine()
    rec = expiry.expiry_record("2003-11-13", "2009-03-31", "lapsed", "2021-05-03", pta_days=761, patent_id="US7511131", as_of="2026-10-06")
    assert rec["pte_156"]["printed"] == "12/13/22025"
    assert ("on the USPTO list of patent terms extended under 35 USC 156: original expiration date 2025-12-13 (printed on the "
            "list as 12/13/22025) plus 412 days") in lapse_wording.basis_text(rec["basis"], rec)
    assert lapse_wording.pte_original_text(rec["pte_156"]) == "2025-12-13 (printed on the list as 12/13/22025)"
    assert lapse_wording.pte_original_text({"original_expiry": "2023-01-16"}) == "2023-01-16"
    assert lapse_wording.term_end_text(rec) is None
    rec = expiry.expiry_record("2011-07-27", "2017-02-28", "lapsed", "2025-04-07", pta_days=252, patent_id="US9580461", as_of="2026-10-06")
    assert rec["status"] == "expired_fee_nonpayment" and rec["expiry_date_estimated"] is None and rec["reinstatement_possible"] == "unknown"
    text = lapse_wording.term_end_text(rec)
    assert text == "none: the dates on the USPTO 35 USC 156 list add up to 2023-08-12 and the record's own dates to 2032-04-04"
    assert lapse_wording.plain(rec, "US9580461", "20261006.5") == (
        "As of 2026-10-06, the USPTO maintenance-fee record for US 9580461 has an entry dated 2025-04-07 recording that the "
        "patent expired for nonpayment of a maintenance fee, and no reinstatement after it (data version 20261006.5).")
    page = render_to_string("lapse_accounts/record.html", {"rec": rec, "pid": "9580461", "term_end_text": text, "basis_text": ""})
    assert "<dt>Term end, computed from the record's dates</dt><dd>" + text.replace("'", "&#x27;") + "</dd>" in page
    assert "not derivable from this record" not in page
    rec = expiry.expiry_record(patent_id="US8158616", as_of="2026-10-06", **REVIEW_INPUTS["8158616"])
    assert rec["status"] == "unknown" and lapse_wording.term_end_text(rec) == (
        "none: the dates on the USPTO 35 USC 156 list add up to 2025-05-31 and the record's own dates to 2030-06-08")
    rec = expiry.expiry_record(patent_id="US5000000", as_of="2026-10-06", **REVIEW_INPUTS["5000000"])
    assert lapse_wording.term_end_text(rec) is None


def test_parent_dates_the_record_contradicts(client, lapse_env):
    """Independent audit 4 of 2026-10-12, finding F3 (bundle 5, B10; patentref lapse/parents.py): a parent date the record
    contradicts is never used. US 9527819: PCT/US2005/039541 dated 2008-07-22, and its national stage's record gives
    2005-11-02, so 2025-11-02 (it served active to 2028-07-22). US 9087778: 13/655,875 dated 1920-02-10, and the patent
    granted on it gives 2012-10-19, so 2032-10-19 (it served expired_term 1940-02-10). US 6419129: PCT/US94/07514 dated
    1914-06-29, no other record dates it, and it could be earlier than the 1994-06-02 parent: no term end, and since the
    term ends in 2014 on every 1994 day before that parent, expired_term (the coordinator's decision of 2026-10-12; the
    fixture has no fee lapse for it). A status that depends on the day stays unknown with UNKNOWN_PARENT_DATE."""
    import html as html_mod

    from API import lapse_group
    from API.search_sqlite import LapseSQLiteSearch

    searcher = LapseSQLiteSearch.from_django_settings()
    con = searcher.connection()
    cat = searcher.catalog_facts()
    expiry = lapse_group.expiry_module()
    rows = lapse_group.fetch_rows(con, cat, ["9527819", "9087778", "6419129", "11086507"])
    assert rows["9527819"]["parent_filing_date"] == "2005-11-02" and "parent_unsettled" not in rows["9527819"]
    assert rows["9087778"]["parent_filing_date"] == "2012-10-19"
    assert rows["11086507"]["parent_filing_date"] == "2005-12-23"  # the rule leaves good rows alone
    assert rows["6419129"].get("parent_filing_date") is None
    assert rows["6419129"]["parent_unsettled"] == {"number": "PCT/US94/07514", "date": "1914-06-29", "why": "pct_year",
                                                   "first": "1994-01-01", "last": "1994-12-31", "other": "1994-06-02",
                                                   "span": "number"}
    rec = lapse_group.record_for(expiry, cat, "9527819", rows["9527819"])
    assert (rec["status"], rec["expiry_date_estimated"], rec["term_from_parent"]) == ("expired_term", "2025-11-02", "2005-11-02")
    rec = lapse_group.record_for(expiry, cat, "9087778", rows["9087778"])
    assert (rec["status"], rec["expiry_date_estimated"]) == ("active", "2032-10-19")
    rec = lapse_group.record_for(expiry, cat, "6419129", rows["6419129"])
    assert (rec["status"], rec["expiry_date_estimated"], rec["basis"]) == ("expired_term", None, [])
    assert rec["parent_unsettled"]["settled"] is True
    sentence = lapse_wording.plain(rec, "US6419129", "20261006.2")
    assert sentence == (
        "As of 2026-10-06, the USPTO record for US 6419129 gives parent application PCT/US94/07514 the filing date 1914-06-29, "
        "which is not in the year that application's number names, and PatentRef holds no other USPTO record that gives that "
        "application's filing date, so no single term end can be computed, but on any filing date that application's number "
        "allows, the term end computed from the record's dates has passed, and the USPTO list of patent terms extended under "
        "35 USC 156, updated through 2026-09-08, does not name this patent (data version 20261006.2).")
    assert lapse_wording.check_banned(sentence) == []
    assert lapse_wording.term_end_text(rec) == ("none: the USPTO record gives parent application PCT/US94/07514 the filing "
                                                "date 1914-06-29, which is not in the year that application's number names")
    page = html_mod.unescape(client.get("/p/6419129").content.decode())
    assert sentence in page and lapse_wording.term_end_text(rec) in page and "parent_unsettled" not in page
    # the other two settled statuses, and a status that depends on the day
    act = dict(rec, status="active", reinstatement_possible=False)
    assert lapse_wording.plain(act, "US6419129", "v").endswith(
        "so no single term end can be computed, but on any filing date that application's number allows, the term end "
        "computed from the record's dates has not passed (data version v).")
    # the span kinds of the wording review: the row's own date joined, or any day from 1978-06-01
    both = dict(act, parent_unsettled=dict(rec["parent_unsettled"], span="number_and_row"))
    assert "but on that date and on any filing date that application's number allows, the term end" in \
        lapse_wording.plain(both, "US6419129", "v")
    opn = dict(act, parent_unsettled=dict(rec["parent_unsettled"], span="open", why="pct_short_year", number="PCT/EP11/06449"))
    assert ("PCT/EP11/06449 the filing date 1914-06-29, which cannot be checked against that application's number, since the "
            "number is written with a two-digit year, a form in use only up to 2003, and PatentRef holds") in \
        lapse_wording.plain(opn, "US6419129", "v")
    # a number that names no possible year keeps pct_no_year (US 6405983: PCT/AU76/00185)
    nop = dict(act, parent_unsettled=dict(rec["parent_unsettled"], span="open", why="pct_no_year", number="PCT/AU76/00185"))
    assert "which cannot be checked against that application's number, since the number names no possible filing year, " \
        "and PatentRef holds" in lapse_wording.plain(nop, "US6419129", "v")
    assert lapse_wording.check_banned(lapse_wording.PARENT_WHY["pct_short_year"]) == []
    assert "but on any day from 1978-06-01 to this patent's own filing, the term end" in lapse_wording.plain(opn, "US6419129", "v")
    fee = dict(rec, status="expired_fee_nonpayment", lapse_date="2010-07-16")
    assert lapse_wording.plain(fee, "US6419129", "v").startswith(
        "As of 2026-10-06, the USPTO maintenance-fee record for US 6419129 has an entry dated 2010-07-16 recording that the "
        "patent expired for nonpayment of a maintenance fee, and no reinstatement after it; the record gives parent application")
    unk = dict(rec, status="unknown", parent_unsettled=dict(rec["parent_unsettled"], settled=False))
    assert lapse_wording.plain(unk, "US6419129", "20261006.2") == (
        "As of 2026-10-06, the USPTO record for US 6419129 does not settle its status: it gives parent application "
        "PCT/US94/07514 the filing date 1914-06-29, which is not in the year that application's number names, and PatentRef "
        "holds no other USPTO record that gives that application's filing date (data version 20261006.2).")
    for tpl in list(lapse_wording.SETTLED_PARENT.values()) + [lapse_wording.SETTLED_PARENT_EXPIRED_NO_LIST]:
        assert lapse_wording.check_banned(tpl) == []
    # the US form of the clause, with the serial number as the USPTO writes it
    us = dict(rec, parent_unsettled={"number": "13655875", "date": "1920-02-10", "why": "us_series"})
    assert "parent application 13/655,875 the filing date 1920-02-10, which is outside the years in which applications " \
           "of that serial number's series were filed" in lapse_wording.plain(us, "US9087778", "v")
