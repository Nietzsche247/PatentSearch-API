"""SQL shapes of the SQLite backend (PatentRef gate 2.10) checked against a brute-force oracle.

A small database in the Lapse layout, built here with the same functions build_sample.py uses for the
index set, trigram tables and value stats. Every query below is run twice through LapseSQLiteSearch,
once with the stats and trigram tables present and once with them removed (the old shapes), and both
results are compared with a plain Python evaluation of the ES semantics. The shape tests then assert
which SQL each path emits. No Django, no network. Run from the fork root:

  python -m pytest lapse_tools/tests -q -p no:cacheprovider
"""
import json
import os
import random
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lapse_tools"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pvapi.settings.lapse_local")
os.environ.setdefault("LAPSE_DATA_DIR", tempfile.mkdtemp(prefix="lapse_shapes_"))

import build_sample as B  # noqa: E402
from API import search_sqlite as S  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="lapse_shapes_db_"))
N = 3000
TYPES = ["utility"] * 90 + ["design"] * 8 + ["reissue", "plant"]
SECTIONS = "ABCDEFGHY"
FIRST = ["Sarvotham", "Sarvo", "Anna", "Bob", "Carla", "Dmitri", "Élodie", "Fran", "Gus", "ΟΔΥΣΣΕΥΣ", "Kelvin", "Jo"]
LAST = ["Smith", "Smithson", "Whitney", "Hopper", "Jones", "Müller", "Schmidt", "Lee", "O'Brien", "Mitchell"]
ORGS = ["Apple Inc.", "Applied Materials", "CNH Industrial Canada, Ltd.", "Pineapple Co", None, "Google LLC", "Samsung"]


def build(path, with_v2=True):
    rnd = random.Random(7)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    fields = [("", "patent_id", "keyword", 0), ("", "patent_title", "text", 0), ("", "patent_type", "keyword", 0),
              ("", "patent_date", "date", 0), ("", "patent_year", "long", 0), ("", "withdrawn", "boolean", 0),
              ("inventors", "inventor_name_first", "text", 1), ("inventors", "inventor_name_last", "text", 1),
              ("inventors", "inventor_country", "keyword", 0),
              ("assignees", "assignee_organization", "text", 1),
              ("cpc_current", "cpc_section", "keyword", 0), ("cpc_current", "cpc_group_id", "keyword", 0)]
    con.executemany("INSERT INTO _lapse_fields VALUES ('patents',?,?,?,?)", fields)
    con.execute('CREATE TABLE patents("patent_id" TEXT PRIMARY KEY, "patent_title" TEXT, "patent_type" TEXT, '
                '"patent_date" TEXT, "patent_year" INTEGER, "withdrawn" INTEGER)')
    con.execute('CREATE TABLE patents__inventors(_pid TEXT NOT NULL, _ord INTEGER, "inventor_name_first" TEXT, '
                '"inventor_name_last" TEXT, "inventor_country" TEXT)')
    con.execute('CREATE TABLE patents__assignees(_pid TEXT NOT NULL, _ord INTEGER, "assignee_organization" TEXT)')
    con.execute('CREATE TABLE patents__cpc_current(_pid TEXT NOT NULL, _ord INTEGER, "cpc_section" TEXT, "cpc_group_id" TEXT)')
    words = ["surgical", "instrument", "method", "device", "data", "transfer", "assembly", "cotton", "gin", "COBOL"]
    for i in range(N):
        pid = str(10000000 + i * 7)
        title = " ".join(rnd.choice(words) for _ in range(rnd.randint(2, 6)))
        year = rnd.randint(1990, 2025)
        date = f"{year}-{rnd.randint(1, 12):02d}-{rnd.randint(1, 28):02d}"
        con.execute("INSERT INTO patents VALUES (?,?,?,?,?,?)",
                    (pid, title, rnd.choice(TYPES), date, year, 1 if rnd.random() < 0.01 else 0))
        for k in range(rnd.randint(1, 3)):
            con.execute("INSERT INTO patents__inventors VALUES (?,?,?,?,?)",
                        (pid, k, rnd.choice(FIRST), rnd.choice(LAST), rnd.choice(["US"] * 6 + ["JP", "DE", None])))
        if rnd.random() < 0.8:
            con.execute("INSERT INTO patents__assignees VALUES (?,?,?)", (pid, 0, rnd.choice(ORGS)))
        for k in range(rnd.randint(1, 4)):
            sec = rnd.choice(SECTIONS + "HHHH")
            con.execute("INSERT INTO patents__cpc_current VALUES (?,?,?,?)", (pid, k, sec, f"{sec}01L{rnd.randint(1, 60)}/00"))
    con.execute("CREATE VIRTUAL TABLE fts_patents USING fts5(patent_title, content='patents', content_rowid='rowid', "
                f"tokenize=\"{B.FTS_TOKENIZE}\")")
    con.execute("INSERT INTO fts_patents(fts_patents) VALUES ('rebuild')")
    con.execute("CREATE VIRTUAL TABLE fts_patents__assignees USING fts5(assignee_organization, content='patents__assignees', "
                f"content_rowid='rowid', tokenize=\"{B.FTS_TOKENIZE}\")")
    con.execute("INSERT INTO fts_patents__assignees(fts_patents__assignees) VALUES ('rebuild')")
    con.execute("CREATE VIRTUAL TABLE fts_patents__inventors USING fts5(inventor_name_first, inventor_name_last, "
                f"content='patents__inventors', content_rowid='rowid', tokenize=\"{B.FTS_TOKENIZE}\")")
    con.execute("INSERT INTO fts_patents__inventors(fts_patents__inventors) VALUES ('rebuild')")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.commit()
    if with_v2:
        B.make_indexes(con, upgrade=True)
        con.execute("INSERT INTO _lapse_build VALUES ('index_set', ?)", (B.INDEX_SET,))
    else:
        for t in ("patents__inventors", "patents__assignees", "patents__cpc_current"):
            con.execute(f'CREATE INDEX "ix_{t}__pid" ON "{t}"(_pid)')
        con.execute('CREATE INDEX ix_patents_patent_date ON patents(patent_date)')
        con.execute('CREATE INDEX ix_patents_patent_type ON patents(patent_type)')
        con.execute("ANALYZE")
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def dbs():
    v2, v1 = TMP / "v2.db", TMP / "v1.db"
    build(v2, True)
    build(v1, False)
    return S.LapseSQLiteSearch(str(v2), count_cache=str(TMP / "memo.sqlite3"), count_cache_min_ms=0.0), \
        S.LapseSQLiteSearch(str(v1), count_cache=None)


# ---- oracle: the ES semantics evaluated in Python over the raw rows
def load(path):
    con = sqlite3.connect(path)
    pats = {r[0]: dict(zip(["patent_id", "patent_title", "patent_type", "patent_date", "patent_year", "withdrawn"], r))
            for r in con.execute("SELECT * FROM patents")}
    for p in pats.values():
        p["inventors"], p["assignees"], p["cpc_current"] = [], [], []
    for pid, _, f, l, c in con.execute("SELECT * FROM patents__inventors"):
        pats[pid]["inventors"].append({"inventor_name_first": f, "inventor_name_last": l, "inventor_country": c})
    for pid, _, o in con.execute("SELECT * FROM patents__assignees"):
        pats[pid]["assignees"].append({"assignee_organization": o})
    for pid, _, s, g in con.execute("SELECT * FROM patents__cpc_current"):
        pats[pid]["cpc_current"].append({"cpc_section": s, "cpc_group_id": g})
    return pats


def contains(v, needle):
    return v is not None and needle.lower() in v.lower() and any(needle.lower() in t for t in S.tokens(v))


def kw_contains(v, needle):
    return v is not None and needle.lower() in v.lower() and len(v) <= 256


def kw_begins(v, needle):
    return v is not None and v.lower().startswith(needle.lower()) and len(v) <= 256


def ev(p, q):
    """Evaluate one ES clause (the subset the parser emits) on a patent dict."""
    kind, body = next(iter(q.items()))
    if kind == "bool":
        ok = all(ev(p, s) for c in ("filter", "must") for s in body.get(c, []))
        if "should" in body and not any(c in body for c in ("filter", "must")):
            ok = any(ev(p, s) for s in body["should"])
        return ok and not any(ev(p, s) for s in body.get("must_not", []))
    if kind == "nested":
        return any(ev(child, body["query"]) for child in p[body["path"]])
    if kind == "match":
        f, v = next(iter(body.items()))
        f = f.replace(".keyword", "").split(".")[-1]
        if f == "patent_title":
            toks = S.tokens(p[f])
            return any(t in toks for t in S.tokens(v))
        if f == "withdrawn":
            return p[f] == (1 if v else 0)
        return p.get(f) == v
    if kind == "terms":
        f, vs = next(iter(body.items()))
        f = f.replace(".keyword", "").split(".")[-1]
        return p.get(f) in vs
    if kind == "range":
        f, spec = next(iter(body.items()))
        v = p[f]
        return v is not None and all((v >= b if k == "gte" else v <= b if k == "lte" else v > b if k == "gt" else v < b)
                                     for k, b in spec.items())
    if kind == "wildcard":
        f, spec = next(iter(body.items()))
        kw = f.endswith(".keyword")
        f = f.replace(".keyword", "").split(".")[-1]
        needle = spec["value"][1:-1]
        return kw_contains(p.get(f), needle) if kw else contains(p.get(f), needle)
    if kind == "prefix":
        f, spec = next(iter(body.items()))
        f = f.replace(".keyword", "").split(".")[-1]
        return kw_begins(p.get(f), spec["value"])
    raise AssertionError(kind)


def oracle(pats, q, sort, size, after=None):
    rows = [p for p in pats.values() if ev(p, q)]
    keys = [(next(iter(s.items()))) for s in sort]

    def sk(p):
        out = []
        for f, o in keys:
            v = p[f]
            out.append((0, v) if o == "asc" else (0, _neg(v)))
        return out

    rows.sort(key=sk)
    if after:
        def beyond(p):
            for (f, o), a in zip(keys, after):
                v = p[f]
                if v == a:
                    continue
                return (v > a) if o == "asc" else (v < a)
            return False
        rows = [p for p in rows if beyond(p)]
    return len([p for p in pats.values() if ev(p, q)]), [p["patent_id"] for p in rows[:size]]


class _neg:
    def __init__(self, v):
        self.v = v

    def __lt__(self, o):
        return self.v > o.v

    def __eq__(self, o):
        return self.v == o.v


def wrap(q):
    return {"bool": {"filter": [{"match": {"withdrawn": False}}, q]}}


def nested(path, q):
    return {"nested": {"path": path, "query": q}}


CASES = [
    ("cpc dense", wrap(nested("cpc_current", {"match": {"cpc_current.cpc_section": "H"}}))),
    ("cpc sparse", wrap(nested("cpc_current", {"match": {"cpc_current.cpc_section": "Y"}}))),
    ("inventor country dense", wrap(nested("inventors", {"match": {"inventors.inventor_country": "US"}}))),
    ("last name", wrap(nested("inventors", {"match": {"inventors.inventor_name_last.keyword": "Whitney"}}))),
    ("neq list", wrap({"bool": {"must_not": [{"bool": {"should": [{"match": {"patent_type": "utility"}},
                                                                  {"match": {"patent_type": "design"}},
                                                                  {"match": {"patent_type": "reissue"}}]}}]}})),
    ("neq terms", wrap({"bool": {"must_not": [{"terms": {"patent_type": ["utility", "design"]}}]}})),
    ("not design", wrap({"bool": {"must_not": [{"match": {"patent_type": "design"}}]}})),
    ("not utility (common complement)", wrap({"bool": {"must_not": [{"match": {"patent_type": "utility"}}]}})),
    ("neq year list", wrap({"bool": {"must_not": [{"terms": {"patent_year": [2000, 2001]}}]}})),
    ("contains first kw", wrap(nested("inventors", {"wildcard": {"inventors.inventor_name_first.keyword": {"value": "*sarvo*", "case_insensitive": True}}}))),
    ("contains last kw short", wrap(nested("inventors", {"wildcard": {"inventors.inventor_name_last.keyword": {"value": "*mi*", "case_insensitive": True}}}))),
    ("contains last kw", wrap(nested("inventors", {"wildcard": {"inventors.inventor_name_last.keyword": {"value": "*mit*", "case_insensitive": True}}}))),
    ("contains non-ascii", wrap(nested("inventors", {"wildcard": {"inventors.inventor_name_last.keyword": {"value": "*üll*", "case_insensitive": True}}}))),
    ("contains greek", wrap(nested("inventors", {"wildcard": {"inventors.inventor_name_first.keyword": {"value": "*ευς*", "case_insensitive": True}}}))),
    ("contains apostrophe", wrap(nested("inventors", {"wildcard": {"inventors.inventor_name_last.keyword": {"value": "*o'b*", "case_insensitive": True}}}))),
    ("begins org", wrap(nested("assignees", {"prefix": {"assignees.assignee_organization.keyword": {"value": "Apple", "case_insensitive": True}}}))),
    ("begins org short", wrap(nested("assignees", {"prefix": {"assignees.assignee_organization.keyword": {"value": "Ap", "case_insensitive": True}}}))),
    ("contains title text", wrap({"wildcard": {"patent_title": {"value": "*urgic*", "case_insensitive": True}}})),
    ("contains title with space", wrap({"wildcard": {"patent_title": {"value": "*cotton gin*", "case_insensitive": True}}})),
    ("contains title short", wrap({"wildcard": {"patent_title": {"value": "*ab*", "case_insensitive": True}}})),
    ("utility", wrap({"match": {"patent_type": "utility"}})),
    ("gte date", wrap({"range": {"patent_date": {"gte": "2007-01-09"}}})),
    ("year", wrap({"match": {"patent_year": 2021}})),
    ("withdrawn true", {"match": {"withdrawn": True}}),
    ("match all", None),
]
SORTS = [[{"patent_id": "asc"}], [{"patent_date": "desc"}, {"patent_id": "asc"}], [{"patent_date": "desc"}],
         [{"patent_year": "asc"}, {"patent_id": "asc"}]]


def run(searcher, q, sort, size, after=None):
    r = searcher.search("patents", q or {}, ["patent_id"], size, after, sort)
    ids = [h["_id"] for h in r["hits"]["hits"]]
    return searcher.count("patents", q or {})["count"], ids


@pytest.mark.parametrize("label,q", CASES, ids=[c[0] for c in CASES])
def test_results_match_oracle_and_old_shapes(dbs, label, q):
    new, old = dbs
    pats = load(new.real)
    for sort in SORTS:
        for size in (5, 50):
            want = oracle(pats, q or {"bool": {}}, sort, size)
            got_new = run(new, q, sort, size)
            got_old = run(old, q, sort, size)
            assert got_new == want, (label, sort, size, got_new[0], want[0], got_new[1][:5], want[1][:5])
            assert got_old == want, (label, sort, size, "old shapes")
            # page 2 through the cursor of the last row of page 1
            if want[1]:
                last = pats[want[1][-1]]
                after = [last[next(iter(s))] for s in sort]
                want2 = oracle(pats, q or {"bool": {}}, sort, size, after)
                assert run(new, q, sort, size, after) == want2, (label, sort, size, "page 2")
                assert run(old, q, sort, size, after) == want2, (label, sort, size, "page 2 old")


def sql_of(searcher, q, mode="search"):
    tr = S.Translator(searcher.meta("patents"), mode)
    return tr.where(q), tr.params, tr.density


def test_dense_nested_uses_exists_and_count_uses_in(dbs):
    new, old = dbs
    q = wrap(nested("cpc_current", {"match": {"cpc_current.cpc_section": "H"}}))
    sql, _, _ = sql_of(new, q)
    assert "EXISTS (SELECT 1 FROM \"patents__cpc_current\" c WHERE c.\"_pid\" = m.\"patent_id\"" in sql
    sql_count, _, _ = sql_of(new, q, "count")
    assert "IN (SELECT c.\"_pid\"" in sql_count and "EXISTS" not in sql_count
    sparse, _, _ = sql_of(new, wrap(nested("cpc_current", {"match": {"cpc_current.cpc_section": "Z"}})))
    assert "IN (SELECT c.\"_pid\"" in sparse and "EXISTS" not in sparse
    old_sql, _, _ = sql_of(old, q)
    assert "EXISTS" not in old_sql and "likelihood" not in old_sql


def test_likelihood_hints_only_with_stats(dbs):
    new, old = dbs
    q = wrap({"match": {"patent_type": "utility"}})
    sql, params, density = sql_of(new, q)
    assert sql.count("likelihood(") == 2 and params == [0, "utility"]
    assert 0.85 < density < 0.95
    sql_old, _, d_old = sql_of(old, q)
    assert "likelihood" not in sql_old and d_old is None


def test_must_not_rare_complement_becomes_range_union(dbs):
    new, old = dbs
    q = wrap({"bool": {"must_not": [{"terms": {"patent_type": ["utility", "design", "reissue"]}}]}})
    sql, params, _ = sql_of(new, q)
    assert 'm.rowid IN (SELECT rowid FROM "patents" WHERE ("patent_type" < ?' in sql
    assert '"patent_type" IS NULL' in sql and "NOT COALESCE" not in sql
    assert params == [0, "design", "design", "reissue", "reissue", "utility", "utility"]
    sql_c, _, _ = sql_of(new, q, "count")
    assert 'm."patent_type" < ?' in sql_c and "rowid IN" not in sql_c
    # a common complement keeps the plain NOT (the range union would walk most of the index)
    sql2, _, _ = sql_of(new, wrap({"bool": {"must_not": [{"match": {"patent_type": "design"}}]}}))
    assert "NOT COALESCE" in sql2 and "rowid IN" not in sql2
    old_sql, _, _ = sql_of(old, q)
    assert "NOT COALESCE" in old_sql


def test_trigram_prefilter_rules(dbs):
    new, old = dbs
    w = lambda needle: wrap(nested("inventors", {"wildcard": {"inventors.inventor_name_first.keyword": {"value": f"*{needle}*", "case_insensitive": True}}}))
    sql, params, _ = sql_of(new, w("sarvo"))
    assert 'c.rowid IN (SELECT rowid FROM "fts_trgm_patents__inventors" WHERE "fts_trgm_patents__inventors" MATCH ?)' in sql
    assert params[1] == '"inventor_name_first" : "sarvo"' and "instr(" in sql
    assert "fts_trgm" not in sql_of(new, w("sa"))[0]            # under three characters: no trigram
    assert "fts_trgm" not in sql_of(new, w("ευς"))[0]           # non-ASCII: Python folding only
    assert "fts_trgm" not in sql_of(old, w("sarvo"))[0]         # no trigram table: old predicate
    t = wrap({"wildcard": {"patent_title": {"value": "*urgic*", "case_insensitive": True}}})
    sql_t, params_t, _ = sql_of(new, t)
    assert 'm.rowid IN (SELECT rowid FROM "fts_trgm_patents"' in sql_t and "lapse_tok_wild" in sql_t
    b = wrap(nested("assignees", {"prefix": {"assignees.assignee_organization.keyword": {"value": "Apple", "case_insensitive": True}}}))
    sql_b, params_b, _ = sql_of(new, b)
    assert "fts_trgm_patents__assignees" in sql_b and params_b[1] == '"assignee_organization" : "apple"' and "LIKE ?" in sql_b


def test_keyset_nested_form_and_null_fallback(dbs):
    new, _ = dbs
    meta = new.meta("patents")
    spec = new._sort_spec(meta, [{"patent_date": "desc"}, {"patent_id": "asc"}])
    params = []
    sql = new._after(spec, ["2024-10-01", "US12345678"], params, {"patent_date": False, "patent_id": False})
    assert sql == '(m."patent_date" <= ? AND (m."patent_date" < ? OR (m."patent_id" > ?)))'
    assert params == ["2024-10-01", "2024-10-01", "US12345678"]
    params = []
    sql = new._after(spec, ["2024-10-01", "US12345678"], params, {"patent_date": True, "patent_id": False})
    assert "IS NULL" in sql and params == ["2024-10-01", "2024-10-01", "US12345678"]


def test_count_memo_is_shared_and_version_keyed(dbs):
    new, _ = dbs
    q = wrap({"match": {"patent_type": "utility"}})
    n1 = new.count("patents", q)["count"]
    where, params, _ = sql_of(new, q, "count")
    sql = f'SELECT count(*) FROM "patents" m WHERE {where}'
    con = sqlite3.connect(str(TMP / "memo.sqlite3"))
    rows = con.execute("SELECT data_version, n, key FROM counts").fetchall()
    assert (new.data_version(), n1, new._memo_key("patents", sql, params)) in rows
    # another searcher on the same file (another worker, nothing in its process) reads the stored count
    S.LapseSQLiteSearch._memo_local.clear()
    other = S.LapseSQLiteSearch(new.path, count_cache=str(TMP / "memo.sqlite3"), count_cache_min_ms=0.0)
    assert other._memo_get("patents", sql, params) == n1
    assert other.count("patents", q)["count"] == n1
    # the key carries the data version: a different version never sees it
    fake = type("V", (), {"data_version": lambda self: "other"})()
    assert other._memo_key("patents", sql, params) != S.LapseSQLiteSearch._memo_key(fake, "patents", sql, params)


def test_steering_hint_only_for_broad_filters(dbs):
    new, _ = dbs
    seen = {}
    orig = new._run

    def spy(sql, params):
        seen["sql"] = sql
        return orig(sql, params)

    new._run = spy
    try:
        new.search("patents", wrap({"match": {"patent_type": "utility"}}), ["patent_id"], 5, None, [{"patent_date": "desc"}])
        assert 'INDEXED BY "ix_patents_patent_date"' in seen["sql"]
        new.search("patents", wrap({"match": {"patent_year": 2021}}), ["patent_id"], 5, None, [{"patent_date": "desc"}])
        assert "INDEXED BY" not in seen["sql"]
        new.search("patents", wrap({"match": {"patent_type": "utility"}}), ["patent_id"], 5, None, [{"patent_id": "asc"}])
        assert "INDEXED BY" not in seen["sql"]
    finally:
        new._run = orig


def test_upgrade_is_idempotent_and_stamps_index_set():
    path = TMP / "up.db"
    build(path, False)
    B.upgrade_indexes(str(path))
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_patents_withdrawn_patent_type" in names and "ix_patents__cpc_current_cpc_section__pid" in names
    assert "ix_patents__cpc_current_cpc_section" not in names
    assert con.execute("SELECT v FROM _lapse_build WHERE k='index_set'").fetchone()[0] == B.INDEX_SET
    stats = dict(con.execute("SELECT col, n FROM _lapse_value_stats WHERE tbl='patents' AND col='*'").fetchall())
    assert stats["*"] == N
    before = con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    con.close()
    B.upgrade_indexes(str(path))
    con = sqlite3.connect(path)
    assert con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == before
    assert con.execute("SELECT count(*) FROM _lapse_build WHERE k='index_set'").fetchone()[0] == 1


def test_random_needles_agree_with_and_without_trigram(dbs):
    """_contains and _begins on random substrings of the data (ASCII and not, 1 to 7 characters):
    the trigram path and the plain scan return the same counts and pages as the oracle."""
    new, old = dbs
    pats = load(new.real)
    rnd = random.Random(11)
    pool = FIRST + LAST + [o for o in ORGS if o] + [p["patent_title"] for p in list(pats.values())[:40]]
    for _ in range(120):
        src = rnd.choice(pool)
        a = rnd.randrange(len(src))
        needle = src[a:a + rnd.randint(1, 7)].strip()
        if not needle or "*" in needle or "?" in needle:
            continue
        for field, kind in (("inventors.inventor_name_first.keyword", "wildcard"), ("inventors.inventor_name_last.keyword", "wildcard"),
                            ("assignees.assignee_organization.keyword", "prefix"), ("patent_title", "wildcard")):
            value = f"*{needle}*" if kind == "wildcard" else needle
            leaf = {kind: {field: {"value": value, "case_insensitive": True}}}
            q = wrap(leaf if field == "patent_title" else nested(field.split(".")[0], leaf))
            want = oracle(pats, q, [{"patent_id": "asc"}], 20)
            assert run(new, q, [{"patent_id": "asc"}], 20) == want, (field, needle)
            assert run(old, q, [{"patent_id": "asc"}], 20) == want, (field, needle, "old")
