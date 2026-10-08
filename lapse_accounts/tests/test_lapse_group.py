"""The `lapse` group on /api/v1/patent/ and PatentRef's own paths (checklist 2.9 and 2.12).

A four-row search database (patents, patents__application, patents__us_term_of_grant, fts_patents) and a
four-row Lapse catalog in the loader layout, attached read-only through LAPSE_CATALOG_PATH. The status engine
is `lapse.expiry` from the patentref clone (LAPSE_REPO_DIR; defaults to the sibling checkout for the tests).
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
os.environ.setdefault("LAPSE_REPO_DIR", str(ROOT.parent / "lapse"))

PATENTS = [
    # id, title, date, type, withdrawn, filing_date, term_extension, term_disclaimer, disclaimer_date
    ("10000364", "Resin composition for an optical lens", "2018-06-19", "utility", 0, "2015-03-20", "573", "", ""),
    ("6671888", "Method of drying a coated web", "2004-01-06", "utility", 0, "2002-04-01", "129", "", ""),
    ("8087108", "Pool cleaning head with a brush", "2012-01-03", "utility", 0, "2007-05-25", "918", "", ""),
    ("4890344", "Fastening device for a lamp", "1990-01-02", "utility", 0, "1989-01-31", "0", "", "2006-05-16"),
]
CATALOG = [
    # id, filing, grant, fee_status, lapse_date, reinstated_date, prior, prior_pct, fwd_early, fwd_late, fwd_cites, fwd_recent
    ("US10000364", "2015-03-20", "2018-06-19", None, None, None, None, None, 3, 0, 3, 1),
    ("US6671888", "2002-04-01", "2004-01-06", None, None, "2017-08-30", 1.5, 88.2, 2, 0, 2, 0),
    ("US8087108", "2007-05-25", "2012-01-03", "lapsed", "2016-01-03", None, None, None, 1, 0, 1, 0),
    ("US4890344", "1989-01-31", "1990-01-02", None, None, None, None, None, 0, 0, 0, 0),
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
    settings.LAPSE_REPO_DIR = os.environ["LAPSE_REPO_DIR"]
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
        assert body["plain"] == ("As of 2026-10-06, the USPTO record for US 8087108 shows it expired on 2016-01-03 for nonpayment of a "
                                 "maintenance fee; the record shows no reinstatement (data version 20261006.2).")
        assert [n["audience"] for n in body["next_steps"]].count("human") == 1
        assert lapse_wording.check_banned(json.dumps(body)) == []
        assert r["X-Data-Version"] == "20261006.2"
    r = client.get("/api/v1/lapse/status/6671888/", HTTP_X_API_KEY=key)
    body = r.json()
    jsonschema.validate(body, SCHEMA)
    assert body["status"] == "expired_term" and body["basis"][-1] == "reinstated:2017-08-30" and body["reinstatement_possible"] is False
    assert "term ended on 2022-08-08" in body["plain"] and "reinstated 2017-08-30" in body["plain"]
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
    assert "the USPTO record for US 8087108 shows it expired on 2016-01-03" in html
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


def test_wording_templates_and_banned_check():
    rec = {"status": "active", "as_of": "2026-10-06", "basis": ["term_20y"]}
    assert lapse_wording.plain(rec, "US10000364", "20261006.2") == \
        "As of 2026-10-06, the USPTO record for US 10000364 shows no term end and no unresolved maintenance-fee lapse (data version 20261006.2)."
    assert lapse_wording.check_banned("This patent is free to use") == ["free to use"]
    assert lapse_wording.check_banned("record facts " + chr(0x2014) + " and more") == [chr(0x2014)]
    assert lapse_wording.check_banned('{"confidence": 0.9}') == ["confidence"]
    assert lapse_wording.check_banned("") == []
    for t in lapse_wording.TEMPLATES.values():
        assert lapse_wording.check_banned(t) == [] and "{as_of}" in t and "{v}" in t


def test_similar_term_selection_uses_document_frequencies(lapse_env):
    from API import lapse_views
    from API.search_sqlite import LapseSQLiteSearch

    con = LapseSQLiteSearch.from_django_settings().connection()
    assert con.execute("SELECT name FROM temp.sqlite_master WHERE name = 'fts_patents_vocab'").fetchone()
    # 'lens' is in one title, 'a' in several, 'zzz' in none: known tokens only, rarest first, at least three kept
    assert lapse_views.select_terms(con, ["a", "lens", "zzz", "web", "composition"]) == ["composition", "lens", "web"]
    assert lapse_views.select_terms(con, []) == []
