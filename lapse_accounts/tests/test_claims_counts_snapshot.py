"""Tests for the claim counts snapshot job (structure layer, gate S0.6; lapse_tools/claims_counts_snapshot.py).

On the long-text file of the claim tree's tests (claimgraph's 37 fixtures in build_text.py's layout): a straight run in parts;
a run stopped after two parts and resumed gives the same bytes; every row equals API/claim_counts.py on the fixture; the
dictionary, manifest and SHA256SUMS; check mode, and check of a changed file; the guard refusing at the start and pausing
between parts; a resume against other settings refused; the guard's own readings (systemctl, /proc/locks, not-before); the
reading query on the index; the work caps equal to the endpoint's.

  python -m pytest lapse_accounts/tests/test_claims_counts_snapshot.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import datetime
import gzip
import json
import os
import sqlite3

import pytest

from API import claim_counts, lapse_structure
from lapse_accounts.tests.conftest import _TMP
from lapse_accounts.tests.test_structure_claims import MANIFEST, TEXT_VERSION, build_files, fixture_rows
from lapse_tools import claims_counts_snapshot as snap


@pytest.fixture(scope="module")
def text():
    search, text = str(_TMP / "snap_search.db"), str(_TMP / "snap_text.db")
    build_files(search, text)
    return text


def run(text, out, *more):
    return snap.main(["run", "--text", text, "--out", str(out), "--guard", "none"] + [str(x) for x in more])


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8", newline="\n") as fh:
        return [ln.rstrip("\n").split("\t") for ln in fh]


def test_a_run_in_parts_matches_the_counting_code(text, tmp_path, capsys):
    out = tmp_path / "a"
    assert run(text, out, "--part-size", 10) == 0
    names = snap.files_of(TEXT_VERSION)
    rows = read_rows(out / names["data"])
    assert rows[0] == list(claim_counts.TSV_COLUMNS)
    body = rows[1:]
    assert [r[0] for r in body] == sorted(MANIFEST)            # patent_id order, as text
    for r in body:
        pc, _, _ = claim_counts.counts_from_rows(r[0], fixture_rows(r[0]))
        assert "\t".join(r) + "\n" == claim_counts.tsv_line(r[0], pc), r[0]
    prog = json.loads((out / "progress.json").read_text(encoding="utf-8"))
    assert prog["done"] and prog["patents"] == len(MANIFEST) and [p["patents"] for p in prog["parts"]] == [10, 10, 10, 7]
    man = json.loads((out / names["manifest"]).read_text(encoding="utf-8"))
    assert man["patents"] == 37 and man["text_data_version"] == TEXT_VERSION and man["columns"] == list(claim_counts.TSV_COLUMNS)
    assert man["counts_version"] == claim_counts.COUNTS_VERSION and man["left_out"] == 0
    sums = (out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert [ln.split("  ")[1] for ln in sums] == [names["data"], names["dictionary"], names["manifest"]]
    dic = (out / names["dictionary"]).read_text(encoding="utf-8").splitlines()
    assert dic[0] == "column\ttype\tdefinition\tuspto_variable"
    assert [ln.split("\t")[0] for ln in dic[1:]] == list(claim_counts.TSV_COLUMNS)
    assert snap.main(["check", "--out", str(out)]) == 0
    assert "CHECK: OK (3 files match SHA256SUMS" in capsys.readouterr().out


def test_a_stopped_run_resumes_to_the_same_bytes(text, tmp_path, capsys):
    straight, stopped = tmp_path / "s", tmp_path / "r"
    assert run(text, straight, "--part-size", 8) == 0
    assert run(text, stopped, "--part-size", 8, "--stop-after-parts", 2) == 3
    assert "SNAPSHOT: PAUSED (--stop-after-parts 2; 16 patents written" in capsys.readouterr().out
    prog = json.loads((stopped / "progress.json").read_text(encoding="utf-8"))
    assert len(prog["parts"]) == 2 and not prog["done"] and prog["last_patent_id"] == sorted(MANIFEST)[15]
    assert run(text, stopped, "--part-size", 8) == 0
    assert "resuming after" in capsys.readouterr().out
    name = snap.files_of(TEXT_VERSION)["data"]
    assert (stopped / name).read_bytes() == (straight / name).read_bytes()
    assert (stopped / "SHA256SUMS").read_text().split()[0] == (straight / "SHA256SUMS").read_text().split()[0]
    assert run(text, stopped, "--part-size", 8) == 0           # done: nothing more to do
    assert run(text, stopped, "--part-size", 9) == 2           # other settings than the parts were made with: refused


def test_limit_and_check_of_a_changed_file(text, tmp_path, capsys):
    out = tmp_path / "l"
    assert run(text, out, "--limit", 5, "--part-size", 2) == 0
    name = snap.files_of(TEXT_VERSION)["data"]
    assert len(read_rows(out / name)) == 6
    p = out / name
    raw = bytearray(p.read_bytes())
    raw[-12] ^= 0xFF
    p.write_bytes(bytes(raw))
    assert snap.main(["check", "--out", str(out)]) == 1
    assert "CHECK: FAIL" in capsys.readouterr().out


def test_the_guard_refuses_at_the_start_and_pauses_between_parts(text, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(snap, "guard_reasons", lambda *a, **k: ["patentref-backup.timer fires at 2026-11-06T03:31:08Z, within 45 min"])
    assert snap.main(["run", "--text", text, "--out", str(tmp_path / "g"), "--guard", "box"]) == 2
    assert "SNAPSHOT: REFUSED (patentref-backup.timer fires" in capsys.readouterr().out
    assert not (tmp_path / "g" / "progress.json").exists()
    calls = {"n": 0}

    def later(*a, **k):
        calls["n"] += 1
        return [] if calls["n"] == 1 else ["/data/lapse/heavy.lock is held"]
    monkeypatch.setattr(snap, "guard_reasons", later)
    assert snap.main(["run", "--text", text, "--out", str(tmp_path / "p"), "--guard", "box", "--part-size", "10"]) == 3
    o = capsys.readouterr().out
    assert "SNAPSHOT: PAUSED (/data/lapse/heavy.lock is held; 10 patents written" in o
    assert json.loads((tmp_path / "p" / "progress.json").read_text())["patents"] == 10


def test_the_guards_own_readings(monkeypatch, tmp_path):
    now = datetime.datetime(2026, 11, 6, 3, 0, tzinfo=datetime.timezone.utc)
    answers = {("show", "-p", "NextElapseUSecRealtime", "--value", "patentref-backup.timer"): "Fri 2026-11-06 03:31:08 UTC",
               ("is-active", "lapse-contract-probe.service"): "active",
               ("show", "-p", "ExecMainStartTimestamp", "--value", "lapse-refresh-grants.service"): "Fri 2026-11-06 02:00:00 UTC"}
    monkeypatch.setattr(snap, "_systemctl", lambda *a: answers.get(a, "inactive" if a[0] == "is-active" else ""))
    monkeypatch.setattr(snap, "LOCKS", (str(tmp_path / "no.lock"),))
    why = snap.guard_reasons(45, "2026-11-05T03:47:00Z", now=now)
    assert why == ["patentref-backup.timer fires at 2026-11-06T03:31:08Z, within 45 min", "lapse-contract-probe.service is running",
                   "inside the grants run's window (it started 2026-11-06T02:00:00Z; about 130 min)"]
    why = snap.guard_reasons(45, "2026-11-05T03:47:00Z", now=datetime.datetime(2026, 10, 29, 12, tzinfo=datetime.timezone.utc))
    assert why[0] == "not before 2026-11-05T03:47:00Z (gate 5.8's earliest tick)"
    monkeypatch.setattr(snap, "_systemctl", lambda *a: "")
    assert snap.guard_reasons(45, "", now=now) == []
    assert snap._systemd_time("n/a") is None and snap._systemd_time("Tue 2026-11-03 04:45:44 UTC").hour == 4


@pytest.mark.skipif(not os.path.exists("/proc/locks"), reason="/proc/locks is Linux only")
def test_a_held_lock_is_seen_and_never_taken(tmp_path):
    import fcntl
    p = tmp_path / "heavy.lock"
    p.write_text("")
    assert snap.lock_held(str(p)) is False
    with open(p) as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        assert snap.lock_held(str(p)) is True
    assert snap.lock_held(str(p)) is False


def test_the_reading_query_walks_the_index(text):
    con = sqlite3.connect(text)
    plan = " ".join(r[-1] for r in con.execute("EXPLAIN QUERY PLAN " + snap.STREAM_SQL, ("",)))
    assert "USING INDEX ix_g_claims_patent_id_claim_sequence" in plan and "TEMP B-TREE" not in plan, plan


def test_work_caps_and_settings():
    assert (snap.WORK_CAP_CLAIMS, snap.WORK_CAP_CHARS) == (lapse_structure.MAX_CLAIMS, lapse_structure.MAX_TEXT_CHARS)
    assert snap.NOT_BEFORE == "2026-11-05T03:47:00Z" and snap.PART_SIZE == 100_000
    assert set(snap.LOCKS) == {"/data/lapse/deploy.lock", "/data/lapse/heavy.lock", "/data/lapse/refresh.lock"}
    for words in ("SNAPSHOT: REFUSED", "SNAPSHOT: PAUSED", "SNAPSHOT: DONE", "CHECK: OK", "GUARD: "):
        assert words in open(snap.__file__, encoding="utf-8").read(), words
