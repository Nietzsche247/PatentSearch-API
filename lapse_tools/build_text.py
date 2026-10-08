"""Build the long-text database (PatentRef checklist 1.6): the four granted text indices of the upstream API
from the PatentsView PVGPATTXT bulk zips.

  python build_text.py --bulk /data/lapse/patentsview --out /data/api/text_next.db
                       [--es-data-load /srv/api/es-data-load] [--desc-from 2005] [--workers 8] [--only g_claims]

Why a second file (decision 2026-10-08, patentref decisions.md): the text is 400 GB raw and changes with the
quarterly PVGPATTXT release, the search snapshot is 160 GB and is rebuilt every week with the fee file. Kept
in one file the weekly build would take about 14 hours and the box would hold three to four copies of 400 GB.
So the text lives in `text_<data_version>.db`, ATTACHed read-only as `txt` beside the search snapshot
(API/search_sqlite.py), swapped by `lapse refresh --swap` together with the other two files, and rebuilt
only when the PVGPATTXT files change (`lapse refresh --build` decides from the sidecar's inputs fingerprint).

Layout (the same the search snapshot has, API/search_sqlite.py):
  g_claims, g_brf_sum_texts, g_detail_desc_texts, g_draw_desc_texts   one row per upstream document, key uuid
  fts_<table>              FTS5 over the text column, CONTENTLESS (content=''), fed row by row at load time,
                           detail=full (positions kept, so _text_phrase is answered by the index). --desc-fts-detail
                           none drops the positions of the descriptions (their index shrinks from about 30 to about
                           5 percent of the raw text); the translator then answers a phrase with an AND prefilter
                           plus the exact Python test, which is too slow for a common phrase over 5.7M descriptions
                           (measured 2026-10-08: 35,798 candidates in one year at 1.5 ms each), so full is the default
  _lapse_fields, _lapse_indices, _lapse_build, _lapse_nulls (tbl, col, has_null: the searcher's null check
                           without a scan of a 150M-row table)
The text column of every table is stored zlib-compressed (level 6; 2.2x on claims, more on long documents);
the searcher decompresses on read and in its Python matchers, so the API sees plain text.

Rows whose patent_id is not a granted patent in g_patent are dropped (the rule every table of the API layout
follows); the count is logged per file. uuid is patent_id-<sequence> for claims and drawing descriptions and
patent_id for the one-per-patent tables (upstream's uuids are database ids no bulk file carries).
"""
import argparse
import csv
import io
import json
import multiprocessing as mp
import os
import re
import sqlite3
import sys
import time
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_sample import FTS_TOKENIZE, log, nz, rows, to_int, zero_prefix  # noqa: E402

csv.field_size_limit(2**31 - 1)

T, I, B = "TEXT", "INTEGER", "BLOB"
ZLEVEL = 6
TEXT_SCHEMA = {
    "g_claims": [("uuid", T), ("patent_id", T), ("patent_zero_prefix", T), ("claim_number", T), ("claim_dependent", T),
                 ("claim_text", B), ("claim_sequence", I), ("exemplary", I)],
    "g_brf_sum_texts": [("uuid", T), ("patent_id", T), ("patent_zero_prefix", T), ("summary_text", B)],
    "g_detail_desc_texts": [("uuid", T), ("patent_id", T), ("patent_zero_prefix", T), ("description_text", B),
                            ("description_length", I)],
    "g_draw_desc_texts": [("uuid", T), ("patent_id", T), ("patent_zero_prefix", T), ("draw_desc_text", B),
                          ("draw_desc_sequence", I)],
}
TEXT_KEYS = {t: "uuid" for t in TEXT_SCHEMA}
# table -> bulk file family (g_<family>_YYYY.tsv.zip), text column, FTS detail
TEXT_FAMILY = {"g_claims": "g_claims", "g_brf_sum_texts": "g_brf_sum_text",
               "g_detail_desc_texts": "g_detail_desc_text", "g_draw_desc_texts": "g_draw_desc_text"}
TEXT_COLUMN = {"g_claims": "claim_text", "g_brf_sum_texts": "summary_text",
               "g_detail_desc_texts": "description_text", "g_draw_desc_texts": "draw_desc_text"}
FTS_DETAIL = {"g_claims": "full", "g_brf_sum_texts": "full", "g_detail_desc_texts": "full", "g_draw_desc_texts": "full"}
TEXT_SCHEMA_FILES = {"g_claims": "granted/claim.json", "g_brf_sum_texts": "granted/brf_sum_text.json",
                     "g_detail_desc_texts": "granted/detail_desc_text.json", "g_draw_desc_texts": "granted/draw_desc_text.json"}
# every sortable field of the four endpoints leads an index (the null check and the ORDER BY walk need it)
TEXT_INDEXES = {
    "g_claims": [("patent_id", "claim_sequence"), "claim_sequence", "claim_number", "claim_dependent", "exemplary",
                 "patent_zero_prefix"],
    "g_brf_sum_texts": ["patent_id", "patent_zero_prefix"],
    "g_detail_desc_texts": ["patent_id", "description_length", "patent_zero_prefix"],
    "g_draw_desc_texts": [("patent_id", "draw_desc_sequence"), "draw_desc_sequence", "patent_zero_prefix"],
}
INDEX_SET = "1"
DESC_FROM_DEFAULT = 2005
BATCH = {"g_claims": 20000, "g_brf_sum_texts": 4000, "g_detail_desc_texts": 500, "g_draw_desc_texts": 20000}


def family_files(bulk, table, desc_from):
    """The year files of one family on disk, oldest first; detail descriptions from desc_from on."""
    fam = TEXT_FAMILY[table]
    out = []
    for p in sorted(Path(bulk).glob(f"{fam}_*.tsv.zip")):
        m = re.match(rf"^{re.escape(fam)}_(\d{{4}})\.tsv\.zip$", p.name)
        if not m:
            continue
        if table == "g_detail_desc_texts" and int(m.group(1)) < desc_from:
            continue
        out.append(p)
    return out


def granted_ids(bulk):
    """The patent ids of g_patent (the membership test every table of the API layout applies)."""
    ids = set()
    for r in rows(bulk, "g_patent"):
        ids.add(r["patent_id"])
    return ids


def convert(table, r):
    """One bulk row -> (uuid, patent_id, patent_zero_prefix, ...columns...) with the text still plain."""
    pid = r["patent_id"]
    zp = zero_prefix(pid)
    if table == "g_claims":
        seq = to_int(r.get("claim_sequence"))
        return (f"{pid}-{seq}", pid, zp, nz(r.get("claim_number")), nz(r.get("dependent")), nz(r.get("claim_text")),
                seq, to_int(r.get("exemplary")))
    if table == "g_brf_sum_texts":
        return (pid, pid, zp, nz(r.get("summary_text")))
    if table == "g_detail_desc_texts":
        t = nz(r.get("description_text"))
        n = to_int(r.get("description_length"))
        return (pid, pid, zp, t, n if n is not None else (len(t) if t else None))
    if table == "g_draw_desc_texts":
        seq = to_int(r.get("draw_desc_sequence"))
        return (f"{pid}-{seq}", pid, zp, nz(r.get("draw_desc_text")), seq)
    raise ValueError(table)


_IDS = set()  # granted ids, set in the parent before the workers fork (inherited, never pickled)


def producer(args):
    """Worker: parse one zip, drop orphans, compress the text; put batches of (row tuple with the compressed
    text, plain text) on the queue. Ends with ("done", table, file name, rows kept, orphans)."""
    table, path, q, batch = args
    ids = _IDS
    ti = [c for c, _ in TEXT_SCHEMA[table]].index(TEXT_COLUMN[table])
    buf, kept, orphans = [], 0, 0
    zf = zipfile.ZipFile(path)
    with zf.open(zf.namelist()[0]) as f:
        rd = csv.reader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""), delimiter="\t", quotechar='"')
        head = next(rd)
        for rec in rd:
            if len(rec) != len(head):
                continue
            r = dict(zip(head, rec))
            if r.get("patent_id") not in ids:
                orphans += 1
                continue
            row = list(convert(table, r))
            text = row[ti]
            row[ti] = zlib.compress(text.encode("utf-8"), ZLEVEL) if text else None
            buf.append((tuple(row), text))
            kept += 1
            if len(buf) >= batch:
                q.put(("rows", table, Path(path).name, buf))
                buf = []
    if buf:
        q.put(("rows", table, Path(path).name, buf))
    q.put(("done", table, Path(path).name, kept, orphans))
    return Path(path).name


def create_schema(out):
    for t, cols in TEXT_SCHEMA.items():
        coldefs = ", ".join(f'"{c}" {ty}' + (" PRIMARY KEY" if c == TEXT_KEYS[t] else "") for c, ty in cols)
        out.execute(f'CREATE TABLE "{t}" ({coldefs})')
        out.execute(f'CREATE VIRTUAL TABLE "fts_{t}" USING fts5("{TEXT_COLUMN[t]}", content=\'\', '
                    f"detail={FTS_DETAIL[t]}, tokenize=\"{FTS_TOKENIZE}\")")


def load(out, bulk, desc_from, workers, only=None, log_fn=log):
    """Stream every family through the worker pool into `out`; one writer, rowids assigned here so the
    contentless FTS rows carry the same rowid as their table row. Returns {table: rows}, source list."""
    tables = [t for t in TEXT_SCHEMA if not only or t in only]
    jobs = []
    for t in tables:
        for p in family_files(bulk, t, desc_from):
            jobs.append((t, p))
    if not jobs:
        raise SystemExit("no PVGPATTXT files found in " + str(bulk))
    log_fn(f"{len(jobs)} files: " + ", ".join(f"{t} {sum(1 for x, _ in jobs if x == t)}" for t in tables))
    # OR IGNORE: the rowids are assigned here and never repeat, so the only conflict is a uuid the source
    # carries twice (the same patent in two year files); the first row stays, the later one is dropped and its
    # rowid stays unused. (2026-10-08: a fallback that retried the batch row by row from the batch's first
    # rowid collided with the rows executemany had already written and dropped everything after the first
    # duplicate; the per-file inserted count in the log and the final count check below catch that class.)
    ins = {t: f'INSERT OR IGNORE INTO "{t}"(rowid, {", ".join(chr(34) + c + chr(34) for c, _ in TEXT_SCHEMA[t])}) '
              f'VALUES ({",".join("?" * (len(TEXT_SCHEMA[t]) + 1))})' for t in tables}
    fts = {t: f'INSERT INTO "fts_{t}"(rowid, "{TEXT_COLUMN[t]}") VALUES (?, ?)' for t in tables}
    next_rowid = {t: 1 for t in tables}
    counts = {t: 0 for t in tables}
    sources = []
    dups = {t: 0 for t in tables}       # identical copies of a row already in the table: dropped
    renamed = {t: 0 for t in tables}    # same key, different text: kept under the key plus "~2", "~3", ...
    dup_examples = {t: [] for t in tables}
    ti = {t: [c for c, _ in TEXT_SCHEMA[t]].index(TEXT_COLUMN[t]) for t in tables}
    per_file = {}  # file name -> [inserted, duplicates]
    ctx = mp.get_context("fork")
    q = ctx.Queue(maxsize=max(2, workers) * 2)
    pending = [(t, str(p), q, BATCH[t]) for t, p in jobs]
    running = []
    active = [0]   # parsers started whose "done" has not arrived yet: a slot is free as soon as "done" is read
    t0 = time.time()

    def start_more():
        # (2026-10-08: freeing a slot only once the process was seen dead stalled the load whenever every
        # running parser had sent "done" but not yet exited, with files still pending)
        while pending and active[0] < workers:
            a = pending.pop(0)
            pr = ctx.Process(target=producer, args=(a,), daemon=True)
            pr.start()
            running.append(pr)
            active[0] += 1

    start_more()
    files_done = 0
    import queue as _queue
    while files_done < len(jobs):
        try:
            msg = q.get(timeout=120)
        except _queue.Empty:
            dead = [p for p in running if not p.is_alive() and p.exitcode not in (0, None)]
            if dead:
                raise RuntimeError(f"a text worker died (exit {dead[0].exitcode}); see the traceback above")
            if not pending and active[0] == 0:
                raise RuntimeError(f"no parser running and {len(jobs) - files_done} files unreported")
            continue
        if msg[0] == "rows":
            _, t, name, buf = msg
            rid = next_rowid[t]
            recs = [(rid + k, *row) for k, (row, _) in enumerate(buf)]
            before = out.total_changes
            out.executemany(ins[t], recs)
            inserted = out.total_changes - before
            if inserted == len(recs):
                out.executemany(fts[t], [(rid + k, text) for k, (_, text) in enumerate(buf) if text])
            else:
                # some uuids were already in the table (the same row twice in the source, or two rows that share a
                # key): an identical copy is dropped; a row with a different text is kept under the key plus "~n",
                # in the rowid its ignored insert left free (2026-10-08: 1,327,958 of the 1,328,037 collisions of
                # the 2026-04-10 release were identical copies; 23 within a file and up to 56 across files were not)
                present = {r[0] for r in out.execute(f'SELECT rowid FROM "{t}" WHERE rowid BETWEEN ? AND ?',
                                                     (rid, rid + len(recs) - 1))}
                n_dup = 0
                for k, (row, text) in enumerate(buf):
                    if rid + k in present:
                        continue
                    old = out.execute(f'SELECT "{TEXT_COLUMN[t]}" FROM "{t}" WHERE uuid=?', (row[0],)).fetchone()
                    if old is not None and (zlib.decompress(old[0]).decode("utf-8") if old[0] else None) == text:
                        n_dup += 1
                        if len(dup_examples[t]) < 5:
                            dup_examples[t].append(f"{row[0]} ({name})")
                        continue
                    n = 2
                    while out.execute(f'SELECT 1 FROM "{t}" WHERE uuid=?', (f"{row[0]}~{n}",)).fetchone():
                        n += 1
                    out.execute(ins[t], (rid + k, f"{row[0]}~{n}", *row[1:]))
                    present.add(rid + k)
                    renamed[t] += 1
                inserted = len(recs) - n_dup
                out.executemany(fts[t], [(rid + k, text) for k, (_, text) in enumerate(buf) if text and rid + k in present])
                dups[t] += n_dup
            next_rowid[t] = rid + len(recs)
            counts[t] += inserted
            f = per_file.setdefault(name, [0, 0])
            f[0] += inserted
            f[1] += len(recs) - inserted
        else:
            _, t, name, kept, orphans = msg
            files_done += 1
            ins_n, dup_n = per_file.get(name, [0, 0])
            sources.append({"file": name, "table": t, "rows": kept, "orphans": orphans, "inserted": ins_n, "duplicates": dup_n})
            log_fn(f"  {name}: {kept:,} rows, {orphans:,} orphans dropped, {ins_n:,} inserted"
                   + (f", {dup_n:,} identical duplicates dropped" if dup_n else "") + f" ({files_done}/{len(jobs)}, {time.time() - t0:.0f}s)")
            if ins_n + dup_n != kept:
                raise RuntimeError(f"{name}: the parser kept {kept:,} rows but the writer saw {ins_n + dup_n:,}")
            out.commit()
            active[0] -= 1
            for p in [p for p in running if not p.is_alive()]:
                p.join()
                running.remove(p)
            start_more()
    for p in running:
        p.join()
    out.commit()
    for t in tables:
        if dups[t] or renamed[t]:
            log_fn(f"  {t}: {dups[t]:,} identical duplicates dropped (first: {', '.join(dup_examples[t])}); "
                   f"{renamed[t]:,} rows sharing a key with a different text kept under key~n")
    return counts, sources, dups, renamed


def make_indexes(out, tables):
    log("indexes")
    for t in tables:
        for spec in TEXT_INDEXES[t]:
            cols = (spec,) if isinstance(spec, str) else tuple(spec)
            name = f"ix_{t}_{'_'.join(cols)}"
            t0 = time.time()
            out.execute(f'CREATE INDEX "{name}" ON "{t}"({", ".join(chr(34) + c + chr(34) for c in cols)})')
            out.commit()
            log(f"  {name} {time.time() - t0:.0f}s")
    log("nulls")
    out.execute("CREATE TABLE _lapse_nulls(tbl TEXT, col TEXT, has_null INTEGER)")
    for t in tables:
        for c, _ in TEXT_SCHEMA[t]:
            has = out.execute(f'SELECT 1 FROM "{t}" WHERE "{c}" IS NULL LIMIT 1').fetchone() is not None
            out.execute("INSERT INTO _lapse_nulls VALUES (?,?,?)", (t, c, 1 if has else 0))
    out.commit()
    out.execute("PRAGMA analysis_limit=1000")  # sampled statistics: a full ANALYZE would read every index of 300 GB
    out.execute("ANALYZE")
    out.commit()


def fill_meta(out, esdl, tables):
    log("meta")
    out.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    out.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    base = Path(esdl) / "src" / "es_data_load" / "pv" / "schemas"
    for t in tables:
        out.execute("INSERT INTO _lapse_indices VALUES (?,?,?)", (t, t, TEXT_KEYS[t]))
        props = json.load(open(base / TEXT_SCHEMA_FILES[t], encoding="utf-8"))
        for k, v in props.items():
            out.execute("INSERT INTO _lapse_fields VALUES (?,?,?,?,?)",
                        (t, "", k, v.get("type"), 1 if "keyword" in v.get("fields", {}) else 0))
    out.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bulk", default="/data/lapse/patentsview")
    ap.add_argument("--out", default="/data/api/text_next.db")
    ap.add_argument("--es-data-load", default="/srv/api/es-data-load")
    ap.add_argument("--desc-from", type=int, default=DESC_FROM_DEFAULT, help="first grant year of detail descriptions")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--only", action="append", help="build only this table (repeatable; a test aid)")
    ap.add_argument("--desc-fts-detail", choices=["full", "none"], default="full",
                    help="FTS5 detail of the descriptions (none: about 63 GB smaller, phrases answered slowly)")
    a = ap.parse_args()
    FTS_DETAIL["g_detail_desc_texts"] = a.desc_fts_detail
    tables = [t for t in TEXT_SCHEMA if not a.only or t in a.only]
    tmp = a.out + ".building"
    for p in (tmp, tmp + "-journal"):
        if os.path.exists(p):
            os.remove(p)
    log("granted ids from g_patent")
    global _IDS
    _IDS = granted_ids(a.bulk)
    log(f"  {len(_IDS):,} granted patents")
    tmpdir = Path(a.out).parent / "tmp"
    tmpdir.mkdir(exist_ok=True)
    os.environ["SQLITE_TMPDIR"] = str(tmpdir)  # index sorts of a 150M-row table never touch a small /tmp
    out = sqlite3.connect(tmp, timeout=600)
    out.execute("pragma journal_mode=off")
    out.execute("pragma synchronous=off")
    out.execute("pragma cache_size=-4000000")
    out.execute("pragma temp_store=file")
    create_schema(out)
    counts, sources, dups, renamed = load(out, a.bulk, a.desc_from, a.workers, tables)
    for t in tables:  # the table holds exactly what the writer counted (a silent insert failure stops the build)
        n = out.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        if n != counts[t]:
            raise SystemExit(f"{t}: {n:,} rows in the table, {counts[t]:,} counted by the writer")
    for t in tables:
        log(f"  {t:28s} {counts[t]:,}")
    make_indexes(out, tables)
    fill_meta(out, a.es_data_load, tables)
    out.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    out.executemany("INSERT INTO _lapse_build VALUES (?,?)",
                    [("built_at", time.strftime("%Y-%m-%d %H:%M:%S")), ("kind", "text"), ("index_set", INDEX_SET),
                     ("desc_from", str(a.desc_from)), ("zlib_level", str(ZLEVEL)),
                     ("fts_detail", json.dumps({t: FTS_DETAIL[t] for t in tables})),
                     ("row_counts", json.dumps(counts)), ("duplicates", json.dumps(dups)), ("renamed", json.dumps(renamed)), ("sources", json.dumps(sources))])
    out.commit()
    out.close()
    if os.path.exists(a.out):
        os.remove(a.out)
    os.replace(tmp, a.out)
    log("BUILD_DONE")


if __name__ == "__main__":
    main()
