"""Data files for the tests of the record page's claim links (gate S0.4, test_record_anchors.py) and of the patent brief
(gate S0.5, test_structure_brief.py): a search file in the Lapse layout, a status catalog in the loader layout and a
long-text file in build_text.py's layout (txt.g_claims with the claim text zlib-compressed, the (patent_id,
claim_sequence) index, _lapse_build.data_version), filled with the real claims of claimgraph's 37 fixtures
(lapse_accounts/tests/claim_fixtures, 1976 to 2025) and made-up titles and dates (by each fixture's era), plus one made-up patent
whose claim text holds markup characters. NO_TEXT is in the search file and the catalog without claims (a grant after the
long-text file's last grant date); UNKNOWN is in neither. The status engine is lapse.expiry from a patentref checkout
(test_lapse_group.REPO_DIR: LAPSE_REPO_DIR, a sibling checkout, or /srv/lapse on the box).
"""
import csv
import json
import os
import sqlite3
import uuid
import zlib
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

from API import lapse_brief, lapse_structure
from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.test_lapse_group import REPO_DIR

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "lapse_accounts" / "tests" / "claim_fixtures"
MANIFEST = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
DATA_VERSION = "20261027.2"
TEXT_VERSION = "text_test.1"
NO_TEXT = "12999999"     # in the search file and the catalog, no claims in the long-text file
UNKNOWN = "9999998"      # in neither file
MARKUP = "11111111"      # made up: claim text with markup characters, which the page must show as text
MARKUP_ROWS = [
    (MARKUP, "0", '1. A filter comprising: a <b>mesh</b> & a frame; and a "seal" <script>x()</script> on the frame.', "", "1", "1"),
    (MARKUP, "1", "2. The filter of claim 1, wherein the mesh is <i>steel</i>.", "claim 1", "2", "0"),
]
csv.field_size_limit(2 ** 31 - 1)


def fixture_rows(pid):
    if pid == MARKUP:
        return [list(r) for r in MARKUP_ROWS]
    with (FIXTURES / MANIFEST[pid]["file"]).open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    assert rows[0] == ["patent_id", "claim_sequence", "claim_text", "dependent", "claim_number", "exemplary"]
    return rows[1:]


def patent_ids():
    return sorted(MANIFEST) + [MARKUP]


def dates(pid):
    """(filing, grant): made up from the fixture's era ("1970s" gives a 1975 grant), at most 2025."""
    era = (MANIFEST.get(pid) or {}).get("era") or "2020s"
    year = min(int(era[:4]) + 5, 2025) if era[:4].isdigit() else 2025
    return f"{year - 2}-03-10", f"{year}-06-17"


def title(pid):
    return f"Fixture patent {pid}" if pid != MARKUP else 'A filter & a "seal" <b>made up</b>'


def ptype(pid):
    p = pid.upper()
    return "design" if p.startswith("D") else "plant" if p.startswith("PP") else "reissue" if p.startswith("RE") else "utility"


def build_files(search, catalog, text):
    for p in (search, catalog, text):
        if os.path.exists(p):
            os.unlink(p)
    ids = patent_ids() + [NO_TEXT]
    con = sqlite3.connect(search)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", DATA_VERSION), ("release", "20261027")])
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, "
                "patent_type TEXT, withdrawn INT)")
    con.execute("CREATE TABLE patents__application(_pid TEXT NOT NULL, _ord INTEGER, application_id TEXT, filing_date TEXT)")
    con.execute("CREATE INDEX ix_app ON patents__application(_pid)")
    con.execute("CREATE TABLE patents__us_term_of_grant(_pid TEXT NOT NULL, _ord INTEGER, term_grant TEXT, term_extension TEXT, "
                "term_disclaimer TEXT, disclaimer_date TEXT)")
    for pid in ids:
        filing, grant = dates(pid) if pid != NO_TEXT else ("2024-01-02", "2026-01-06")
        con.execute("INSERT INTO patents VALUES (?,?,?,?,?,0)", (pid, pid, title(pid), grant, ptype(pid)))
        con.execute("INSERT INTO patents__application VALUES (?,0,?,?)", (pid, "a" + pid, filing))
        con.execute("INSERT INTO patents__us_term_of_grant VALUES (?,0,'','0','','')", (pid,))
    con.commit()
    con.close()
    con = sqlite3.connect(catalog)
    con.execute("CREATE TABLE catalog(id TEXT NOT NULL, filing_date TEXT, grant_date TEXT, fee_status TEXT, lapse_date TEXT, prior REAL, "
                "prior_pct REAL, fwd_early INTEGER, fwd_late INTEGER, fwd_cites INTEGER, fwd_recent INTEGER, grant_year INTEGER, "
                "reinstated_date TEXT, pta_days INTEGER, term_disclaimer INTEGER, disclaimer_date TEXT)")
    con.execute("CREATE UNIQUE INDEX catalog_id ON catalog(id)")
    con.execute("CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)")
    con.executemany("INSERT INTO meta VALUES (?,?)", [("data_version", DATA_VERSION), ("release", "20261027"),
                                                      ("fee_rule", "lapse:EXP.;reinstatement:EXPX,PMFG")])
    for pid in ids:
        filing, grant = dates(pid) if pid != NO_TEXT else ("2024-01-02", "2026-01-06")
        con.execute("INSERT INTO catalog(id, filing_date, grant_date, fee_status, lapse_date, fwd_early, fwd_late, fwd_cites, fwd_recent, "
                    "grant_year) VALUES (?,?,?,NULL,NULL,0,0,0,0,?)", ("US" + pid, filing, grant, int(grant[:4])))
    con.commit()
    con.close()
    con = sqlite3.connect(text)
    con.execute('CREATE TABLE "g_claims" ("uuid" TEXT PRIMARY KEY, "patent_id" TEXT, "patent_zero_prefix" TEXT, "claim_number" TEXT, '
                '"claim_dependent" TEXT, "claim_text" BLOB, "claim_sequence" INTEGER, "exemplary" INTEGER)')
    con.execute('CREATE INDEX "ix_g_claims_patent_id_claim_sequence" ON "g_claims"("patent_id", "claim_sequence")')
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", TEXT_VERSION), ("kind", "text")])
    for pid in patent_ids():
        for p, seq, text_, dep, num, ex in fixture_rows(pid):
            con.execute("INSERT INTO g_claims VALUES (?,?,?,?,?,?,?,?)",
                        (f"{p}-{seq}", p, p, num, dep or None, zlib.compress(text_.encode("utf-8"), 6), int(seq) if seq.isdigit() else None,
                         int(ex) if ex.isdigit() else None))
    con.commit()
    con.close()


def clear_caches():
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    lapse_brief.CACHE.clear()


@contextmanager
def in_service(search, catalog, text):
    """The three files as the files in service (settings swapped, every cache cleared before and after)."""
    old = dict(settings.LAPSE_SQLITE)
    saved = {k: getattr(settings, k, None) for k in ("LAPSE_CATALOG_PATH", "LAPSE_TEXT_PATH", "LAPSE_REPO_DIR")}
    settings.LAPSE_SQLITE["path"] = search
    settings.LAPSE_CATALOG_PATH = catalog
    settings.LAPSE_TEXT_PATH = text
    if REPO_DIR:
        settings.LAPSE_REPO_DIR = REPO_DIR
    clear_caches()
    try:
        yield
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        for k, v in saved.items():
            setattr(settings, k, v)
        clear_caches()


def key_minter(client, token_factory):
    """Fresh Free keys on demand (the tests' monthly allowance is 5 requests a key, LAPSE_FREE_MONTHLY_LIMIT), each from an
    account of its own email domain (new accounts per domain and day are limited, LAPSE_SIGNUP_DOMAIN_DAILY)."""
    def mint():
        sub = str(uuid.uuid4())
        token = token_factory(sub=sub, email=f"user-{sub[:8]}@d{sub[:8]}.example.test")
        r = client.post("/api/v1/meta/keys/", data=json.dumps({"terms_version": settings.LAPSE_TERMS_VERSION}),
                        content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token)
        assert r.status_code == 201, r.content
        return r.json()["api_key"]

    state = {"key": None, "used": 0}

    def key():
        if state["key"] is None or state["used"] >= 5:
            state["key"], state["used"] = mint(), 0
        state["used"] += 1
        return state["key"]
    return key
