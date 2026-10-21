"""The assignee names as printed on the grant (PatentRef 1.10.0) on /api/v1/patent/ and /api/v1/assignee/.

Two search databases in the layout lapse_tools/build_sample.py writes: one with the raw_* columns (and one weekly-grant
patent, whose printed names lapse/grants.py copies from its names as printed), and one built before them (no such
column). Checks: the default f of both endpoints is unchanged; a whole group ("assignees", "assignees.*") answers
upstream's keys only; f naming a printed field returns it on every row, beside the disambiguated name; q or s on one
answers 400 ERR_Q with the reason; on the older file f still gets the keys, null; every row of a group has one key set.

  python -m pytest lapse_accounts/tests/test_printed_names.py -q -p no:cacheprovider -o addopts=""
"""
import json
import os
import sqlite3

import pytest
from django.conf import settings

from API.search_sqlite import LapseSQLiteSearch
from lapse_accounts.tests.conftest import _TMP

RAW = ["raw_assignee_individual_name_first", "raw_assignee_individual_name_last", "raw_assignee_organization"]


def build_db(path, printed=True):
    if os.path.exists(path):
        os.unlink(path)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_indices VALUES (?,?,?)", [("patents", "patents", "patent_id"),
                                                                 ("assignees", "assignees", "assignee_id")])
    fields = [("patents", "", "patent_id", "keyword", 0), ("patents", "", "patent_title", "text", 1),
              ("patents", "", "patent_date", "date", 0), ("patents", "", "withdrawn", "boolean", 0),
              ("patents", "assignees", "assignee_id", "keyword", 0), ("patents", "assignees", "assignee_organization", "text", 1),
              ("patents", "assignees", "assignee_individual_name_first", "text", 1),
              ("patents", "assignees", "assignee_individual_name_last", "text", 1),
              ("patents", "assignees", "assignee_sequence", "integer", 0), ("patents", "assignees", "disambiguated", "boolean", 0),
              ("assignees", "", "assignee_id", "keyword", 0), ("assignees", "", "assignee_organization", "text", 1),
              ("assignees", "", "assignee_individual_name_first", "text", 1),
              ("assignees", "", "assignee_individual_name_last", "text", 1)]
    if printed:
        fields += [(i, p, c, "keyword", 0) for i, p in (("patents", "assignees"), ("assignees", "")) for c in RAW]
    con.executemany("INSERT INTO _lapse_fields VALUES (?,?,?,?,?)", fields)
    extra = ", " + ", ".join(f"{c} TEXT" for c in RAW) if printed else ""
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_title TEXT, patent_date TEXT, withdrawn INT)")
    con.execute("CREATE TABLE patents__assignees(_pid TEXT NOT NULL, _ord INTEGER, assignee_id TEXT, "
                "assignee_individual_name_first TEXT, assignee_individual_name_last TEXT, assignee_organization TEXT, "
                f"assignee_sequence INTEGER{extra}, disambiguated INTEGER DEFAULT 1)")
    con.execute("CREATE TABLE assignees(assignee_id TEXT PRIMARY KEY, assignee_individual_name_first TEXT, "
                f"assignee_individual_name_last TEXT, assignee_organization TEXT, assignee_num_patents INTEGER{extra})")
    con.executemany("INSERT INTO patents VALUES (?,?,?,0)", [
        ("7437596", "Self-healing control network for building automation systems", "2008-10-14"),
        ("6859933", "Dynamic vibration absorber for a disk player", "2005-02-22"),
        ("D386112", "Ornamental time capsule", "1997-11-11"),
        ("12751376", "Modular planter systems", "2026-10-06")])
    rows = [("7437596", 0, "a-siemens", None, None, "SIEMENS BUILDING TECHNOLOGIES AG", 0,
             None, None, "Siemens Building Technologies, Inc.", 1),
            ("6859933", 0, "a-sdc", None, None, "SAMSUNG DISPLAY CO., LTD.", 0, None, None, "Samsung Electronics, Co., Ltd.", 1),
            ("D386112", 0, "a-wolf-d", "Diana F.", "Wolf", None, 0, "Diana F.", "Wolf", None, 1),
            ("D386112", 1, "a-wolf-f", "Frank E.", "Wolf", None, 1, "Frank E.", "Wolf", None, 1),
            # a weekly-grant row: no id, the names as printed, the printed fields equal to them (lapse/grants.py)
            ("12751376", 0, None, None, None, "Oliviine, Inc.", 0, None, None, "Oliviine, Inc.", 0)]
    for r in rows:
        v = r if printed else r[:7] + r[10:]
        con.execute(f"INSERT INTO patents__assignees VALUES ({','.join('?' * len(v))})", v)
    ents = [("a-siemens", None, None, "SIEMENS BUILDING TECHNOLOGIES AG", 812, None, None, "Siemens Building Technologies, Inc."),
            ("a-sdc", None, None, "SAMSUNG DISPLAY CO., LTD.", 174536, None, None, "Samsung Display Co., Ltd"),
            ("a-wolf-d", "Diana F.", "Wolf", None, 1, "Diana F.", "Wolf", None),
            ("a-sec", None, None, "Samsung Electronics Co., Ltd.", 200000, None, None, "SAMSUNG ELECTRONICS CO., LTD.")]
    for e in ents:
        v = e if printed else e[:5]
        con.execute(f"INSERT INTO assignees VALUES ({','.join('?' * len(v))})", v)
    con.commit()
    con.close()


def _use(path):
    settings.LAPSE_SQLITE["path"] = path
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()


@pytest.fixture(params=[True, False], ids=["printed", "older-file"])
def env(request):
    path = str(_TMP / f"printed_{request.param}.db")
    build_db(path, printed=request.param)
    old = dict(settings.LAPSE_SQLITE)
    old_cat = getattr(settings, "LAPSE_CATALOG_PATH", None)
    settings.LAPSE_CATALOG_PATH = str(_TMP / "no_catalog.db")
    _use(path)
    try:
        yield request.param
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        settings.LAPSE_CATALOG_PATH = old_cat
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()


def get(client, key, route, q, f=None, s=None):
    params = {"q": json.dumps(q)}
    if f is not None:
        params["f"] = json.dumps(f)
    if s is not None:
        params["s"] = json.dumps(s)
    return client.get(route, params, HTTP_X_API_KEY=key)


ALL = {"_or": [{"patent_id": p} for p in ("7437596", "6859933", "D386112", "12751376")]}
UPSTREAM_KEYS = {"assignee", "assignee_id", "assignee_individual_name_first", "assignee_individual_name_last",
                 "assignee_organization", "assignee_sequence"}


def by_id(r):
    assert r.status_code == 200, r.content
    return {p["patent_id"]: p for p in r.json()["patents"]}


def test_default_f_is_unchanged(client, user_key, env):
    _, key, _ = user_key
    r = get(client, key, "/api/v1/patent/", {"patent_id": "7437596"})
    assert r.status_code == 200 and r.json()["patents"] == [
        {"patent_id": "7437596", "patent_title": "Self-healing control network for building automation systems",
         "patent_date": "2008-10-14"}]
    r = get(client, key, "/api/v1/assignee/", {"assignee_id": "a-siemens"})
    assert r.status_code == 200, r.content
    assert set(r.json()["assignees"][0]) == {"assignee_id", "assignee_individual_name_first", "assignee_individual_name_last",
                                             "assignee_organization"}


def test_whole_group_answers_upstream_keys(client, user_key, env):
    _, key, _ = user_key
    for f in (["patent_id", "assignees"], ["patent_id", "assignees.*"]):
        got = by_id(get(client, key, "/api/v1/patent/", ALL, f))
        for p in got.values():
            for row in p["assignees"]:
                assert set(row) == UPSTREAM_KEYS, (f, row)
        assert got["7437596"]["assignees"][0]["assignee_organization"] == "SIEMENS BUILDING TECHNOLOGIES AG"


def test_named_printed_name_beside_the_disambiguated_name(client, user_key, env):
    printed = env
    _, key, _ = user_key
    got = by_id(get(client, key, "/api/v1/patent/", ALL,
                    ["patent_id", "assignees.assignee_organization", "assignees.raw_assignee_organization"]))
    want = {"7437596": [("SIEMENS BUILDING TECHNOLOGIES AG", "Siemens Building Technologies, Inc.")],
            "6859933": [("SAMSUNG DISPLAY CO., LTD.", "Samsung Electronics, Co., Ltd.")],
            "D386112": [(None, None), (None, None)],
            "12751376": [("Oliviine, Inc.", "Oliviine, Inc.")]}
    for pid, rows in want.items():
        assert [(a["assignee_organization"], a["raw_assignee_organization"]) for a in got[pid]["assignees"]] == \
            [(d, p if printed else None) for d, p in rows], pid
    # the whole group and the three printed fields: upstream's keys plus the three, on every row
    got = by_id(get(client, key, "/api/v1/patent/", ALL, ["patent_id", "assignees"] + ["assignees." + c for c in RAW]))
    for p in got.values():
        for row in p["assignees"]:
            assert set(row) == UPSTREAM_KEYS | set(RAW), row
    d = got["D386112"]["assignees"]
    assert [(a["raw_assignee_individual_name_first"], a["raw_assignee_individual_name_last"]) for a in d] == \
        ([("Diana F.", "Wolf"), ("Frank E.", "Wolf")] if printed else [(None, None), (None, None)])


def test_assignee_record_with_the_printed_name(client, user_key, env):
    printed = env
    _, key, _ = user_key
    r = get(client, key, "/api/v1/assignee/", {"assignee_id": "a-sdc"}, ["assignee_id", "assignee_organization",
                                                                       "raw_assignee_organization"])
    assert r.status_code == 200, r.content
    assert r.json()["assignees"] == [{"assignee_id": "a-sdc", "assignee_organization": "SAMSUNG DISPLAY CO., LTD.",
                                      "raw_assignee_organization": "Samsung Display Co., Ltd" if printed else None}]


def test_q_and_s_on_a_printed_field_answer_400(client, user_key, env):
    _, key, _ = user_key
    for route, q, s in (("/api/v1/patent/", {"assignees.raw_assignee_organization": "ResMed Limited"}, None),
                        ("/api/v1/patent/", {"_begins": {"assignees.raw_assignee_organization": "Res"}}, None),
                        ("/api/v1/assignee/", {"raw_assignee_organization": "ResMed Limited"}, None),
                        ("/api/v1/assignee/", {"assignee_id": "a-sdc"}, [{"raw_assignee_organization": "asc"}])):
        r = get(client, key, route, q, ["patent_id"] if "patent" in route else ["assignee_id"], s)
        assert r.status_code == 400, (route, q, s, r.status_code, r.content)
        assert r["X-Status-Reason-Code"] == "ERR_Q"
        assert "is returned with f only" in r["X-Status-Reason"], r["X-Status-Reason"]


def test_every_row_of_the_group_has_one_key_set(client, user_key, env):
    _, key, _ = user_key
    shapes = [["patent_id", "assignees"], ["patent_id", "assignees", "assignees.disambiguated"],
              ["patent_id", "assignees.raw_assignee_organization"],
              ["patent_id", "assignees.assignee_id", "assignees.raw_assignee_individual_name_last", "assignees.disambiguated"]]
    for f in shapes:
        got = by_id(get(client, key, "/api/v1/patent/", ALL, f))
        keys = {tuple(sorted(row)) for p in got.values() for row in p.get("assignees", [])}
        assert len(keys) == 1, (f, keys)


def test_contract_runner_checks_pass(client, user_key, env):
    """The four checks lapse_tools/compat/run_contract.py adds for the printed names (every build's contract run and the
    daily probe read them) pass on both files, run through this server."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("run_contract_t", Path(__file__).resolve().parents[2] / "lapse_tools" / "compat" / "run_contract.py")
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    _, key, _ = user_key

    def call(base, k, method, path, params=None, **kw):
        r = client.get(path, {a: (v if isinstance(v, str) else json.dumps(v)) for a, v in (params or {}).items()}, HTTP_X_API_KEY=k)
        try:
            js = r.json()
        except ValueError:
            js = None
        return r.status_code, dict(r.headers), js, 0.0

    out = []
    rc.printed_name_checks("http://test", key, lambda name, ok, detail: out.append((name, ok, detail)), call=call)
    assert [n for n, _, _ in out] == ["printed assignee name with f only", "whole assignees group without the printed names",
                                      "q on the printed assignee name -> 400", "assignee record with the printed name, f only"]
    assert all(ok for _, ok, _ in out), out
