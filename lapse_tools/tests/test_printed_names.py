"""The assignee names as printed on the grant (PatentRef 1.10.0): build_sample.py loads g_assignee_not_disambiguated beside
g_assignee_disambiguated, joined by patent_id and assignee_sequence.

A bulk directory of five small zips (patents, both assignee files, locations, inventors) in the PatentsView layout, built
here, then build_sample.py run on it as the refresh runner runs it (a subprocess, --mod 1). Checks: the three raw_*
columns follow the upstream columns of patents__assignees and carry the printed names of the same row; the disambiguated
names are unchanged; a repeated key in the raw file never adds an assignee row; the assignee record carries the names
printed on its most recent patent; _lapse_fields types them keyword; without the raw file the columns exist and are null.

  python -m pytest lapse_tools/tests/test_printed_names.py -q -p no:cacheprovider -o addopts=""
"""
import json
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
BUILD = ROOT / "lapse_tools" / "build_sample.py"

PATENTS = [["patent_id", "patent_type", "patent_date", "patent_title", "wipo_kind", "num_claims", "withdrawn", "filename"],
           ["7437596", "utility", "2008-10-14", "Self-healing control network", "B2", "20", "0", "a.xml"],
           ["6859933", "utility", "2005-02-22", "Dynamic vibration absorber", "B2", "9", "0", "b.xml"],
           ["9367739", "utility", "2016-06-14", "Iris recognition portable device", "B2", "15", "0", "c.xml"],
           ["D386112", "design", "1997-11-11", "Ornamental time capsule", "S1", "1", "0", "d.xml"]]
DIS = [["patent_id", "assignee_sequence", "assignee_id", "disambig_assignee_individual_name_first",
        "disambig_assignee_individual_name_last", "disambig_assignee_organization", "assignee_type", "location_id"],
       ["7437596", "0", "a-siemens", "", "", "SIEMENS BUILDING TECHNOLOGIES AG", "3", "loc1"],
       ["6859933", "0", "a-samsung-display", "", "", "SAMSUNG DISPLAY CO., LTD.", "3", "loc2"],
       ["9367739", "0", "a-samsung-display", "", "", "SAMSUNG DISPLAY CO., LTD.", "3", "loc2"],
       ["D386112", "0", "a-wolf-d", "Diana F.", "Wolf", "", "4", "loc1"],
       ["D386112", "1", "a-wolf-f", "Frank E.", "Wolf", "", "4", "loc1"]]
RAW = [["patent_id", "assignee_sequence", "assignee_id", "raw_assignee_individual_name_first",
        "raw_assignee_individual_name_last", "raw_assignee_organization", "assignee_type", "rawlocation_id"],
       ["7437596", "0", "a-siemens", "", "", "Siemens Building Technologies, Inc.", "2", "r1"],
       ["6859933", "0", "a-samsung-display", "", "", "Samsung Electronics, Co., Ltd.", "3", "r2"],
       ["9367739", "0", "a-samsung-display", "", "", "Samsung Display Co., Ltd", "3", "r3"],
       ["9367739", "0", "a-samsung-display", "", "", "a repeated key: never loaded", "3", "r3"],
       ["D386112", "0", "a-wolf-d", "Diana F.", "Wolf", "", "4", "r4"],
       ["D386112", "1", "a-wolf-f", "Frank E.", "Wolf", "", "4", "r4"]]
LOC = [["location_id", "disambig_city", "disambig_state", "disambig_country", "latitude", "longitude", "county",
        "state_fips", "county_fips"],
       ["loc1", "Buffalo Grove", "IL", "US", "42.15", "-87.96", "Lake", "17", "097"],
       ["loc2", "Yongin", "", "KR", "37.24", "127.18", "", "", ""]]
INV = [["patent_id", "inventor_sequence", "inventor_id", "disambig_inventor_name_first", "disambig_inventor_name_last",
        "gender_code", "location_id"],
       ["7437596", "0", "i1", "Norman R.", "McFarland", "M", "loc1"]]
# the es-data-load schema files fill_meta_and_fts reads (a few fields each; the rest of the layout needs none)
SCHEMAS = {"patents_fields.json": {"patent_id": {"type": "keyword"}, "patent_title": {"type": "text"},
                                   "patent_date": {"type": "date"}, "withdrawn": {"type": "boolean"},
                                   "assignees": {"type": "nested", "properties": {
                                       "assignee_id": {"type": "keyword"},
                                       "assignee_organization": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                                       "assignee_sequence": {"type": "integer"}}}},
           "assignees_fields.json": {"assignee_id": {"type": "keyword"},
                                     "assignee_organization": {"type": "text", "fields": {"keyword": {"type": "keyword"}}}}}
FILES = ["inventors", "locations", "us_patent_citations", "attorneys", "us_application_citations", "foreign_citations",
         "other_references", "rel_app_text", "cpc_groups", "uspc_subclasses", "ipcr"]


def tsv_zip(path, rows):
    body = "\n".join("\t".join('"' + c + '"' for c in r) for r in rows) + "\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(path.name[:-4], body)


def build(tmp, raw=True):
    bulk = tmp / "bulk"
    bulk.mkdir()
    tsv_zip(bulk / "g_patent.tsv.zip", PATENTS)
    tsv_zip(bulk / "g_assignee_disambiguated.tsv.zip", DIS)
    tsv_zip(bulk / "g_location_disambiguated.tsv.zip", LOC)
    tsv_zip(bulk / "g_inventor_disambiguated.tsv.zip", INV)
    if raw:
        tsv_zip(bulk / "g_assignee_not_disambiguated.tsv.zip", RAW)
    sch = tmp / "esdl" / "src" / "es_data_load" / "pv" / "schemas" / "granted"
    sch.mkdir(parents=True)
    for name in FILES:
        (sch / f"{name}_fields.json").write_text("{}", encoding="utf-8")
    for name, body in SCHEMAS.items():
        (sch / name).write_text(json.dumps(body), encoding="utf-8")
    out = tmp / "snapshot.db"
    r = subprocess.run([sys.executable, str(BUILD), "--bulk", str(bulk), "--out", str(out), "--stage", str(tmp / "stage.db"),
                        "--es-data-load", str(tmp / "esdl"), "--mod", "1"], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "BUILD_DONE" in r.stdout, r.stdout + r.stderr
    return out, r.stdout


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out, log = build(tmp_path_factory.mktemp("printed"))
    con = sqlite3.connect(out)
    yield con, log
    con.close()


def cols(con, t):
    return [r[1] for r in con.execute(f'PRAGMA table_info("{t}")')]


def test_columns_follow_the_upstream_columns(built):
    con, _ = built
    c = cols(con, "patents__assignees")
    assert c[-4:] == ["assignee_sequence", "raw_assignee_individual_name_first", "raw_assignee_individual_name_last",
                      "raw_assignee_organization"]
    assert cols(con, "assignees")[-3:] == ["raw_assignee_individual_name_first", "raw_assignee_individual_name_last",
                                           "raw_assignee_organization"]


def test_printed_name_beside_the_disambiguated_name(built):
    con, _ = built
    got = con.execute('SELECT _pid, assignee_sequence, assignee_organization, raw_assignee_organization, '
                      'assignee_individual_name_first, raw_assignee_individual_name_first, raw_assignee_individual_name_last '
                      'FROM patents__assignees ORDER BY _pid, _ord').fetchall()
    assert got == [
        ("6859933", 0, "SAMSUNG DISPLAY CO., LTD.", "Samsung Electronics, Co., Ltd.", None, None, None),
        ("7437596", 0, "SIEMENS BUILDING TECHNOLOGIES AG", "Siemens Building Technologies, Inc.", None, None, None),
        ("9367739", 0, "SAMSUNG DISPLAY CO., LTD.", "Samsung Display Co., Ltd", None, None, None),
        ("D386112", 0, None, None, "Diana F.", "Diana F.", "Wolf"),
        ("D386112", 1, None, None, "Frank E.", "Frank E.", "Wolf"),
    ]


def test_a_repeated_raw_key_adds_no_row(built):
    con, log = built
    assert con.execute("SELECT count(*) FROM patents__assignees").fetchone()[0] == len(DIS) - 1
    assert json.loads(con.execute("SELECT v FROM _lapse_build WHERE k='row_counts'").fetchone()[0])["patents__assignees"] == 5
    assert "asr rows 6" in log  # seven data rows in the file, one repeated key


def test_assignee_record_carries_the_names_of_its_most_recent_patent(built):
    con, _ = built
    got = dict(((r[0], r[1:]) for r in con.execute(
        "SELECT assignee_id, assignee_organization, raw_assignee_organization, raw_assignee_individual_name_last FROM assignees")))
    assert got["a-siemens"] == ("SIEMENS BUILDING TECHNOLOGIES AG", "Siemens Building Technologies, Inc.", None)
    # two patents: 6859933 (2005) and 9367739 (2016); the record's names come from the most recent one
    assert got["a-samsung-display"] == ("SAMSUNG DISPLAY CO., LTD.", "Samsung Display Co., Ltd", None)
    assert got["a-wolf-d"] == (None, None, "Wolf")


def test_lapse_fields_type_them_keyword(built):
    con, _ = built
    got = sorted(con.execute("SELECT idx, path, field, es_type FROM _lapse_fields WHERE field LIKE 'raw_assignee_%'"))
    assert got == sorted([(i, p, f, "keyword") for i, p in (("patents", "assignees"), ("assignees", ""))
                          for f in ("raw_assignee_individual_name_first", "raw_assignee_individual_name_last",
                                    "raw_assignee_organization")])


def test_no_index_trigram_or_stats_on_them(built):
    con, _ = built
    ddl = " ".join(r[0] or "" for r in con.execute("SELECT sql FROM sqlite_master WHERE type IN ('index', 'table') AND name NOT IN ('patents__assignees', 'assignees')"))
    assert "raw_assignee" not in ddl
    assert not con.execute("SELECT 1 FROM _lapse_value_stats WHERE col LIKE 'raw_assignee%'").fetchone()


def test_without_the_raw_file_the_columns_are_null(tmp_path):
    out, log = build(tmp_path, raw=False)
    con = sqlite3.connect(out)
    try:
        assert "(missing) g_assignee_not_disambiguated.tsv.zip" in log
        assert "raw_assignee_organization" in cols(con, "patents__assignees")
        assert con.execute("SELECT count(*), count(raw_assignee_organization), count(assignee_organization) "
                           "FROM patents__assignees").fetchone() == (5, 0, 3)
    finally:
        con.close()
