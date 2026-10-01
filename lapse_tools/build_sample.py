"""Build a SQLite sample database shaped like the PatentSearch ES indices.

  py build_sample.py --bulk C:\\LapseAPI\\bulk --out C:\\LapseAPI\\data\\sample.db
       [--stage C:\\LapseAPI\\data\\stage.db] [--mod 94] [--es-data-load C:\\LapseAPI\\es-data-load]

Inputs are the PatentsView PVGPATDIS bulk zips (g_patent, g_patent_abstract, g_application,
g_inventor_disambiguated, g_assignee_disambiguated, g_location_disambiguated, g_cpc_current,
g_us_patent_citation, g_wipo_technology, g_figures, g_us_term_of_grant, g_botanic, g_gov_interest*,
g_pct_data, g_foreign_priority). Missing optional files are skipped.

Layout (read by API/search_sqlite.py):
  <index>                      one row per ES document, columns named like the API fields
  <index>__<nested_path>       one row per nested object; _pid = parent key, _ord = order
  fts_<table>                  FTS5 index over the ES "text" fields of that table
  _lapse_fields                index, path, field, es_type, keyword_subfield (from es-data-load schemas)
  _lapse_indices               index, table, key_field
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
    for tag, b in buckets.items():
        log(f"  extras {tag}: {len(b)}")
        ids |= set(b)
    ids = {i for i in ids if st.execute("select 1 from p where patent_id=?", (i,)).fetchone()}
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
}
KEYS = {"patents": "patent_id", "inventors": "inventor_id", "assignees": "assignee_id",
        "locations": "location_id", "us_patent_citations": "uuid"}


def create_schema(out):
    for t, cols in SCHEMA.items():
        extra = ""
        if "__" in t:
            extra = "_pid TEXT NOT NULL, _ord INTEGER, "
        pk = KEYS.get(t)
        coldefs = ", ".join(f'"{c}" {ty}' + (" PRIMARY KEY" if c == pk else "") for c, ty in cols)
        out.execute(f'CREATE TABLE "{t}" ({extra}{coldefs})')


def ins(out, table, recs):
    cols = SCHEMA[table]
    names = ([] if "__" not in table else ["_pid", "_ord"]) + [c for c, _ in cols]
    sql = f'INSERT INTO "{table}" ({", ".join(chr(34) + n + chr(34) for n in names)}) VALUES ({",".join("?" * len(names))})'
    return batched_insert(out, sql, recs, 50000)


# ---------------------------------------------------------------- fill
def days_between(a, b):
    import datetime as dt
    try:
        return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
    except (TypeError, ValueError):
        return None


def valid_date(d):
    return d if d and re.match(r"^(1[7-9]|20)\d\d-\d\d-\d\d$", d) else None


def fill_patents(out, st, S, bulk):
    st.execute("create temp table s(patent_id text primary key)")
    st.executemany("insert into temp.s values (?)", ((i,) for i in S))
    pat = {}
    for pid, typ, date, title, wk, wd in st.execute(
            "select p.patent_id, patent_type, patent_date, patent_title, wipo_kind, withdrawn from p join temp.s using(patent_id)"):
        pat[pid] = {"patent_id": pid, "patent_zero_prefix": zero_prefix(pid), "patent_title": title,
                    "patent_type": typ, "patent_date": date, "patent_year": int(date[:4]) if date else None,
                    "wipo_kind": wk, "withdrawn": wd}
    log("patents base", len(pat))
    log("abstracts")
    for r in rows(bulk, "g_patent_abstract"):
        if r["patent_id"] in pat:
            pat[r["patent_id"]]["patent_abstract"] = nz(r["patent_abstract"])
    log("gov interest")
    for r in rows(bulk, "g_gov_interest"):
        if r["patent_id"] in pat:
            pat[r["patent_id"]]["gov_interest_statement"] = nz(r["gi_statement"])
    log("applications")
    app = []
    for r in rows(bulk, "g_application"):
        pid = r["patent_id"]
        if pid in pat:
            fd = nz(r["filing_date"])
            app.append((pid, len(app), nz(r["application_id"]), nz(r["patent_application_type"]), fd,
                        nz(r["series_code"]), 1 if r["rule_47_flag"] in ("1", "TRUE", "true") else 0,
                        nz(r["patent_application_type"])))
            v = valid_date(fd)
            if v and (pat[pid].get("patent_earliest_application_date") is None
                      or v < pat[pid]["patent_earliest_application_date"]):
                pat[pid]["patent_earliest_application_date"] = v
    ins(out, "patents__application", app)
    log("term of grant")
    tog = []
    for r in rows(bulk, "g_us_term_of_grant"):
        pid = r["patent_id"]
        if pid in pat:
            tog.append((pid, len(tog), nz(r["term_grant"]), nz(r["term_extension"]), nz(r["term_disclaimer"]),
                        nz(r["disclaimer_date"])))
            if to_int(r["term_extension"]) is not None:
                pat[pid]["patent_term_extension"] = to_int(r["term_extension"])
    ins(out, "patents__us_term_of_grant", tog)
    log("us patent citations (large)")
    cited, cited_by, cits = {}, {}, []
    n = 0
    for r in rows(bulk, "g_us_patent_citation"):
        n += 1
        pid, cpid = r["patent_id"], r["citation_patent_id"]
        if pid in pat:
            cited[pid] = cited.get(pid, 0) + 1
            seq = to_int(r["citation_sequence"])
            cits.append((f"{pid}-{seq}", pid, pid, zero_prefix(pid), seq, nz(cpid), nz(cpid), nz(r["wipo_kind"]),
                         nz(r["citation_category"]), nz(r["citation_date"]), nz(r["record_name"])))
        if cpid in pat:
            cited_by[cpid] = cited_by.get(cpid, 0) + 1
        if n % 20000000 == 0:
            log("   citation rows scanned", n)
    out.executemany("INSERT OR IGNORE INTO us_patent_citations VALUES (?,?,?,?,?,?,?,?,?,?,?)", cits)
    out.commit()
    for pid, d in pat.items():
        d["patent_num_us_patents_cited"] = cited.get(pid, 0)
        d["patent_num_times_cited_by_us_patents"] = cited_by.get(pid, 0)
        d["patent_processing_days"] = days_between(d.get("patent_earliest_application_date"), d.get("patent_date"))
    cols = [c for c, _ in SCHEMA["patents"]]
    ins(out, "patents", (tuple(d.get(c) for c in cols) for d in pat.values()))
    return pat


def fill_children(out, st, pat, bulk):
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
        recs = []
        for r in rows(bulk, name):
            if r["patent_id"] in pat:
                recs.append((r["patent_id"], len(recs), *fn(r)))
        ins(out, table, recs)

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
    ents, years = [], []
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
            ents.append((cur, last[3], last[4], last[5], last[7], last[8], last[9], last[10], last[11], last[6],
                         min(dates) if dates else None, max(dates) if dates else None, others.get(cur, 0),
                         len(pats), len(yc)))
        else:
            ents.append((cur, last[3], last[4], last[5], last[6], last[8], last[9], last[10], last[11], last[12],
                         last[7], min(dates) if dates else None, max(dates) if dates else None,
                         others.get(cur, 0), len(pats), len(yc)))
        for k, (y, c) in enumerate(sorted(yc.items())):
            years.append((cur, k, y, c))

    for r in st.execute(q):
        if r[0] != cur:
            flush()
            cur, grp = r[0], []
        grp.append(r)
    flush()
    ins(out, f"{kind}s", ents)
    ins(out, f"{kind}s__{kind}_years", years)
    log(f"  {len(ents)} {kind}s")


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


SCHEMA_FILES = {"patents": "granted/patents_fields.json", "inventors": "granted/inventors_fields.json",
                "assignees": "granted/assignees_fields.json", "locations": "granted/locations_fields.json",
                "us_patent_citations": "granted/us_patent_citations_fields.json"}
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
    pat = fill_patents(out, st, S, a.bulk)
    fill_children(out, st, pat, a.bulk)
    fill_entity(out, st, "inventor")
    fill_entity(out, st, "assignee")
    fill_locations(out, st)
    fill_meta_and_fts(out, a.es_data_load)
    make_indexes(out)
    counts = {t: out.execute(f'select count(*) from "{t}"').fetchone()[0] for t in SCHEMA}
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
