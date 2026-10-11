"""Raw names from the weekly grant XML (PatentRef checklist 4.7): the `disambiguated` flag on the inventor, assignee
and attorney groups of /api/v1/patent/, and the `weekly_grants` block of /api/v1/meta/.

A search database in the layout lapse/grants.py leaves behind in the patentref repo: the flag column added with
DEFAULT 1 (every PatentsView row reads 1), typed boolean in _lapse_fields, and one weekly-grant patent whose rows
carry 0 and no ids. Checks: every row of every group carries one key set, on both kinds of rows and for every shape
of f (gate 2.7: the R package binds the rows of a group into one table); a whole-group request answers upstream's
keys on both kinds of rows (no flag key); naming the field shows it on every row, true and false; q filters on it;
the default f is unchanged; /meta/ reports the overlay. Reuses conftest.py (temporary Django DB, operator key).

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
              ("attorneys", "disambiguated", "boolean", 0), ("application", "application_id", "keyword", 0),
              ("application", "filing_date", "date", 0), ("cpc_current", "cpc_sequence", "long", 0),
              ("cpc_current", "cpc_class_id", "keyword", 0), ("cpc_current", "cpc_group_id", "keyword", 0)]
    con.executemany("INSERT INTO _lapse_fields VALUES ('patents',?,?,?,?)", fields)
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_title TEXT, patent_date TEXT, withdrawn INT)")
    con.execute("CREATE TABLE patents__inventors(_pid TEXT NOT NULL, _ord INTEGER, inventor_id TEXT, inventor_name_first TEXT, "
                "inventor_name_last TEXT, inventor_sequence INTEGER)")
    con.execute("CREATE TABLE patents__assignees(_pid TEXT NOT NULL, _ord INTEGER, assignee_id TEXT, assignee_organization TEXT)")
    con.execute("CREATE TABLE patents__attorneys(_pid TEXT NOT NULL, _ord INTEGER, attorney_id TEXT, attorney_organization TEXT)")
    con.execute("CREATE TABLE patents__application(_pid TEXT NOT NULL, _ord INTEGER, application_id TEXT, filing_date TEXT)")
    con.execute("CREATE TABLE patents__cpc_current(_pid TEXT NOT NULL, _ord INTEGER, cpc_sequence INTEGER, cpc_class_id TEXT, "
                "cpc_group_id TEXT)")
    con.executemany("INSERT INTO patents VALUES (?,?,?,0)", [("12507606", "Agricultural machine", "2025-12-30"),
                                                             ("12751376", "Modular planter systems", "2026-10-06")])
    con.execute("INSERT INTO patents__inventors VALUES ('12507606', 0, 'fl:ud_ln:beschorn-1', 'Udo', 'Beschorn', 0)")
    con.execute("INSERT INTO patents__assignees VALUES ('12507606', 0, 'f24d71b6', 'CLAAS')")
    con.execute("INSERT INTO patents__attorneys VALUES ('12507606', 0, 'f97465e5', 'Lempia Summerfield Katz LLC')")
    con.execute("INSERT INTO patents__application VALUES ('12507606', 0, '18/123456', '2023-03-01')")
    con.execute("INSERT INTO patents__cpc_current VALUES ('12507606', 0, 0, 'A01', 'A01B63/00')")
    # what lapse/grants.py does to a copy of the file in service, then the rows of one weekly-grant patent
    for t in ("patents__inventors", "patents__assignees", "patents__attorneys"):
        con.execute(f"ALTER TABLE {t} ADD COLUMN disambiguated INTEGER DEFAULT 1")
    con.executemany("INSERT INTO patents__inventors VALUES ('12751376', ?, NULL, ?, ?, ?, 0)",
                    [(0, "Daniel Thomas", "Jones", 0), (1, "Bronte", "Brillantes", 1)])
    con.execute("INSERT INTO patents__assignees VALUES ('12751376', 0, NULL, 'Oliviine, Inc.', 0)")
    con.execute("INSERT INTO patents__attorneys VALUES ('12751376', 0, NULL, 'Schacht Law Office, Inc.', 0)")
    con.execute("INSERT INTO patents__application VALUES ('12751376', 0, '18/654321', '2024-05-02')")
    con.executemany("INSERT INTO patents__cpc_current VALUES ('12751376', ?, ?, ?, ?)",
                    [(0, 0, "A01", "A01C7/00"), (1, 1, "A01", "A01C5/06")])
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


GROUPS = ["inventors", "assignees", "attorneys", "application", "cpc_current"]
FLAGGED = ["inventors", "assignees", "attorneys"]
BOTH = {"_or": [{"patent_id": "12507606"}, {"patent_id": "12751376"}]}


def key_sets(patents):
    """{group: set of key tuples} over every row of every patent in a response."""
    out = {}
    for p in patents:
        for g, rows in p.items():
            if isinstance(rows, list):
                for row in rows:
                    out.setdefault(g, set()).add(tuple(sorted(row)))
    return out


def test_every_row_of_every_group_has_one_key_set(client, user_key, weekly_env):
    _, key, _ = user_key
    shapes = [
        ["patent_id"] + GROUPS,                                                   # whole groups (what the R package sends)
        ["patent_id"] + [g + ".*" for g in GROUPS],                               # whole groups by wildcard
        ["patent_id"] + GROUPS + [g + ".disambiguated" for g in FLAGGED],          # whole groups and the flag
        ["patent_id", "inventors.inventor_name_last", "inventors.disambiguated", "assignees.assignee_id",
         "attorneys.disambiguated", "cpc_current.cpc_group_id"],                  # subfields, flag in some groups
        ["patent_id", "inventors.inventor_id", "inventors.inventor_name_last", "assignees.assignee_organization"],
    ]
    for f in shapes:
        r = _patent(client, key, BOTH, f)
        assert r.status_code == 200, (f, r.content)
        patents = r.json()["patents"]
        assert {p["patent_id"] for p in patents} == {"12507606", "12751376"}, f
        sets = key_sets(patents)
        assert sets, f
        for g, ks in sets.items():
            assert len(ks) == 1, f"f={f}: group {g} has {len(ks)} key sets across PatentsView and weekly rows: {sorted(ks)}"


def test_whole_group_answers_upstream_keys_on_both_kinds_of_rows(client, user_key, weekly_env):
    _, key, _ = user_key
    r = _patent(client, key, BOTH, ["patent_id"] + GROUPS)
    assert r.status_code == 200, r.content
    by = {p["patent_id"]: p for p in r.json()["patents"]}
    pv = by["12507606"]
    assert pv["inventors"] == [{"inventor": pv["inventors"][0]["inventor"], "inventor_id": "fl:ud_ln:beschorn-1",
                                "inventor_name_first": "Udo", "inventor_name_last": "Beschorn", "inventor_sequence": 0}]
    raw = by["12751376"]
    for p in (pv, raw):
        for g in FLAGGED:
            assert all("disambiguated" not in row for row in p[g]), (p["patent_id"], g)
    assert raw["inventors"][0]["inventor_id"] is None and raw["inventors"][0]["inventor"] is None
    assert raw["inventors"][0]["inventor_name_last"] == "Jones"
    assert raw["assignees"][0] == {"assignee": None, "assignee_id": None, "assignee_organization": "Oliviine, Inc."}
    assert set(raw["inventors"][0]) == set(pv["inventors"][0])
    assert set(raw["cpc_current"][0]) == set(pv["cpc_current"][0])


def test_named_flag_on_every_row_with_the_whole_group(client, user_key, weekly_env):
    _, key, _ = user_key
    r = _patent(client, key, BOTH, ["patent_id", "inventors", "inventors.disambiguated", "attorneys.disambiguated"])
    assert r.status_code == 200, r.content
    by = {p["patent_id"]: p for p in r.json()["patents"]}
    assert [i["disambiguated"] for i in by["12507606"]["inventors"]] == [True]
    assert [i["disambiguated"] for i in by["12751376"]["inventors"]] == [False, False]
    assert by["12507606"]["inventors"][0]["inventor_name_last"] == "Beschorn"
    assert by["12507606"]["attorneys"] == [{"disambiguated": True}]
    assert by["12751376"]["attorneys"] == [{"disambiguated": False}]


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
