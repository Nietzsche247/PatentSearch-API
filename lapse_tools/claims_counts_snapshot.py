"""The claim counts snapshot (the structure layer, gate S0.6): the counts of every patent the long-text file holds, in
patent_id order, as one gzip-compressed TSV with its data dictionary, a manifest and SHA256SUMS. The counts are
API/claim_counts.py's, so each row is what GET /api/v1/structure/counts/<patent_id>/ answers for the same file.

The full pass runs only after gate 5.8 ticks: C:\\anitw\\s06_snapshot.ps1 (desk item s06_snapshot) starts it on patentref-us1
under systemd-run with CPU and I/O limits, through s06_snapshot_box.sh. This module:
- reads txt.g_claims read only, in (patent_id, claim_sequence) order through that index, one patent at a time; no lock;
- counts in worker processes and writes PART_SIZE patents at a time as one gzip member (a .tmp name, then renamed), with a
  checkpoint after each part, so a stopped run resumes after the last part it finished;
- asks the box's guard before every part (unless --guard none): a timer due within the margin or its job still running, the
  grants run's window, the deploy, heavy or refresh lock held, or --not-before not reached make it write its checkpoint and
  stop (exit 3, PAUSED), to be started again later; at the start the same reasons refuse it (exit 2);
- at the end joins a header member and the parts into claims_counts_<text data version>.tsv.gz (gzip and Python read the
  members as one file), writes the dictionary, the manifest and SHA256SUMS, and reads the file back to count its rows.

  python -m lapse_tools.claims_counts_snapshot run --text /data/api/text_current.db --out DIR [--limit N] [--part-size N]
         [--workers N] [--guard box|none] [--margin-min 45] [--not-before 2026-11-05T03:47:00Z] [--stop-after-parts N]
  python -m lapse_tools.claims_counts_snapshot guard [--margin-min 45] [--not-before ...]
  python -m lapse_tools.claims_counts_snapshot check --out DIR
Exit codes: 0 done (check: the files agree), 1 failed, 2 refused or bad input, 3 paused.
"""
import argparse
import datetime
import gzip
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from API import claim_counts  # noqa: E402

PART_SIZE = 100_000
BATCH = 2048                    # patents handed to the workers at a time
WORK_CAP_CLAIMS = 1000          # the endpoint's caps (API/lapse_structure.py MAX_CLAIMS and MAX_TEXT_CHARS)
WORK_CAP_CHARS = 1_000_000
NOT_BEFORE = "2026-11-05T03:47:00Z"   # gate 5.8's earliest tick: 30 days of measured uptime since 2026-10-06
TIMERS = ("patentref-backup.timer", "lapse-refresh-grants.timer", "lapse-refresh-check.timer", "lapse-contract-probe.timer",
          "patentref-counts.timer", "patentref-registry-backup.timer")
GRANTS_SERVICE = "lapse-refresh-grants.service"
GRANTS_WINDOW_MIN = 130         # the Tuesday and Thursday grants run's window, about 2 h 10 min from its start
LOCKS = ("/data/lapse/deploy.lock", "/data/lapse/heavy.lock", "/data/lapse/refresh.lock")
STREAM_SQL = ("SELECT patent_id, claim_sequence, claim_text, claim_dependent, claim_number, exemplary FROM g_claims "
              "WHERE patent_id > ? ORDER BY patent_id, claim_sequence")
PROGRESS = "progress.json"


def utcnow():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(t=None):
    return (t or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_utc(s):
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)


# ---------------------------------------------------------------- the box's guard

def _systemctl(*a):
    try:
        r = subprocess.run(["systemctl"] + list(a), capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _systemd_time(s):
    """systemctl's 'Fri 2026-10-30 03:31:08 UTC' as an aware datetime, or None."""
    s = (s or "").strip()
    if not s or s in ("n/a", "0"):
        return None
    try:
        return datetime.datetime.strptime(s, "%a %Y-%m-%d %H:%M:%S UTC").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def lock_held(path):
    """Whether a process holds a lock on the file (read from /proc/locks; the lock is never taken here)."""
    try:
        st = os.stat(path)
    except OSError:
        return False
    key = "%02x:%02x:%d" % (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
    try:
        with open("/proc/locks", encoding="ascii", errors="replace") as fh:
            for line in fh:
                f = [x for x in line.split() if x != "->"]
                if len(f) > 5 and f[5] == key:
                    return True
    except OSError:
        return False
    return False


def guard_reasons(margin_min=45, not_before=NOT_BEFORE, now=None):
    """Why the snapshot must not read now (empty when it may): not-before, timers, their jobs, the grants window, locks."""
    now = now or utcnow()
    out = []
    if not_before and now < parse_utc(not_before):
        out.append(f"not before {not_before} (gate 5.8's earliest tick)")
    for t in TIMERS:
        nxt = _systemd_time(_systemctl("show", "-p", "NextElapseUSecRealtime", "--value", t))
        if nxt is not None and (nxt - now).total_seconds() < margin_min * 60:
            out.append(f"{t} fires at {stamp(nxt)}, within {margin_min} min")
        svc = t[:-len(".timer")] + ".service"
        if _systemctl("is-active", svc) in ("active", "activating", "reloading", "deactivating"):
            out.append(f"{svc} is running")
    started = _systemd_time(_systemctl("show", "-p", "ExecMainStartTimestamp", "--value", GRANTS_SERVICE))
    if started is not None and 0 <= (now - started).total_seconds() < GRANTS_WINDOW_MIN * 60:
        out.append(f"inside the grants run's window (it started {stamp(started)}; about {GRANTS_WINDOW_MIN} min)")
    for p in LOCKS:
        if lock_held(p):
            out.append(f"{p} is held")
    return out


# ---------------------------------------------------------------- reading and counting

def patents(con, after, limit=None):
    """(patent_id, rows) in patent_id order after `after`, through the (patent_id, claim_sequence) index."""
    cur = con.execute(STREAM_SQL, (after,))
    pid, rows, n = None, [], 0
    for r in cur:
        if r[0] != pid:
            if pid is not None:
                yield pid, rows
                n += 1
                if limit is not None and n >= limit:
                    return
            pid, rows = r[0], []
        rows.append(r)
    if pid is not None and (limit is None or n < limit):
        yield pid, rows


def counted(source, pool, batch=BATCH):
    """count_one over the source, a batch at a time: the main thread reads the next batch from SQLite, the workers count it,
    so memory holds one batch and the cursor stays in the thread that opened it (Pool.imap would drain the whole source)."""
    buf = []
    for item in source:
        buf.append(item)
        if len(buf) >= batch:
            yield from (pool.map(count_one, buf, chunksize=16) if pool is not None else map(count_one, buf))
            buf = []
    if buf:
        yield from (pool.map(count_one, buf, chunksize=16) if pool is not None else map(count_one, buf))


def count_one(item):
    """(patent_id, TSV line or None, problem or None, claims) for one patent's rows."""
    pid, rows = item
    if len(rows) > WORK_CAP_CLAIMS:
        return pid, None, f"over the work cap ({len(rows)} claim rows)", len(rows)
    rows = [(p, seq, claim_counts.text_of(t), dep, num, ex) for p, seq, t, dep, num, ex in rows]
    chars = sum(len(r[2]) for r in rows)
    if chars > WORK_CAP_CHARS:
        return pid, None, f"over the work cap ({chars} characters)", len(rows)
    pc, _, problems = claim_counts.counts_from_rows(pid, rows)
    if problems:
        return pid, None, f"the claim tree failed its check: {str(problems[0])[:200]}", len(rows)
    return pid, claim_counts.tsv_line(pid, pc), None, len(rows)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def text_version(con):
    try:
        row = con.execute("SELECT v FROM _lapse_build WHERE k = 'data_version'").fetchone()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""


def open_text(path):
    real = os.path.realpath(path)
    con = sqlite3.connect("file:" + real.replace("\\", "/") + "?mode=ro", uri=True)
    return con, real


def files_of(tdv):
    base = f"claims_counts_{tdv}"
    return {"data": base + ".tsv.gz", "dictionary": base + "_dictionary.tsv", "manifest": base + "_manifest.json",
            "problems": base + "_problems.tsv"}


# ---------------------------------------------------------------- run

def run(a):
    if a.guard == "box":
        why = guard_reasons(a.margin_min, a.not_before)
        if why:
            print("SNAPSHOT: REFUSED (" + "; ".join(why) + ")")
            return 2
    con, real = open_text(a.text)
    tdv = text_version(con)
    if not tdv:
        print(f"SNAPSHOT: REFUSED ({real} has no _lapse_build data_version)")
        return 2
    os.makedirs(os.path.join(a.out, "parts"), exist_ok=True)
    for f in os.listdir(os.path.join(a.out, "parts")):
        if f.endswith(".tmp"):
            os.remove(os.path.join(a.out, "parts", f))
    prog_path = os.path.join(a.out, PROGRESS)
    want = {"text_file": os.path.basename(real), "text_data_version": tdv, "structure_version": claim_counts.STRUCTURE_VERSION,
            "counts_version": claim_counts.COUNTS_VERSION, "generator": claim_counts.GENERATOR, "part_size": a.part_size,
            "limit": a.limit}
    if os.path.exists(prog_path):
        with open(prog_path, encoding="utf-8") as fh:
            prog = json.load(fh)
        diff = [k for k, v in want.items() if prog.get(k) != v]
        if diff:
            print(f"SNAPSHOT: REFUSED (the parts in {a.out} were made with other {', '.join(diff)}; use a new --out)")
            return 2
        if prog.get("done"):
            print(f"SNAPSHOT: DONE before ({prog.get('patents')} patents); nothing to do")
            return 0
        print(f"resuming after {prog['last_patent_id']!r}: {len(prog['parts'])} parts, {prog['patents']} patents so far")
    else:
        prog = dict(want, parts=[], patents=0, claims=0, problems=0, last_patent_id="", started=stamp(), done=False)
        write_json(prog_path, prog)
    left = None if a.limit is None else a.limit - prog["patents"] - prog["problems"]
    if left is not None and left <= 0:
        return assemble(a.out, prog, tdv, real, 0.0)
    source = patents(con, prog["last_patent_id"], left)
    pool = None
    if a.workers > 1:
        from multiprocessing import get_context
        pool = get_context("fork" if hasattr(os, "fork") else "spawn").Pool(a.workers)
    results = counted(source, pool)
    t0 = time.time()
    made = run_claims = 0
    try:
        while True:
            if a.guard == "box" and made:
                why = guard_reasons(a.margin_min, a.not_before)
                if why:
                    print("SNAPSHOT: PAUSED (" + "; ".join(why) + f"; {prog['patents']} patents written, resumes after {prog['last_patent_id']!r})")
                    return 3
            if a.stop_after_parts is not None and made >= a.stop_after_parts:
                print(f"SNAPSHOT: PAUSED (--stop-after-parts {a.stop_after_parts}; {prog['patents']} patents written, "
                      f"resumes after {prog['last_patent_id']!r})")
                return 3
            k = len(prog["parts"]) + 1
            name = f"part_{k:05d}.tsv.gz"
            path = os.path.join(a.out, "parts", name)
            n = claims = 0
            first = last = None
            probs = []
            with open(path + ".tmp", "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as gz, \
                    io.TextIOWrapper(gz, encoding="utf-8", newline="\n") as fh:   # no time or name in the header: the same rows, the same bytes
                for pid, line, problem, nclaims in results:
                    first = first or pid
                    last = pid
                    claims += nclaims
                    if line is None:
                        probs.append(f"{pid}\t{problem}\n")
                    else:
                        fh.write(line)
                        n += 1
                    if n + len(probs) >= a.part_size:
                        break
            if last is None:
                os.remove(path + ".tmp")
                break
            os.replace(path + ".tmp", path)
            if probs:
                with open(os.path.join(a.out, "parts", f"problems_{k:05d}.tsv"), "w", encoding="utf-8", newline="\n") as fh:
                    fh.writelines(probs)
            prog["parts"].append({"n": k, "file": name, "first": first, "last": last, "patents": n, "claims": claims,
                                  "problems": len(probs), "bytes": os.path.getsize(path), "sha256": sha256_file(path)})
            prog.update(patents=prog["patents"] + n, claims=prog["claims"] + claims, problems=prog["problems"] + len(probs),
                        last_patent_id=last, updated=stamp())
            write_json(prog_path, prog)
            made += 1
            run_claims += claims
            rate = run_claims / max(1e-9, time.time() - t0)
            print(f"part {k}: {n} patents ({first} to {last}), {claims} claims, {len(probs)} left out; {prog['patents']} in all; "
                  f"{rate:,.0f} claims/s this run")
            if n + len(probs) < a.part_size:
                break
    finally:
        if pool is not None:
            pool.terminate()
    return assemble(a.out, prog, tdv, real, time.time() - t0)


def assemble(out, prog, tdv, real, seconds):
    names = files_of(tdv)
    data = os.path.join(out, names["data"])
    with open(data + ".tmp", "wb") as dst:
        with gzip.GzipFile(filename="", fileobj=dst, mode="wb", compresslevel=6, mtime=0) as gz:
            gz.write(("\t".join(claim_counts.TSV_COLUMNS) + "\n").encode("utf-8"))
        for p in prog["parts"]:
            src = os.path.join(out, "parts", p["file"])
            if sha256_file(src) != p["sha256"]:
                print(f"SNAPSHOT: FAIL ({p['file']} is not the file its checkpoint names)")
                return 1
            with open(src, "rb") as fh:
                for b in iter(lambda: fh.read(1 << 20), b""):
                    dst.write(b)
    os.replace(data + ".tmp", data)
    rows = 0
    with gzip.open(data, "rt", encoding="utf-8", newline="\n") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        for _ in fh:
            rows += 1
    if head != list(claim_counts.TSV_COLUMNS) or rows != prog["patents"]:
        print(f"SNAPSHOT: FAIL (the joined file reads {rows} rows and header {head[:3]}..., the parts {prog['patents']})")
        return 1
    with open(os.path.join(out, names["dictionary"]), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("column\ttype\tdefinition\tuspto_variable\n")
        for col, level, typ, definition, uspto in claim_counts.dictionary_rows():
            if level == "patent":
                fh.write(f"{col}\t{typ}\t{definition}\t{uspto}\n")
    problems = [os.path.join(out, "parts", f) for f in sorted(os.listdir(os.path.join(out, "parts"))) if f.startswith("problems_")]
    if problems:
        with open(os.path.join(out, names["problems"]), "w", encoding="utf-8", newline="\n") as dst:
            dst.write("patent_id\twhy_left_out\n")
            for p in problems:
                with open(p, encoding="utf-8") as fh:
                    dst.write(fh.read())
    manifest = {"dataset": "PatentRef claim counts", "counts_version": claim_counts.COUNTS_VERSION,
                "structure_version": claim_counts.STRUCTURE_VERSION, "generator": claim_counts.GENERATOR,
                "text_file": os.path.basename(real), "text_data_version": tdv, "patents": prog["patents"], "claims": prog["claims"],
                "left_out": prog["problems"], "parts": len(prog["parts"]), "order": "patent_id as text (the long-text file's index)",
                "columns": list(claim_counts.TSV_COLUMNS), "started": prog.get("started"), "finished": stamp(),
                "seconds_last_run": round(seconds, 1), "files": {}}
    for k in ("data", "dictionary", "problems"):
        p = os.path.join(out, names[k])
        if os.path.exists(p):
            manifest["files"][names[k]] = {"bytes": os.path.getsize(p), "sha256": sha256_file(p)}
    write_json(os.path.join(out, names["manifest"]), manifest)
    sums = [(sha256_file(os.path.join(out, names[k])), names[k]) for k in ("data", "dictionary", "manifest", "problems")
            if os.path.exists(os.path.join(out, names[k]))]
    with open(os.path.join(out, "SHA256SUMS"), "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(f"{h}  {n}\n" for h, n in sums)
    prog.update(done=True, finished=manifest["finished"])
    write_json(os.path.join(out, PROGRESS), prog)
    print(f"SNAPSHOT: DONE ({prog['patents']} patents, {prog['claims']} claims, {prog['problems']} left out; {names['data']} "
          f"{os.path.getsize(data)} bytes, sha256 {sums[0][0]})")
    return 0


def check(a):
    """The finished snapshot's files against SHA256SUMS and the manifest, and the data file's rows against both."""
    out = a.out
    try:
        with open(os.path.join(out, "SHA256SUMS"), encoding="utf-8") as fh:
            sums = [ln.split("  ", 1) for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        print(f"CHECK: FAIL (no SHA256SUMS in {out})")
        return 1
    bad = [n for h, n in sums if not os.path.exists(os.path.join(out, n)) or sha256_file(os.path.join(out, n)) != h]
    man = next((n for _, n in sums if n.endswith("_manifest.json")), None)
    data = next((n for _, n in sums if n.endswith(".tsv.gz")), None)
    if bad or not man or not data:
        print(f"CHECK: FAIL (files that do not match SHA256SUMS: {bad or 'none'}; manifest {man}, data {data})")
        return 1
    with open(os.path.join(out, man), encoding="utf-8") as fh:
        m = json.load(fh)
    rows = 0
    with gzip.open(os.path.join(out, data), "rt", encoding="utf-8", newline="\n") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        for line in fh:
            if line.count("\t") != len(head) - 1:
                print(f"CHECK: FAIL (row {rows + 1} has {line.count(chr(9)) + 1} cells, the header {len(head)})")
                return 1
            rows += 1
    if rows != m["patents"] or head != m["columns"]:
        print(f"CHECK: FAIL ({data} has {rows} rows; the manifest says {m['patents']})")
        return 1
    print(f"CHECK: OK ({len(sums)} files match SHA256SUMS; {data} {rows} rows, {len(head)} columns, text {m['text_data_version']})")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="claims_counts_snapshot")
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("--text", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--part-size", type=int, default=PART_SIZE)
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--guard", choices=("box", "none"), default="box")
    r.add_argument("--margin-min", type=int, default=45)
    r.add_argument("--not-before", default=NOT_BEFORE)
    r.add_argument("--stop-after-parts", type=int, default=None)
    g = sub.add_parser("guard")
    g.add_argument("--margin-min", type=int, default=45)
    g.add_argument("--not-before", default=NOT_BEFORE)
    c = sub.add_parser("check")
    c.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if a.mode == "guard":
        why = guard_reasons(a.margin_min, a.not_before)
        print("GUARD: " + ("REFUSE (" + "; ".join(why) + ")" if why else "OK TO RUN"))
        return 2 if why else 0
    if a.mode == "check":
        return check(a)
    if a.part_size < 1 or a.workers < 1 or (a.limit is not None and a.limit < 1):
        print("SNAPSHOT: REFUSED (--part-size, --workers and --limit must be 1 or more)")
        return 2
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
