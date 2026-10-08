"""Raw names from the weekly grant XML (PatentRef checklist 4.7): the `disambiguated` flag on the inventor, assignee
and attorney groups of /api/v1/patent/, and the `weekly_grants` block of /api/v1/meta/.

A search database in the layout lapse/grants.py leaves behind in the patentref repo: the flag column added with
DEFAULT 1 (every PatentsView row reads 1), typed boolean in _lapse_fields, and one weekly-grant patent whose rows
carry 0 and no ids. Checks: a whole-group request answers PatentsView rows exactly as before (no flag key) and shows
`disambiguated: false` on the raw rows; naming the field shows true and false; q filters on it; the default f is
unchanged; /meta/ reports the overlay. Reuses conftest.py (temporary Django DB, operator key).

  python -m pytest lapse_accounts/tests/test_weekly_grants.py -q -p no:cacheprovider
"""
import json
import os

import pytest
from django.conf import settings

from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP


def build_db(path):
    import sqlite3
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    fields = [("", "patent_id", "keyword", 0), ("", "patent_title", "text", 1), ("", "patent_date", "date", 0), ("", "withdrawn", "boolean", 0),
              ("inventors", "inventor_id", "keyword", 0), ("inventors", "inventor_name_first", "text", 1),
              ("inventors", "inventor_name_last", "text", 1), ("inventors", "inventor_sequence", "long", 0),
              ("inventors", "disambiguated", "boolean", 0), ("assignees", "assignee_id", "keyword", 0),
              ("assignees", "assignee_organization", "text", 1), ("assignees", "disambiguated", "boolean", 0),
              ("attorneys", "attorney_id", "keyword", 0), ("attorneys", "attorney_organization", "text", 1),
              ("attorneys", "disambiguated", "boolean", 0)]
    con.executemany("INSERT INTO _lapse_fields VALUES ('patents',?,?,?,?)", fields)
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_title TEXT, patent_date TEXT, withdrawn INT)")
    con.execute("CREATE TABLE patents__inventors(_pid TEXT NOT NULL, _ord INTEGER, inventor_id TEXT, inventor_name_first TEXT, "
                "inventor_name_last TEXT, inventor_sequence INTEGER)")
    con.execute("CREATE TABLE patents__assignees(_pid TEXT NOT NULL, _ord INTEGER, assignee_id TEXT, assignee_organization TEXT)")
    con.execute("CREATE TABLE patents__attorneys(_pid TEXT NOT NULL, _ord INTEGER, attorney_id TEXT, attorney_organization TEXT)")
    con.executemany("INSERT INTO patents VALUES (?,?,?,0)", [("12507606", "Agricultural machine", "2025-12-30"),
                                                             ("12751376", "Modular planter systems", "2026-10-06")])
    con.execute("INSERT INTO patents__inventors VALUES ('12507606', 0, 'fl:ud_ln:beschorn-1', 'Udo', 'Beschorn', 0)")
    con.execute("INSERT INTO patents__assignees VALUES ('12507606', 0, 'f24d71b6', 'CLAAS')")
    con.execute("INSERT INTO patents__attorneys VALUES ('12507606', 0, 'f97465e5', 'Lempia Summerfield Katz LLC')")
    # what lapse/grants.py does to a copy of the file in service, then the rows of one weekly-grant patent
    for t in ("patents__inventors", "patents__assignees", "patents__attorneys"):
        con.execute(f"ALTER TABLE {t} ADD COLUMN disambiguated INTEGER DEFAULT 1")
    con.executemany("INSERT INTO patents__inventors VALUES ('12751376', ?, NULL, ?, ?, ?, 0)",
                    [(0, "Daniel Thomas", "Jones", 0), (1, "Bronte", "Brillantes", 1)])
    con.execute("INSERT INTO patents__assignees VALUES ('12751376', 0, NULL, 'Oliviine, Inc.', 0)")
    con.execute("INSERT INTO patents__attorneys VALUES ('12751376', 0, NULL, 'Schacht Law Office, Inc.', 0)")
    weeks = [{"file": "ipg261006.zip", "issue_date": "2026-10-06", "docs": 7977, "patents": 1, "present": 7976}]
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", "20261006.5"), ("release", "20261006"),
                                                             ("grants", json.dumps(weeks)), ("pv_through", "2025-12-30"),
                                                             ("grants_through", "2026-10-06"), ("grant_patents", "1")])
    con.commit()
    con.close()


@pytest.fixture
def weekly_env():
    path = str(_TMP / "weekly_grants.db")
    build_db(path)
    old = dict(settings.LAPSE_SQLITE)
    old_cat = getattr(settings, "LAPSE_CATALOG_PATH", None)
    settings.LAPSE_SQLITE["path"] = path
    settings.LAPSE_CATALOG_PATH = str(_TMP / "no_catalog.db")
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    try:
        yield path
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        settings.LAPSE_CATALOG_PATH = old_cat
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()


def _patent(client, key, q, f=None):
    params = {"q": json.dumps(q)}
    if f is not None:
        params["f"] = json.dumps(f)
    return client.get("/api/v1/patent/", params, HTTP_X_API_KEY=key)


def test_whole_group_shows_the_flag_only_on_raw_rows(client, user_key, weekly_env):
    _, key, _ = user_key
    r = _patent(client, key, {"_or": [{"patent_id": "12507606"}, {"patent_id": "12751376"}]},
                ["patent_id", "inventors", "assignees", "attorneys"])
    assert r.status_code == 200, r.content
    by = {p["patent_id"]: p for p in r.json()["patents"]}
    pv = by["12507606"]
    assert pv["inventors"] == [{"inventor": pv["inventors"][0]["inventor"], "inventor_id": "fl:ud_ln:beschorn-1",
                                "inventor_name_first": "Udo", "inventor_name_last": "Beschorn", "inventor_sequence": 0}]
    assert "disambiguated" not in pv["assignees"][0] and "disambiguated" not in pv["attorneys"][0]
    raw = by["12751376"]
    assert [i["disambiguated"] for i in raw["inventors"]] == [False, False]
    assert raw["inventors"][0]["inventor_id"] is None and raw["inventors"][0]["inventor"] is None
    assert raw["inventors"][0]["inventor_name_last"] == "Jones"
    assert raw["assignees"][0] == {"assignee": None, "assignee_id": None, "assignee_organization": "Oliviine, Inc.", "disambiguated": False}
    assert raw["attorneys"][0]["disambiguated"] is False


def test_named_flag_shows_true_and_false_and_q_filters(client, user_key, weekly_env):
    _, key, _ = user_key
    r = _patent(client, key, {"_or": [{"patent_id": "12507606"}, {"patent_id": "12751376"}]},
                ["patent_id", "inventors.inventor_name_last", "inventors.disambiguated"])
    assert r.status_code == 200, r.content
    by = {p["patent_id"]: p["inventors"] for p in r.json()["patents"]}
    assert by["12507606"] == [{"inventor_name_last": "Beschorn", "disambiguated": True}]
    assert by["12751376"] == [{"inventor_name_last": "Jones", "disambiguated": False},
                              {"inventor_name_last": "Brillantes", "disambiguated": False}]
    r = _patent(client, key, {"inventors.disambiguated": False}, ["patent_id"])
    assert r.status_code == 200, r.content
    assert [p["patent_id"] for p in r.json()["patents"]] == ["12751376"]
    r = _patent(client, key, {"assignees.disambiguated": True}, ["patent_id"])
    assert [p["patent_id"] for p in r.json()["patents"]] == ["12507606"]


def test_default_f_is_unchanged(client, user_key, weekly_env):
    _, key, _ = user_key
    r = _patent(client, key, {"patent_id": "12751376"})
    assert r.status_code == 200 and r.json()["patents"] == [{"patent_id": "12751376", "patent_title": "Modular planter systems",
                                                              "patent_date": "2026-10-06"}]


def test_meta_reports_the_weekly_grants(client, weekly_env):
    r = client.get("/api/v1/meta/")
    assert r.status_code == 200
    w = r.json()["weekly_grants"]
    assert w["patentsview_through"] == "2025-12-30" and w["weekly_grants_through"] == "2026-10-06"
    assert w["weeks"] == 1 and w["patents"] == 1 and "disambiguated: false" in w["names"]
