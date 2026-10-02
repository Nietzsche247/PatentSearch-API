"""Build a SQLite sample database shaped like the PatentSearch ES indices.

  py build_sample.py --bulk C:\\LapseAPI\\bulk --out C:\\LapseAPI\\data\\sample.db
       [--stage C:\\LapseAPI\\data\\stage.db] [--mod 94] [--es-data-load C:\\LapseAPI\\es-data-load]

Inputs are the PatentsView PVGPATDIS bulk zips (g_patent, g_patent_abstract, g_application,
g_inventor_disambiguated, g_assignee_disambiguated, g_location_disambiguated, g_cpc_current,
g_us_patent_citation, g_wipo_technology, g_figures, g_us_term_of_grant, g_botanic, g_gov_interest*,
g_pct_data, g_foreign_priority, and since the second full build g_attorney_disambiguated, g_us_application_citation,
g_foreign_citation, g_other_reference, g_rel_app_text, g_cpc_at_issue, g_cpc_title, g_ipc_at_issue, g_uspc_at_issue,
g_us_rel_doc, g_examiner_not_disambiguated, g_applicant_not_disambiguated, g_persistent_inventor,
g_persistent_assignee). Missing optional files are skipped.

Layout (read by API/search_sqlite.py):
  <index>                      one row per ES document, columns named like the API fields
  <index>__<nested_path>       one row per nested object; _pid = parent key, _ord = order
  fts_<table>                  FTS5 index over the ES "text" fields of that table
  _lapse_fields                index, path, field, es_type, keyword_subfield (from es-data-load schemas)
  _lapse_indices               index, table, key_field
  persistent_<kind>s           g_persistent_inventor / g_persistent_assignee as shipped (every column, no endpoint)
Sample rule: numeric part of patent_id mod N == 0, plus fixed ids and a few name-matched extras.
"""
import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
from array import array
from pathlib import Path

csv.field_size_limit(2**31 - 1)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.0f}s]", *a, flush=True)


FIXED_IDS = ["7861317", "D345393", "10905426", "11172927", "11191203", "11750976"]
# name based extras so the documented example queries have hits in the sample (limit per rule)
INV_LAST_EXTRAS = {"Whitney", "Hopper", "Whitener", "Heath"}
INV_PAIR_EXTRAS = {("George", "Washington"), ("Abraham", "Lincoln")}
INV_FIRST_CONTAINS = ["sarvo"]
ASG_EXTRA_PREFIX = ["apple"]
ASG_EXTRA_EXACT = {"CNH Industrial Canada, Ltd."}
EXTRA_LIMIT = 40


def rows(bulk, name):
    """Yield dict rows of a PatentsView tsv inside its zip; nothing if the file is absent."""
    p = Path(bulk) / f"{name}.tsv.zip"
    if not p.exists():
        log("  (missing)", p.name)
        return
    zf = zipfile.ZipFile(p)
    inner = zf.namelist()[0]
    with zf.open(inner) as f:
        r = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""),
                       delimiter="\t", quotechar='"')
        head = next(r)
        for rec in r:
            if len(rec) != len(head):
                continue
            yield dict(zip(head, rec))


def nz(v):
    return None if v is None or v == "" else v


def to_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def zero_prefix(pid):
    m = re.match(r"^([A-Za-z]*)(\d+)$", pid or "")
    if not m:
        return pid
    a, d = m.groups()
    return a + d.rjust(8 - len(a), "0")


def numeric_part(pid):
    d = re.sub(r"\D", "", pid or "")
    return int(d) if d else -1


# ---------------------------------------------------------------- stage (full-corpus tables)
def batched_insert(con, sql, it, n=200000):
    buf = []
    total = 0
    for rec in it:
        buf.append(rec)
        if len(buf) >= n:
            con.executemany(sql, buf)
            total += len(buf)
            buf = []
    if buf:
        con.executemany(sql, buf)
        total += len(buf)
    con.commit()
    return total


class Batcher:
    """Buffer rows for one statement and run them in chunks, so a single scan can feed several targets.

    Rows reach the database in the order they were added, the same as a list handed to batched_insert."""

    def __init__(self, con, sql, n=50000):
        self.con, self.sql, self.n = con, sql, n
        self.buf, self.total = [], 0

    def add(self, rec):
        self.buf.append(rec)
        if len(self.buf) >= self.n:
            self.flush()

    def flush(self):
        if self.buf:
            self.con.executemany(self.sql, self.buf)
            self.total += len(self.buf)
            self.buf = []

    def close(self):
        self.flush()
        self.con.commit()
        return self.total


def scan_chunks(con, sql, n=50000):
    """Yield fetched row chunks of `sql`, which must select rowid first and take (rowid lower bound, limit).

    Each chunk is fully fetched before it is handed out, so the caller may update the same table in between."""
    last = 0
    while True:
        chunk = con.execute(sql, (last, n)).fetchall()
        if not chunk:
            return
        yield chunk
        last = chunk[-1][0]


def build_stage(bulk, stage_path, mod):
    st = sqlite3.connect(stage_path)
    st.execute("pragma journal_mode=off")
    st.execute("pragma synchronous=off")
    st.execute("pragma cache_size=-2000000")
    st.execute("pragma mmap_size=8000000000")
    st.execute("pragma temp_store=memory")
    have = {r[0] for r in st.execute("select name from sqlite_master where type='table'")}
    # a staged table left empty by a missing bulk file is rebuilt, so a later download is picked up
    for t in sorted(have):
        if st.execute(f'select count(*) from "{t}"').fetchone()[0] == 0:
            log("  dropping empty staged table", t)
            st.execute(f'drop table "{t}"')
            have.discard(t)
    if "p" not in have:
        log("stage g_patent")
        st.execute("create table p(patent_id text primary key, patent_type text, patent_date text, patent_title text,"
                   " wipo_kind text, withdrawn int, smp int)")
        n = batched_insert(st, "insert or ignore into p values (?,?,?,?,?,?,?)", (
            (r["patent_id"], nz(r["patent_type"]), nz(r["patent_date"]), nz(r["patent_title"]), nz(r["wipo_kind"]),
             to_int(r["withdrawn"]) or 0, 1 if numeric_part(r["patent_id"]) % mod == 0 else 0)
            for r in rows(bulk, "g_patent")))
        log("  p rows", n)
    if "inv" not in have:
        log("stage g_inventor_disambiguated")
        st.execute("create table inv(patent_id text, seq int, inventor_id text, first text, last text, gender text,"
                   " location_id text)")
        n = batched_insert(st, "insert into inv values (?,?,?,?,?,?,?)", (
            (r["patent_id"], to_int(r["inventor_sequence"]), nz(r["inventor_id"]),
             nz(r["disambig_inventor_name_first"]), nz(r["disambig_inventor_name_last"]), nz(r["gender_code"]),
             nz(r["location_id"])) for r in rows(bulk, "g_inventor_disambiguated")))
        log("  inv rows", n)
        st.execute("create index inv_pid on inv(patent_id)")
        st.execute("create index inv_iid on inv(inventor_id)")
    if "asg" not in have:
        log("stage g_assignee_disambiguated")
        st.execute("create table asg(patent_id text, seq int, assignee_id text, first text, last text, org text,"
                   " atype text, location_id text)")
        n = batched_insert(st, "insert into asg values (?,?,?,?,?,?,?,?)", (
            (r["patent_id"], to_int(r["assignee_sequence"]), nz(r["assignee_id"]),
             nz(r["disambig_assignee_individual_name_first"]), nz(r["disambig_assignee_individual_name_last"]),
             nz(r["disambig_assignee_organization"]), nz(r["assignee_type"]), nz(r["location_id"]))
            for r in rows(bulk, "g_assignee_disambiguated")))
        log("  asg rows", n)
        st.execute("create index asg_pid on asg(patent_id)")
        st.execute("create index asg_aid on asg(assignee_id)")
    if "att" not in have:
        log("stage g_attorney_disambiguated")
        st.execute("create table att(patent_id text, seq int, attorney_id text, first text, last text, org text,"
                   " country text)")
        n = batched_insert(st, "insert into att values (?,?,?,?,?,?,?)", (
            (r["patent_id"], to_int(r["attorney_sequence"]), nz(r["attorney_id"]),
             nz(r["disambig_attorney_name_first"]), nz(r["disambig_attorney_name_last"]),
             nz(r["disambig_attorney_organization"]), nz(r["attorney_country"]))
            for r in rows(bulk, "g_attorney_disambiguated")))
        log("  att rows", n)
        st.execute("create index att_pid on att(patent_id)")
        st.execute("create index att_aid on att(attorney_id)")
    if "loc" not in have:
        log("stage g_location_disambiguated")
        st.execute("create table loc(location_id text primary key, city text, state text, country text, lat real,"
                   " lon real, county text, state_fips text, county_fips text)")
        batched_insert(st, "insert or ignore into loc values (?,?,?,?,?,?,?,?,?)", (
            (r["location_id"], nz(r["disambig_city"]), nz(r["disambig_state"]), nz(r["disambig_country"]),
             to_float(r["latitude"]), to_float(r["longitude"]), nz(r["county"]), nz(r["state_fips"]),
             nz(r["county_fips"])) for r in rows(bulk, "g_location_disambiguated")))
    st.commit()
    return st


def pick_sample(st):
    """Sample ids: mod rule + fixed ids + name extras (limited per rule)."""
    ids = {r[0] for r in st.execute("select patent_id from p where smp=1")}
    base = len(ids)
    ids |= {r[0] for r in st.execute(
        "select patent_id from p where patent_id in (%s)" % ",".join("?" * len(FIXED_IDS)), FIXED_IDS)}
    # one sequential scan per staged table (no DISTINCT: it would walk the patent_id index with random reads)
    buckets = {}

    def take(tag, pid):
        b = buckets.setdefault(tag, [])
        if len(b) < EXTRA_LIMIT and pid not in b:
            b.append(pid)

    pairs = set(INV_PAIR_EXTRAS)
    for pid, first, last in st.execute("select patent_id, first, last from inv"):
        if last in INV_LAST_EXTRAS:
            take("last:" + last, pid)
        if (first, last) in pairs:
            take(f"pair:{first} {last}", pid)
        if first and any(s in first.lower() for s in INV_FIRST_CONTAINS):
            take("first~", pid)
    for pid, org in st.execute("select patent_id, org from asg"):
        if not org:
            continue
        if any(org.lower().startswith(p) for p in ASG_EXTRA_PREFIX):
            take("org^", pid)
        if org in ASG_EXTRA_EXACT:
            take("org=" + org, pid)
    buckets["withdrawn"] = [r[0] for r in st.execute("select patent_id from p where withdrawn=1 limit ?", (EXTRA_LIMIT,))]
    extras = set()
    for tag, b in buckets.items():
        log(f"  extras {tag}: {len(b)}")
        extras |= set(b)
    # the mod-rule and fixed ids were read from p, so only the extras (a few hundred ids from inv/asg) can
    # name a patent that p does not have; checking just those avoids one lookup per id on a full build
    ids |= {i for i in extras if st.execute("select 1 from p where patent_id=?", (i,)).fetchone()}
    log(f"sample: {base} by mod rule, {len(ids)} total")
    return ids


# ---------------------------------------------------------------- sample schema
T, I, R = "TEXT", "INTEGER", "REAL"
SCHEMA = {
    "patents": [("patent_id", T), ("patent_zero_prefix", T), ("patent_title", T), ("patent_type", T),
                ("patent_date", T), ("patent_year", I), ("patent_abstract", T), ("wipo_kind", T), ("withdrawn", I),
                ("gov_interest_statement", T), ("patent_earliest_application_date", T),
                ("patent_processing_days", I), ("patent_term_extension", I), ("patent_num_us_patents_cited", I),
                ("patent_num_times_cited_by_us_patents", I), ("patent_num_total_documents_cited", I),
                ("patent_num_foreign_documents_cited", I), ("patent_num_us_applications_cited", I),
                ("patent_detail_desc_length", I), ("patent_cpc_current_group_average_patent_processing_days", I),
                ("patent_uspc_current_mainclass_average_patent_processing_days", I)],
    "patents__inventors": [("inventor_id", T), ("inventor_name_first", T), ("inventor_name_last", T),
                           ("inventor_gender_code", T), ("inventor_location_id", T), ("inventor_city", T),
                           ("inventor_state", T), ("inventor_country", T), ("inventor_sequence", I)],
    "patents__assignees": [("assignee_id", T), ("assignee_type", T), ("assignee_individual_name_first", T),
                           ("assignee_individual_name_last", T), ("assignee_organization", T),
                           ("assignee_location_id", T), ("assignee_city", T), ("assignee_state", T),
                           ("assignee_country", T), ("assignee_sequence", I)],
    "patents__cpc_current": [("cpc_sequence", I), ("cpc_section", T), ("cpc_class", T), ("cpc_class_id", T),
                             ("cpc_subclass", T), ("cpc_subclass_id", T), ("cpc_group", T), ("cpc_group_id", T),
                             ("cpc_type", T)],
    "patents__application": [("application_id", T), ("application_type", T), ("filing_date", T),
                             ("series_code", T), ("rule_47_flag", I), ("filing_type", T)],
    "patents__wipo": [("wipo_field", T), ("wipo_field_id", T), ("wipo_sequence", I)],
    "patents__figures": [("num_figures", I), ("num_sheets", I)],
    "patents__us_term_of_grant": [("term_grant", T), ("term_extension", T), ("term_disclaimer", T),
                                  ("disclaimer_date", T)],
    "patents__botanic": [("latin_name", T), ("variety", T)],
    "patents__gov_interest_organizations": [("fedagency_name", T), ("level_one", T), ("level_two", T),
                                            ("level_three", T)],
    "patents__gov_interest_contract_award_numbers": [("award_number", T)],
    "patents__pct_data": [("published_filed_date", T), ("pct_102_date", T), ("pct_371_date", T),
                          ("application_kind", T), ("pct_doc_number", T), ("pct_doc_type", T)],
    "patents__foreign_priority": [("priority_claim_sequence", I), ("priority_claim_kind", T),
                                  ("foreign_application_id", T), ("filing_date", T), ("foreign_country_filed", T)],
    "inventors": [("inventor_id", T), ("inventor_name_first", T), ("inventor_name_last", T),
                  ("inventor_gender_code", T), ("inventor_lastknown_city", T), ("inventor_lastknown_state", T),
                  ("inventor_lastknown_country", T), ("inventor_lastknown_latitude", R),
                  ("inventor_lastknown_longitude", R), ("inventor_lastknown_location", T),
                  ("inventor_first_seen_date", T), ("inventor_last_seen_date", T), ("inventor_num_assignees", I),
                  ("inventor_num_patents", I), ("inventor_years_active", I)],
    "inventors__inventor_years": [("year", I), ("num_patents", I)],
    "assignees": [("assignee_id", T), ("assignee_individual_name_first", T), ("assignee_individual_name_last", T),
                  ("assignee_organization", T), ("assignee_type", T), ("assignee_lastknown_city", T),
                  ("assignee_lastknown_state", T), ("assignee_lastknown_country", T),
                  ("assignee_lastknown_latitude", R), ("assignee_lastknown_longitude", R),
                  ("assignee_lastknown_location", T), ("assignee_first_seen_date", T),
                  ("assignee_last_seen_date", T), ("assignee_num_inventors", I), ("assignee_num_patents", I),
                  ("assignee_years_active", I)],
    "assignees__assignee_years": [("year", I), ("num_patents", I)],
    "locations": [("location_id", T), ("location_name", T), ("location_county", T), ("location_county_fips", T),
                  ("location_state", T), ("location_state_fips", T), ("location_country", T),
                  ("location_place_type", T), ("location_latitude", R), ("location_longitude", R),
                  ("location_num_assignees", I), ("location_num_patents", I), ("location_num_inventors", I)],
    "us_patent_citations": [("uuid", T), ("patent_id", T), ("patent", T), ("patent_zero_prefix", T),
                            ("citation_sequence", I), ("citation_patent_id", T), ("citation_patent", T),
                            ("citation_wipo_kind", T), ("citation_category", T), ("citation_date", T),
                            ("citation_name", T)],
    # ---- added with the second full build (gates 1.2 to 1.4): the remaining nested groups of the patents index
    "patents__cpc_at_issue": [("cpc_sequence", I), ("cpc_section", T), ("cpc_class", T), ("cpc_class_id", T),
                              ("cpc_subclass", T), ("cpc_subclass_id", T), ("cpc_group", T), ("cpc_group_id", T),
                              ("cpc_type", T)],
    "patents__ipcr": [("ipc_id", T), ("ipc_sequence", I), ("ipc_action_date", T), ("ipc_section", T),
                      ("ipc_class", T), ("ipc_subclass", T), ("ipc_main_group", T), ("ipc_subgroup", T),
                      ("ipc_symbol_position", T), ("ipc_classification_data_source", T),
                      ("ipc_classification_value", T)],
    "patents__uspc_at_issue": [("uspc_mainclass", T), ("uspc_mainclass_id", T), ("uspc_subclass", T),
                               ("uspc_subclass_id", T), ("uspc_sequence", I)],
    "patents__examiners": [("examiner_id", T), ("examiner_first_name", T), ("examiner_last_name", T),
                           ("examiner_role", T), ("art_group", T)],
    "patents__applicants": [("applicant_designation", T), ("applicant_name_first", T), ("applicant_name_last", T),
                            ("applicant_organization", T), ("applicant_sequence", I), ("applicant_type", T),
                            ("location_id", T)],
    "patents__us_related_documents": [("related_doc_type", T), ("related_doc_kind", T), ("related_doc_number", T),
                                      ("published_country", T), ("related_doc_published_date", T),
                                      ("related_doc_status", T), ("related_doc_sequence", I), ("wipo_kind", T)],
    "patents__attorneys": [("attorney_id", T), ("attorney_sequence", I), ("attorney_name_first", T),
                           ("attorney_name_last", T), ("attorney_organization", T)],
    # ---- and the standalone indices those files serve
    "attorneys": [("attorney_id", T), ("attorney_name_first", T), ("attorney_name_last", T),
                  ("attorney_organization", T), ("attorney_first_seen_date", T), ("attorney_last_seen_date", T),
                  ("attorney_num_inventors", I), ("attorney_num_patents", I), ("attorney_years_active", I)],
    "us_application_citations": [("uuid", T), ("patent_id", T), ("patent", T), ("patent_zero_prefix", T),
                                 ("citation_sequence", I), ("citation_document_number", T),
                                 ("citation_wipo_kind", T), ("citation_category", T), ("citation_date", T),
                                 ("citation_name", T)],
    "foreign_citations": [("uuid", T), ("patent_id", T), ("patent", T), ("patent_zero_prefix", T),
                          ("citation_number", T), ("citation_sequence", I), ("citation_date", T),
                          ("citation_category", T), ("citation_country", T)],
    # reference_sequence is a keyword in the ES schema, so it is stored as text (sorted as a string, like upstream)
    "other_references": [("uuid", T), ("patent_id", T), ("patent", T), ("patent_zero_prefix", T),
                         ("reference_sequence", T), ("reference_text", T)],
    "rel_app_text": [("uuid", T), ("patent_id", T), ("patent_zero_prefix", T), ("related_text", T)],
    "cpc_groups": [("cpc_class_id", T), ("cpc_class", T), ("cpc_subclass_id", T), ("cpc_subclass", T),
                   ("cpc_group_id", T), ("cpc_group_title", T)],
    "uspc_subclasses": [("uspc_mainclass_id", T), ("uspc_mainclass", T), ("uspc_subclass_id", T),
                        ("uspc_subclass_title", T)],
    "ipcr": [("ipc_id", T), ("ipc_section", T), ("ipc_class", T), ("ipc_subclass", T)],
}
KEYS = {"patents": "patent_id", "inventors": "inventor_id", "assignees": "assignee_id",
        "locations": "location_id", "us_patent_citations": "uuid", "attorneys": "attorney_id",
        "us_application_citations": "uuid", "foreign_citations": "uuid", "other_references": "uuid",
        "rel_app_text": "uuid", "cpc_groups": "cpc_group_id", "uspc_subclasses": "uspc_subclass_id",
        "ipcr": "ipc_id"}
# g_persistent_* are loaded as shipped (one column per disambiguation release) into these tables; the columns come
# from the file header, so they are not in SCHEMA. No endpoint reads them; gate 1.2 names them.
PERSISTENT = {"g_persistent_inventor": "persistent_inventors", "g_persistent_assignee": "persistent_assignees"}


def create_schema(out):
    for t, cols in SCHEMA.items():
        extra = ""
        if "__" in t:
            extra = "_pid TEXT NOT NULL, _ord INTEGER, "
        pk = KEYS.get(t)
        coldefs = ", ".join(f'"{c}" {ty}' + (" PRIMARY KEY" if c == pk else "") for c, ty in cols)
        out.execute(f'CREATE TABLE "{t}" ({extra}{coldefs})')


def ins_sql(table):
    cols = SCHEMA[table]
    names = ([] if "__" not in table else ["_pid", "_ord"]) + [c for c, _ in cols]
    return f'INSERT INTO "{table}" ({", ".join(chr(34) + n + chr(34) for n in names)}) VALUES ({",".join("?" * len(names))})'


def ins(out, table, recs):
    return batched_insert(out, ins_sql(table), recs, 50000)


# ---------------------------------------------------------------- fill
def days_between(a, b):
    import datetime as dt
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def valid_date(d):
    return d if d and re.match(r"^(1[7-9]|20)\d\d-\d\d-\d\d$", d) else None


def stage_sample(st, S):
    """Put the sample ids in temp.s on the stage connection; every stage query joins against it."""
    st.execute("create temp table s(patent_id text primary key)")
    st.executemany("insert into temp.s values (?)", ((i,) for i in S))
    st.commit()


def fill_patents(out, st, bulk):
    """Base patent rows go in first, straight from the stage join; the per-file columns (abstract, gov interest,
    earliest application date, term extension, citation counts, processing days) are applied afterwards with
    keyed UPDATEs. Nothing here holds a whole table in memory: the only per-patent state is pos (patent_id ->
    position) and two int arrays of citation counts, so a full-corpus build stays in a few GB.

    Returns pos, which doubles as the membership test for the child fills."""
    cols = [c for c, _ in SCHEMA["patents"]]
    ci = {c: i for i, c in enumerate(cols)}
    pos = {}

    def base_rows():
        # same join as before, so rowid order in patents is unchanged
        for pid, typ, date, title, wk, wd in st.execute(
                "select p.patent_id, patent_type, patent_date, patent_title, wipo_kind, withdrawn from p join temp.s using(patent_id)"):
            pos[pid] = len(pos)
            row = [None] * len(cols)
            row[ci["patent_id"]] = pid
            row[ci["patent_zero_prefix"]] = zero_prefix(pid)
            row[ci["patent_title"]] = title
            row[ci["patent_type"]] = typ
            row[ci["patent_date"]] = date
            row[ci["patent_year"]] = int(date[:4]) if date else None
            row[ci["wipo_kind"]] = wk
            row[ci["withdrawn"]] = wd
            yield tuple(row)

    ins(out, "patents", base_rows())
    log("patents base", len(pos))
    log("abstracts")
    batched_insert(out, "UPDATE patents SET patent_abstract=? WHERE patent_id=?", (
        (nz(r["patent_abstract"]), r["patent_id"]) for r in rows(bulk, "g_patent_abstract") if r["patent_id"] in pos))
    log("gov interest")
    batched_insert(out, "UPDATE patents SET gov_interest_statement=? WHERE patent_id=?", (
        (nz(r["gi_statement"]), r["patent_id"]) for r in rows(bulk, "g_gov_interest") if r["patent_id"] in pos))
    log("applications")
    app = Batcher(out, ins_sql("patents__application"))
    # keep the smallest valid filing date per patent (same rule as the old in-memory min)
    earliest = Batcher(out, "UPDATE patents SET patent_earliest_application_date=? WHERE patent_id=?"
                            " AND (patent_earliest_application_date IS NULL OR patent_earliest_application_date > ?)")
    for r in rows(bulk, "g_application"):
        pid = r["patent_id"]
        if pid in pos:
            fd = nz(r["filing_date"])
            app.add((pid, app.total + len(app.buf), nz(r["application_id"]), nz(r["patent_application_type"]), fd,
                     nz(r["series_code"]), 1 if r["rule_47_flag"] in ("1", "TRUE", "true") else 0,
                     nz(r["patent_application_type"])))
            v = valid_date(fd)
            if v:
                earliest.add((v, pid, v))
    app.close()
    earliest.close()
    log("term of grant")
    tog = Batcher(out, ins_sql("patents__us_term_of_grant"))
    ext = Batcher(out, "UPDATE patents SET patent_term_extension=? WHERE patent_id=?")
    for r in rows(bulk, "g_us_term_of_grant"):
        pid = r["patent_id"]
        if pid in pos:
            tog.add((pid, tog.total + len(tog.buf), nz(r["term_grant"]), nz(r["term_extension"]),
                     nz(r["term_disclaimer"]), nz(r["disclaimer_date"])))
            if to_int(r["term_extension"]) is not None:
                ext.add((to_int(r["term_extension"]), pid))
    tog.close()
    ext.close()
    log("us patent citations (large)")
    cited = array("i", [0]) * len(pos)
    cited_by = array("i", [0]) * len(pos)

    def cit_rows():
        n = 0
        for r in rows(bulk, "g_us_patent_citation"):
            n += 1
            pid, cpid = r["patent_id"], r["citation_patent_id"]
            i = pos.get(pid)
            if i is not None:
                cited[i] += 1
                seq = to_int(r["citation_sequence"])
                yield (f"{pid}-{seq}", pid, pid, zero_prefix(pid), seq, nz(cpid), nz(cpid), nz(r["wipo_kind"]),
                       nz(r["citation_category"]), nz(r["citation_date"]), nz(r["record_name"]))
            j = pos.get(cpid)
            if j is not None:
                cited_by[j] += 1
            if n % 20000000 == 0:
                log("   citation rows scanned", n)

    batched_insert(out, "INSERT OR IGNORE INTO us_patent_citations VALUES (?,?,?,?,?,?,?,?,?,?,?)", cit_rows())
    log("us application citations")
    cited_app = array("i", [0]) * len(pos)

    def app_rows():
        for r in rows(bulk, "g_us_application_citation"):
            pid = r["patent_id"]
            i = pos.get(pid)
            if i is not None:
                cited_app[i] += 1
                seq = to_int(r["citation_sequence"])
                yield (f"{pid}-{seq}", pid, pid, zero_prefix(pid), seq, nz(r["citation_document_number"]),
                       nz(r["wipo_kind"]), nz(r["citation_category"]), nz(r["citation_date"]), nz(r["record_name"]))

    batched_insert(out, "INSERT OR IGNORE INTO us_application_citations VALUES (?,?,?,?,?,?,?,?,?,?)", app_rows())
    log("foreign citations")
    cited_foreign = array("i", [0]) * len(pos)

    def foreign_rows():
        for r in rows(bulk, "g_foreign_citation"):
            pid = r["patent_id"]
            i = pos.get(pid)
            if i is not None:
                cited_foreign[i] += 1
                seq = to_int(r["citation_sequence"])
                yield (f"{pid}-{seq}", pid, pid, zero_prefix(pid), nz(r["citation_application_id"]), seq,
                       nz(r["citation_date"]), nz(r["citation_category"]), nz(r["citation_country"]))

    batched_insert(out, "INSERT OR IGNORE INTO foreign_citations VALUES (?,?,?,?,?,?,?,?,?)", foreign_rows())
    log("citation counts + processing days")
    # patent_num_total_documents_cited = us patents + us applications + foreign documents, as in the upstream
    # reporting database (PatentsView-DB 02_Patent.sql); other references are not counted
    for chunk in scan_chunks(out, "select rowid, patent_id, patent_earliest_application_date, patent_date"
                                  " from patents where rowid > ? order by rowid limit ?"):
        out.executemany("UPDATE patents SET patent_num_us_patents_cited=?, patent_num_times_cited_by_us_patents=?,"
                        " patent_processing_days=?, patent_num_us_applications_cited=?,"
                        " patent_num_foreign_documents_cited=?, patent_num_total_documents_cited=? WHERE patent_id=?",
                        [(cited[pos[pid]], cited_by[pos[pid]], days_between(ea, date), cited_app[pos[pid]],
                          cited_foreign[pos[pid]], cited[pos[pid]] + cited_app[pos[pid]] + cited_foreign[pos[pid]], pid)
                         for _, pid, ea, date in chunk])
    out.commit()
    return pos


def fill_children(out, st, pos, bulk):
    log("nested inventors/assignees from stage")
    ins(out, "patents__inventors", (
        (r[0], k, *r[2:]) for k, r in enumerate(st.execute(
            "select i.patent_id, i.seq, i.inventor_id, i.first, i.last, i.gender, i.location_id, l.city, l.state,"
            " l.country, i.seq from inv i join temp.s using(patent_id) left join loc l on l.location_id=i.location_id"
            " order by i.patent_id, i.seq"))))
    ins(out, "patents__assignees", (
        (r[0], k, *r[2:]) for k, r in enumerate(st.execute(
            "select a.patent_id, a.seq, a.assignee_id, a.atype, a.first, a.last, a.org, a.location_id, l.city,"
            " l.state, l.country, a.seq from asg a join temp.s using(patent_id)"
            " left join loc l on l.location_id=a.location_id order by a.patent_id, a.seq"))))

    def stream(name, table, fn):
        log(name)

        def recs():
            k = 0
            for r in rows(bulk, name):
                if r["patent_id"] in pos:
                    yield (r["patent_id"], k, *fn(r))
                    k += 1

        ins(out, table, recs())

    stream("g_cpc_current", "patents__cpc_current", lambda r: (
        to_int(r["cpc_sequence"]), nz(r["cpc_section"]), nz(r["cpc_class"]), nz(r["cpc_class"]),
        nz(r["cpc_subclass"]), nz(r["cpc_subclass"]), nz(r["cpc_group"]), nz(r["cpc_group"]), nz(r["cpc_type"])))
    stream("g_wipo_technology", "patents__wipo", lambda r: (
        nz(r["wipo_field_id"]), nz(r["wipo_field_id"]), to_int(r["wipo_field_sequence"])))
    stream("g_figures", "patents__figures", lambda r: (to_int(r["num_figures"]), to_int(r["num_sheets"])))
    stream("g_botanic", "patents__botanic", lambda r: (nz(r["latin_name"]), nz(r["plant_variety"])))
    stream("g_gov_interest_org", "patents__gov_interest_organizations", lambda r: (
        nz(r["fedagency_name"]), nz(r["level_one"]), nz(r["level_two"]), nz(r["level_three"])))
    stream("g_gov_interest_contracts", "patents__gov_interest_contract_award_numbers",
           lambda r: (nz(r["contract_award_number"]),))
    stream("g_pct_data", "patents__pct_data", lambda r: (
        nz(r["published_or_filed_date"]), nz(r["pct_102_date"]), nz(r["pct_371_date"]), nz(r["application_kind"]),
        nz(r["pct_doc_number"]), nz(r["pct_doc_type"])))
    stream("g_foreign_priority", "patents__foreign_priority", lambda r: (
        to_int(r["priority_claim_sequence"]), nz(r["priority_claim_kind"]), nz(r["foreign_application_id"]),
        nz(r["filing_date"]), nz(r["foreign_country_filed"])))
    # ---- groups added with the second full build
    log("nested attorneys from stage")
    ins(out, "patents__attorneys", (
        (r[0], k, *r[2:]) for k, r in enumerate(st.execute(
            "select a.patent_id, a.seq, a.attorney_id, a.seq, a.first, a.last, a.org from att a join temp.s using(patent_id)"
            " order by a.patent_id, a.seq"))))
    stream("g_cpc_at_issue", "patents__cpc_at_issue", lambda r: (
        to_int(r["cpc_sequence"]), nz(r["cpc_section"]), nz(r["cpc_class"]), nz(r["cpc_class"]),
        nz(r["cpc_subclass"]), nz(r["cpc_subclass"]), nz(r["cpc_group"]), nz(r["cpc_group"]), nz(r["cpc_type"])))
    def stream_all(name, table, fn):
        """Like stream, but the collector in fn sees every row of the file, not only the sampled patents."""
        log(name)

        def recs():
            k = 0
            for r in rows(bulk, name):
                rec = fn(r)
                if r["patent_id"] in pos:
                    yield (r["patent_id"], k, *rec)
                    k += 1

        ins(out, table, recs())

    # ipc_id is section + class + subclass (the three columns the upstream ipcr index carries, e.g. G01S); the bulk
    # file has no symbol_position column, so ipc_symbol_position stays null. The ipcr index is the distinct ids.
    ipcr_ids = {}

    def ipc(r):
        sec, cls, sub = nz(r["section"]), nz(r["ipc_class"]), nz(r["subclass"])
        iid = f"{sec}{cls}{sub}" if sec and cls and sub else None
        if iid and iid not in ipcr_ids:
            ipcr_ids[iid] = (sec, cls, sub)
        return (iid, to_int(r["ipc_sequence"]), nz(r["action_date"]), sec, cls, sub, nz(r["main_group"]),
                nz(r["subgroup"]), None, nz(r["classification_data_source"]), nz(r["classification_value"]))

    stream_all("g_ipc_at_issue", "patents__ipcr", ipc)
    ins(out, "ipcr", ((iid, *v) for iid, v in sorted(ipcr_ids.items())))
    log("  ipcr ids", len(ipcr_ids))
    del ipcr_ids
    # uspc_subclasses (id, mainclass, title) is the distinct subclasses seen in g_uspc_at_issue, first title wins
    uspc_sub = {}

    def uspc(r):
        mid, sid = nz(r["uspc_mainclass_id"]), nz(r["uspc_subclass_id"])
        if sid and sid not in uspc_sub:
            uspc_sub[sid] = (mid, mid, sid, nz(r["uspc_subclass_title"]))
        return (mid, mid, sid, sid, to_int(r["uspc_sequence"]))

    stream_all("g_uspc_at_issue", "patents__uspc_at_issue", uspc)
    ins(out, "uspc_subclasses", (v for _, v in sorted(uspc_sub.items())))
    log("  uspc subclasses", len(uspc_sub))
    del uspc_sub
    # examiner_id: the bulk file carries no examiner id (upstream serves its own persistent_examiner_id), so null
    stream("g_examiner_not_disambiguated", "patents__examiners", lambda r: (
        None, nz(r["raw_examiner_name_first"]), nz(r["raw_examiner_name_last"]), nz(r["examiner_role"]),
        nz(r["art_group"])))
    # location_id: the bulk file has rawlocation_id, which is not an id in g_location_disambiguated, so null
    stream("g_applicant_not_disambiguated", "patents__applicants", lambda r: (
        nz(r["applicant_designation"]), nz(r["raw_applicant_name_first"]), nz(r["raw_applicant_name_last"]),
        nz(r["raw_applicant_organization"]), to_int(r["applicant_sequence"]), nz(r["applicant_type"]), None))
    stream("g_us_rel_doc", "patents__us_related_documents", lambda r: (
        nz(r["related_doc_type"]), nz(r["related_doc_kind"]), nz(r["related_doc_number"]),
        nz(r["published_country"]), nz(r["related_doc_published_date"]), nz(r["related_doc_status"]),
        to_int(r["related_doc_sequence"]), nz(r["wipo_kind"])))
    log("cpc_groups from g_cpc_title")
    batched_insert(out, "INSERT OR IGNORE INTO cpc_groups VALUES (?,?,?,?,?,?)", (
        (nz(r["cpc_class"]), nz(r["cpc_class"]), nz(r["cpc_subclass"]), nz(r["cpc_subclass"]), nz(r["cpc_group"]),
         nz(r["cpc_group_title"])) for r in rows(bulk, "g_cpc_title") if nz(r["cpc_group"])))


def fill_text_endpoints(out, pos, bulk):
    """other_references and rel_app_text: one document per source row, keyed uuid = patent_id-<sequence>."""
    log("other references (large)")

    def oref_rows():
        n = 0
        for r in rows(bulk, "g_other_reference"):
            n += 1
            pid = r["patent_id"]
            if pid in pos:
                seq = nz(r["other_reference_sequence"])
                yield (f"{pid}-{seq}", pid, pid, zero_prefix(pid), seq, nz(r["other_reference_text"]))
            if n % 20000000 == 0:
                log("   other reference rows scanned", n)

    batched_insert(out, "INSERT OR IGNORE INTO other_references VALUES (?,?,?,?,?,?)", oref_rows())
    log("rel app text")
    seen = array("i", [0]) * len(pos)

    def rat_rows():
        for r in rows(bulk, "g_rel_app_text"):
            pid = r["patent_id"]
            i = pos.get(pid)
            if i is not None:
                k = seen[i]
                seen[i] += 1
                yield (f"{pid}-{k}", pid, zero_prefix(pid), nz(r["rel_app_text"]))

    batched_insert(out, "INSERT OR IGNORE INTO rel_app_text VALUES (?,?,?,?)", rat_rows())


def fill_persistent(out, pos, bulk):
    """g_persistent_inventor / g_persistent_assignee as shipped: every column of the header, text, no endpoint."""
    for name, table in PERSISTENT.items():
        p = Path(bulk) / f"{name}.tsv.zip"
        if not p.exists():
            log("  (missing)", p.name)
            continue
        log(name)
        zf = zipfile.ZipFile(p)
        with zf.open(zf.namelist()[0]) as f:
            head = next(csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""), delimiter="\t"))
        cols = ", ".join(f'"{c}" ' + ("INTEGER" if c.endswith("_sequence") else "TEXT") for c in head)
        out.execute(f'CREATE TABLE "{table}" ({cols})')
        sql = f'INSERT INTO "{table}" VALUES ({",".join("?" * len(head))})'
        n = batched_insert(out, sql, (
            tuple(to_int(r[c]) if c.endswith("_sequence") else nz(r[c]) for c in head)
            for r in rows(bulk, name) if r["patent_id"] in pos))
        log(f"  {table} rows", n)
        out.execute(f'CREATE INDEX "ix_{table}_patent_id" ON "{table}"("patent_id")')
        out.commit()


def fill_entity(out, st, kind):
    """kind = 'inventor' | 'assignee': one row per disambiguated entity seen in the sample, stats over the full corpus."""
    src = "inv" if kind == "inventor" else "asg"
    other = "asg" if kind == "inventor" else "inv"
    oid = "assignee_id" if kind == "inventor" else "inventor_id"
    idcol = f"{kind}_id"
    log(f"entity {kind}s")
    st.execute(f"drop table if exists temp.e_{kind}")
    st.execute(f"create temp table e_{kind}(id text primary key)")
    st.executemany(f"insert or ignore into temp.e_{kind} values (?)",
                   out.execute(f'select distinct {idcol} from "patents__{kind}s" where {idcol} is not null'))
    others = dict(st.execute(
        f"select x.{idcol}, count(distinct o.{oid}) from {src} x join temp.e_{kind} e on e.id=x.{idcol}"
        f" join {other} o on o.patent_id=x.patent_id group by x.{idcol}"))
    if kind == "inventor":
        q = ("select x.inventor_id, x.patent_id, p.patent_date, x.first, x.last, x.gender, x.location_id,"
             " l.city, l.state, l.country, l.lat, l.lon from inv x join temp.e_inventor e on e.id=x.inventor_id"
             " join p on p.patent_id=x.patent_id left join loc l on l.location_id=x.location_id"
             " order by x.inventor_id, p.patent_date, x.patent_id")
    else:
        q = ("select x.assignee_id, x.patent_id, p.patent_date, x.first, x.last, x.org, x.atype, x.location_id,"
             " l.city, l.state, l.country, l.lat, l.lon from asg x join temp.e_assignee e on e.id=x.assignee_id"
             " join p on p.patent_id=x.patent_id left join loc l on l.location_id=x.location_id"
             " order by x.assignee_id, p.patent_date, x.patent_id")
    # one ordered scan feeds both tables; each entity is written as soon as its group is complete
    ents = Batcher(out, ins_sql(f"{kind}s"))
    years = Batcher(out, ins_sql(f"{kind}s__{kind}_years"))
    cur, grp = None, []

    def flush():
        if not grp:
            return
        pats = {g[1] for g in grp}
        dates = [g[2] for g in grp if g[2]]
        yc = {}
        seen = set()
        for g in grp:
            if g[2] and g[1] not in seen:
                seen.add(g[1])
                yc[int(g[2][:4])] = yc.get(int(g[2][:4]), 0) + 1
        last = grp[-1]
        if kind == "inventor":
            ents.add((cur, last[3], last[4], last[5], last[7], last[8], last[9], last[10], last[11], last[6],
                      min(dates) if dates else None, max(dates) if dates else None, others.get(cur, 0),
                      len(pats), len(yc)))
        else:
            ents.add((cur, last[3], last[4], last[5], last[6], last[8], last[9], last[10], last[11], last[12],
                      last[7], min(dates) if dates else None, max(dates) if dates else None,
                      others.get(cur, 0), len(pats), len(yc)))
        for k, (y, c) in enumerate(sorted(yc.items())):
            years.add((cur, k, y, c))

    for r in st.execute(q):
        if r[0] != cur:
            flush()
            cur, grp = r[0], []
        grp.append(r)
    flush()
    n_ents = ents.close()
    years.close()
    log(f"  {n_ents} {kind}s")


def fill_locations(out, st):
    log("locations (counts over full corpus)")
    npat = dict(st.execute("select location_id, count(distinct patent_id) from (select location_id, patent_id from inv"
                           " union all select location_id, patent_id from asg) where location_id is not null group by 1"))
    ninv = dict(st.execute("select location_id, count(distinct inventor_id) from inv where location_id is not null group by 1"))
    nasg = dict(st.execute("select location_id, count(distinct assignee_id) from asg where location_id is not null group by 1"))
    ins(out, "locations", (
        (lid, city, county, cfips, state, sfips, country, None, lat, lon, nasg.get(lid, 0), npat.get(lid, 0),
         ninv.get(lid, 0))
        for lid, city, state, country, lat, lon, county, sfips, cfips in st.execute("select * from loc")))


def fill_attorneys(out, st):
    """One row per disambiguated attorney seen in the sample, stats over the full corpus (same shape as fill_entity:
    names from the latest patent, first/last seen from patent dates, years_active = distinct grant years)."""
    log("entity attorneys")
    st.execute("drop table if exists temp.e_att")
    st.execute("create temp table e_att(id text primary key)")
    st.executemany("insert or ignore into temp.e_att values (?)",
                   out.execute('select distinct attorney_id from "patents__attorneys" where attorney_id is not null'))
    ninv = dict(st.execute(
        "select x.attorney_id, count(distinct i.inventor_id) from att x join temp.e_att e on e.id=x.attorney_id"
        " join inv i on i.patent_id=x.patent_id group by x.attorney_id"))
    q = ("select x.attorney_id, x.patent_id, p.patent_date, x.first, x.last, x.org from att x"
         " join temp.e_att e on e.id=x.attorney_id join p on p.patent_id=x.patent_id"
         " order by x.attorney_id, p.patent_date, x.patent_id")
    ents = Batcher(out, ins_sql("attorneys"))
    cur, grp = None, []

    def flush():
        if not grp:
            return
        pats = {g[1] for g in grp}
        dates = [g[2] for g in grp if g[2]]
        years = {d[:4] for d in dates}
        last = grp[-1]
        ents.add((cur, last[3], last[4], last[5], min(dates) if dates else None, max(dates) if dates else None,
                  ninv.get(cur, 0), len(pats), len(years)))

    for r in st.execute(q):
        if r[0] != cur:
            flush()
            cur, grp = r[0], []
        grp.append(r)
    flush()
    log(f"  {ents.close()} attorneys")


SCHEMA_FILES = {"patents": "granted/patents_fields.json", "inventors": "granted/inventors_fields.json",
                "assignees": "granted/assignees_fields.json", "locations": "granted/locations_fields.json",
                "us_patent_citations": "granted/us_patent_citations_fields.json",
                "attorneys": "granted/attorneys_fields.json",
                "us_application_citations": "granted/us_application_citations_fields.json",
                "foreign_citations": "granted/foreign_citations_fields.json",
                "other_references": "granted/other_references_fields.json",
                "rel_app_text": "granted/rel_app_text_fields.json", "cpc_groups": "granted/cpc_groups_fields.json",
                "uspc_subclasses": "granted/uspc_subclasses_fields.json", "ipcr": "granted/ipcr_fields.json"}
FTS_TOKENIZE = "unicode61 remove_diacritics 0 tokenchars '_'"


def fill_meta_and_fts(out, esdl):
    log("meta + fts")
    out.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    out.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    base = Path(esdl) / "src" / "es_data_load" / "pv" / "schemas"
    text_cols = {}
    for idx, rel in SCHEMA_FILES.items():
        out.execute("INSERT INTO _lapse_indices VALUES (?,?,?)", (idx, idx, KEYS[idx]))
        props = json.load(open(base / rel, encoding="utf-8"))
        for k, v in props.items():
            if "properties" in v:
                for ck, cv in v["properties"].items():
                    out.execute("INSERT INTO _lapse_fields VALUES (?,?,?,?,?)",
                                (idx, k, ck, cv.get("type"), 1 if "keyword" in cv.get("fields", {}) else 0))
                    if cv.get("type") == "text":
                        text_cols.setdefault(f"{idx}__{k}", []).append(ck)
            else:
                out.execute("INSERT INTO _lapse_fields VALUES (?,?,?,?,?)",
                            (idx, "", k, v.get("type"), 1 if "keyword" in v.get("fields", {}) else 0))
                if v.get("type") == "text":
                    text_cols.setdefault(idx, []).append(k)
    out.commit()
    for t, cols in text_cols.items():
        if t not in SCHEMA:
            continue
        have = {c for c, _ in SCHEMA[t]}
        cols = [c for c in cols if c in have]
        if not cols:
            continue
        out.execute(f'CREATE VIRTUAL TABLE "fts_{t}" USING fts5({", ".join(cols)}, content="{t}", '
                    f"content_rowid='rowid', tokenize=\"{FTS_TOKENIZE}\")")
        out.execute(f"INSERT INTO \"fts_{t}\"(\"fts_{t}\") VALUES ('rebuild')")
        out.commit()
        log("  fts", t, cols)


INDEXES = {
    "patents": ["patent_date", "patent_type", "patent_year", "patent_zero_prefix", "withdrawn"],
    "patents__inventors": ["_pid", "inventor_id", "inventor_name_last", "inventor_name_first", "inventor_city"],
    "patents__assignees": ["_pid", "assignee_id", "assignee_organization", "assignee_city"],
    "patents__cpc_current": ["_pid", "cpc_section", "cpc_subclass_id", "cpc_group_id"],
    "inventors": ["inventor_name_last", "inventor_name_first"],
    "assignees": ["assignee_organization"],
    "locations": ["location_name", "location_country"],
    "us_patent_citations": ["patent_id", "citation_patent_id", "patent_zero_prefix"],
    "patents__cpc_at_issue": ["_pid", "cpc_section", "cpc_subclass_id", "cpc_group_id"],
    "patents__ipcr": ["_pid", "ipc_id"],
    "patents__uspc_at_issue": ["_pid", "uspc_mainclass_id", "uspc_subclass_id"],
    "patents__examiners": ["_pid", "examiner_last_name"],
    "patents__applicants": ["_pid", "applicant_organization"],
    "patents__us_related_documents": ["_pid", "related_doc_number"],
    "patents__attorneys": ["_pid", "attorney_id", "attorney_name_last", "attorney_organization"],
    "attorneys": ["attorney_name_last", "attorney_organization"],
    "us_application_citations": ["patent_id", "citation_document_number", "patent_zero_prefix"],
    "foreign_citations": ["patent_id", "citation_number", "patent_zero_prefix"],
    "other_references": ["patent_id", "patent_zero_prefix"],
    "rel_app_text": ["patent_id", "patent_zero_prefix"],
    "cpc_groups": ["cpc_subclass_id"],
    "uspc_subclasses": ["uspc_mainclass_id"],
}


def make_indexes(out):
    log("indexes")
    for t in SCHEMA:
        cols = INDEXES.get(t, ["_pid"] if "__" in t else [])
        for c in cols:
            out.execute(f'CREATE INDEX "ix_{t}_{c}" ON "{t}"("{c}")')
    out.execute("ANALYZE")
    out.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk", default=r"C:\LapseAPI\bulk")
    ap.add_argument("--out", default=r"C:\LapseAPI\data\sample.db")
    ap.add_argument("--stage", default=r"C:\LapseAPI\data\stage.db")
    ap.add_argument("--es-data-load", default=r"C:\LapseAPI\es-data-load")
    ap.add_argument("--mod", type=int, default=94)
    a = ap.parse_args()
    st = build_stage(a.bulk, a.stage, a.mod)
    S = pick_sample(st)
    tmp = a.out + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    out = sqlite3.connect(tmp)
    out.execute("pragma journal_mode=off")
    out.execute("pragma synchronous=off")
    create_schema(out)
    stage_sample(st, S)
    del S  # temp.s carries the sample from here on; pos (below) is the in-memory membership test
    pos = fill_patents(out, st, a.bulk)
    fill_children(out, st, pos, a.bulk)
    fill_text_endpoints(out, pos, a.bulk)
    fill_persistent(out, pos, a.bulk)
    del pos
    fill_entity(out, st, "inventor")
    fill_entity(out, st, "assignee")
    fill_attorneys(out, st)
    fill_locations(out, st)
    fill_meta_and_fts(out, a.es_data_load)
    make_indexes(out)
    tables = list(SCHEMA) + [t for t in PERSISTENT.values()
                             if out.execute("select 1 from sqlite_master where name=?", (t,)).fetchone()]
    counts = {t: out.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
    out.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    out.executemany("INSERT INTO _lapse_build VALUES (?,?)",
                    [("built_at", time.strftime("%Y-%m-%d %H:%M:%S")), ("mod", str(a.mod)),
                     ("row_counts", json.dumps(counts))])
    out.commit()
    out.close()
    st.close()
    if os.path.exists(a.out):
        os.remove(a.out)
    os.replace(tmp, a.out)
    for t, n in counts.items():
        log(f"  {t:48s} {n}")
    log("BUILD_DONE")


if __name__ == "__main__":
    main()
