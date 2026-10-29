"""The structure layer's usage counter (release X; lapse_accounts/requestlog.py, lapse_accounts/usage_counts.py and
manage.py structure_usage): the structure_log rows and their client tag, the count rule (keyed data calls below 500 other
than 403 and 429), the service's own keys left out the way the status page leaves them out, distinct key prefixes per
day and over a window, the share of outside calls, the MCP package's calls apart, retention, and the command's output
(counts only, written once a day). For the S1 tripwire of the structure layer spec.

  python -m pytest lapse_accounts/tests/test_structure_usage.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import sqlite3
import time
from datetime import date, datetime, timezone
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import RequestFactory

from lapse_accounts import requestlog, status, usage_counts

DAY = date(2026, 11, 10)
T0 = datetime(2026, 11, 10, tzinfo=timezone.utc).timestamp()
OWN = {"ownkey01"}
CLAIMS = "/api/v1/structure/claims/"


def _file(tmp_path, rows=(), srows=(), structure_log=True, name="usage.sqlite3"):
    path = str(tmp_path / name)
    con = requestlog.connect(path)
    if not structure_log:
        con.execute("DROP TABLE structure_log")
    con.executemany("INSERT INTO request_log VALUES (?,?,?,?,?,?)", rows)
    if structure_log:
        con.executemany("INSERT INTO structure_log VALUES (?,?,?,?,?)", srows)
    con.commit()
    con.close()
    return path


def _req(t, st, prefix, path, client=""):
    """One response as the middleware logs it: the request_log row, and the structure_log row under /api/v1/structure/."""
    row = (t, st, 3.0, prefix, path, "GET")
    srow = (t, st, prefix, path, client) if path.startswith("/api/v1/structure/") else None
    return row, srow


def _rows(reqs):
    pairs = [_req(*r) for r in reqs]
    return [a for a, _ in pairs], [b for _, b in pairs if b]


SAMPLE = [
    (T0 + 10, 200, "userkey1", CLAIMS + "10034028/", "mcp"),       # get_claim_tree from the MCP package
    (T0 + 20, 200, "userkey1", CLAIMS + "3995389/", "mcp"),
    (T0 + 30, 404, "userkey2", CLAIMS + "99999999/", ""),         # a 404 is a call (the query ran), as the monthly counter has it
    (T0 + 40, 200, "userkey3", CLAIMS + "10034028/", ""),
    (T0 + 50, 403, "", CLAIMS + "10034028/", ""),                 # keyless: refused, not a call, in structure_requests_all
    (T0 + 60, 429, "userkey4", CLAIMS + "10034028/", ""),         # throttled: not a call
    (T0 + 70, 500, "userkey4", CLAIMS + "10034028/", ""),         # a server error: not a call
    (T0 + 80, 200, "ownkey01", CLAIMS + "10034028/", ""),          # the service's own: counted apart
    (T0 + 90, 200, "ownkey01", CLAIMS + "10034028/", "mcp"),
    (T0 + 100, 200, "userkey1", "/api/v1/patent/", ""),
    (T0 + 110, 200, "userkey5", "/api/v1/patent/", ""),
    (T0 + 120, 400, "userkey5", "/api/v1/lapse/similar", ""),
    (T0 + 130, 200, "userkey6", "/api/v1/meta/usage/", ""),         # meta: not a data path
    (T0 + 140, 200, "ownkey01", "/api/v1/patent/", ""),             # own: not an outside call
    (T0 + 86400 + 5, 200, "userkey7", CLAIMS + "1/", ""),           # the next day
    (T0 - 5, 200, "userkey8", CLAIMS + "1/", ""),                   # the day before
]


def test_writer_and_middleware_write_structure_rows_with_the_client_tag(tmp_path, monkeypatch):
    path = str(tmp_path / "mw.sqlite3")
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    monkeypatch.setattr(settings, "USAGE_LOG_BACKEND", "sqlite")
    monkeypatch.setattr(requestlog, "writer", requestlog._Writer())
    mw = requestlog.RequestLogMiddleware(lambda req: HttpResponse("x"))
    mw.on = True
    rf = RequestFactory()
    fake = ".".join(("prefix12", "q" * 12))   # a made-up key: prefix, dot, rest
    mw(rf.get(CLAIMS + "10034028/", HTTP_X_API_KEY=fake, HTTP_USER_AGENT="patentref-mcp/0.3.0"))
    mw(rf.get(CLAIMS + "3995389/", HTTP_X_API_KEY=fake, HTTP_USER_AGENT="python-httpx/0.27.0"))
    mw(rf.get("/api/v1/structure/claims/1", HTTP_USER_AGENT="Patentref-MCP/9"))
    mw(rf.get("/api/v1/patent/", HTTP_X_API_KEY=fake, HTTP_USER_AGENT="patentref-mcp/0.3.0"))
    requestlog.writer.flush()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 4          # every response, as before
    rows = con.execute("SELECT status, prefix, path, client FROM structure_log ORDER BY rowid").fetchall()
    assert rows == [(200, "prefix12", CLAIMS + "10034028/", "mcp"), (200, "prefix12", CLAIMS + "3995389/", ""),
                    (200, "", "/api/v1/structure/claims/1", "mcp")]
    cols = [r[1] for r in con.execute("PRAGMA table_info(structure_log)")]
    assert cols == ["ts", "status", "prefix", "path", "client"]                      # no user agent, no key, no address
    assert [r[1] for r in con.execute("PRAGMA table_info(request_log)")] == ["ts", "status", "ms", "prefix", "path", "method"]
    dump = "\n".join(con.iterdump())
    assert "q" * 12 not in dump and "httpx" not in dump and "0.3.0" not in dump
    con.close()


def test_client_tag():
    assert requestlog.client_tag("patentref-mcp/0.3.0") == "mcp" and requestlog.client_tag("PatentRef-MCP/1.0 extra") == "mcp"
    assert requestlog.client_tag(None) == "" and requestlog.client_tag("") == "" and requestlog.client_tag("curl/8.4") == ""
    assert requestlog.client_tag("my patentref-mcp/0.3.0 wrapper") == ""


def test_retention_prunes_structure_rows_with_the_request_rows(tmp_path, monkeypatch):
    path = str(tmp_path / "prune.sqlite3")
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    w = requestlog._Writer()
    old = time.time() - (requestlog.RETENTION_DAYS + 1) * 86400
    w.add((old, 200, 1.0, "a", CLAIMS + "1/", "GET"), (old, 200, "a", CLAIMS + "1/", ""))
    w.flush()                                  # the first flush prunes (no prune yet in this writer): the old rows go
    now = time.time()
    w.add((now, 200, 1.0, "a", CLAIMS + "1/", "GET"), (now, 200, "a", CLAIMS + "1/", ""))
    w._last_prune = 0.0
    w.flush()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM structure_log").fetchone()[0] == 1
    con.close()


def test_a_failed_write_keeps_both_kinds_of_row(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", str(tmp_path / "no_such_dir" / "x.sqlite3"))
    w = requestlog._Writer()
    w.add((1.0, 200, 1.0, "a", CLAIMS + "1/", "GET"), (1.0, 200, "a", CLAIMS + "1/", "mcp"))
    w.flush()
    assert len(w._rows) == 1 and len(w._srows) == 1


def test_day_figures(tmp_path):
    path = _file(tmp_path, *_rows(SAMPLE))
    f = usage_counts.figures(path, T0, T0 + 86400, own=OWN)
    assert f["claim_tree_calls"] == 4 and f["claim_tree_keys"] == 3                   # userkey1 twice, userkey2, userkey3
    assert f["structure_calls"] == 4 and f["structure_keys"] == 3
    assert f["mcp_claim_tree_calls"] == 2 and f["mcp_claim_tree_keys"] == 1           # the own mcp row is left out
    assert f["outside_calls"] == 7 and f["outside_keys"] == 4                         # + /patent/ x2 and the similar 400
    assert f["share_percent"] == pytest.approx(57.143)
    assert f["structure_requests_all"] == 7 and f["own_structure_requests"] == 2
    assert f["covers_window"] is True and f["log_from"].startswith("2026-11-09T23:59")
    text = json.dumps(f)
    for p in ("userkey", "ownkey", "10034028", "3995389"):
        assert p not in text                                                         # counts only


def test_own_keys_are_the_status_pages_own_keys(tmp_path, monkeypatch):
    path = _file(tmp_path, *_rows(SAMPLE))
    monkeypatch.setattr(status, "_own_cache", {"at": time.time(), "prefixes": frozenset(OWN)})
    assert usage_counts.figures(path, T0, T0 + 86400) == usage_counts.figures(path, T0, T0 + 86400, own=OWN)
    everyone = usage_counts.figures(path, T0, T0 + 86400, own=set())                 # no own key file: everything counts
    assert everyone["claim_tree_calls"] == 6 and everyone["own_structure_requests"] == 0 and everyone["outside_calls"] == 10
    # the status page's figures over the same rows leave the same keys out
    req = status.request_figures(path, T0, own=OWN)
    assert req["own_requests"] == 3


def test_the_probe_and_beta_keys_are_left_out_of_both_counts(tmp_path, monkeypatch):
    """Release Y: the GitHub probe's key and the beta test account's key, read from their files in the process, are the
    service's own in the counter and in the status page alike (the key files here are made up, built at run time)."""
    def fake(p):
        return ".".join((p, "y" * 24))

    op, probe, beta = tmp_path / "op.txt", tmp_path / "probe_key.txt", tmp_path / "beta_test_account.env"
    op.write_text(fake("ownkey01") + "\n")
    probe.write_text(fake("probekey") + "\n")
    beta.write_text("BETA_TEST_EMAIL=someone@example.com\nBETA_TEST_API_KEY=" + fake("betakey1") + "\n")
    monkeypatch.setattr(settings, "LAPSE_OWN_KEY_FILES", None, raising=False)
    monkeypatch.setattr(settings, "LAPSE_OWN_KEY_EXTRA_FILES", [str(probe), (str(beta), "BETA_TEST_API_KEY")], raising=False)
    monkeypatch.setenv("LAPSE_API_KEY_FILE", str(op))
    monkeypatch.setattr(status, "_own_cache", {"at": 0.0, "prefixes": frozenset()})
    more = SAMPLE + [
        (T0 + 150, 200, "probekey", "/api/v1/patent/", ""),           # the probe's call every 5 minutes
        (T0 + 160, 200, "betakey1", CLAIMS + "10034028/", ""),        # a drill with the beta test account's key
        (T0 + 170, 200, "betakey1", "/api/v1/patent/", ""),
    ]
    path = _file(tmp_path, *_rows(more))
    f = usage_counts.figures(path, T0, T0 + 86400)
    assert f == usage_counts.figures(path, T0, T0 + 86400, own={"ownkey01", "probekey", "betakey1"})
    base = usage_counts.figures(_file(tmp_path, *_rows(SAMPLE), name="base.sqlite3"), T0, T0 + 86400, own=OWN)
    assert f["claim_tree_calls"] == base["claim_tree_calls"] and f["outside_calls"] == base["outside_calls"]
    assert f["outside_keys"] == base["outside_keys"] and f["own_structure_requests"] == base["own_structure_requests"] + 1
    req = status.request_figures(path, T0)
    assert req["own_requests"] == 6 and req["requests"] == status.request_figures(path, T0, own=OWN)["requests"] - 3


def test_without_a_structure_log_table_the_mcp_calls_are_not_told_apart(tmp_path):
    rows, _ = _rows(SAMPLE)
    path = _file(tmp_path, rows, structure_log=False)
    f = usage_counts.figures(path, T0, T0 + 86400, own=OWN)
    assert f["claim_tree_calls"] == 4 and f["mcp_claim_tree_calls"] is None and "not told apart" in f["note"]
    assert usage_counts.figures(str(tmp_path / "missing.sqlite3"), T0, T0 + 1, own=OWN)["note"] == "no usage-log file"


def test_day_row_and_the_trailing_window_count_distinct_keys_over_days(tmp_path):
    reqs = [(T0 - 10, 200, "", "/api/v1/meta/health/", "")]   # the log holds rows from before the window
    for d in range(30):                                   # one new outside key a day for 30 days, each calling the tree once
        reqs.append((T0 + d * 86400 + 60, 200, f"key{d:05d}", CLAIMS + "1/", "mcp" if d % 2 else ""))
        reqs.append((T0 + d * 86400 + 90, 200, "steady01", "/api/v1/patent/", ""))
    path = _file(tmp_path, *_rows(reqs))
    last = date(2026, 12, 9)
    row = usage_counts.day_row(last, path, own=OWN)
    assert row["day"] == "2026-12-09" and row["claim_tree_calls"] == 1 and row["claim_tree_keys"] == 1
    t = row["trailing_30d"]
    assert t["claim_tree_keys"] == 30 and t["claim_tree_calls"] == 30 and t["outside_calls"] == 60 and t["share_percent"] == 50.0
    assert t["mcp_claim_tree_calls"] == 15 and t["from"] == "2026-11-10T00:00:00Z" and t["covers_window"] is True
    assert usage_counts.day_row(DAY, path, own=OWN, trailing=False).get("trailing_30d") is None
    rows = usage_counts.last_days(3, now=datetime(2026, 12, 9, 12, tzinfo=timezone.utc).timestamp(), path=path, own=OWN)
    assert [r["day"] for r in rows] == ["2026-12-06", "2026-12-07", "2026-12-08", "2026-12-09"]
    assert all(r["claim_tree_keys"] == 1 and "trailing_30d" not in r for r in rows)


def test_tripwire_legs(tmp_path):
    reqs = [(T0 + i * 3600, 200, f"key{i:05d}", CLAIMS + "1/", "") for i in range(9)]
    reqs += [(T0 + i * 60, 200, "steady01", "/api/v1/patent/", "") for i in range(200)]
    path = _file(tmp_path, *_rows(reqs))
    now = T0 + 40 * 86400
    w = usage_counts.tripwire(DAY, path=path, own=OWN, now=now)
    assert w["claim_tree_keys"] == 9 and w["share_percent"] == pytest.approx(4.306)
    assert w["tripwire"] == {**w["tripwire"], "keys_10_or_more": False, "share_5_percent_or_more": False, "usage_leg_holds": False}
    assert w["window_open"] is False and w["days"] == 30 and w["since"] == "2026-11-10"
    path = _file(tmp_path, *_rows(reqs + [(T0 + 99, 200, "key99999", CLAIMS + "2/", "")]), name="ten.sqlite3")
    w = usage_counts.tripwire(DAY, path=path, own=OWN, now=now)
    assert w["claim_tree_keys"] == 10 and w["tripwire"]["keys_10_or_more"] is True and w["tripwire"]["usage_leg_holds"] is True
    assert w["tripwire"]["share_5_percent_or_more"] is False
    few = _file(tmp_path, *_rows([(T0, 200, "a1", CLAIMS + "1/", ""), (T0 + 1, 200, "a1", "/api/v1/patent/", "")]), name="few.sqlite3")
    w = usage_counts.tripwire(DAY, path=few, own=OWN, now=T0 + 3600)
    assert w["share_percent"] == 50.0 and w["tripwire"]["share_5_percent_or_more"] is True and w["window_open"] is True
    assert "counted by hand" in w["tripwire"]["rule"]


def _cmd(*args):
    out = StringIO()
    call_command("structure_usage", *args, stdout=out)
    return out.getvalue()


def test_the_command_writes_a_day_once_and_prints_counts_only(tmp_path, monkeypatch):
    path = _file(tmp_path, *_rows(SAMPLE))
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    monkeypatch.setattr(status, "_own_cache", {"at": time.time(), "prefixes": frozenset(OWN)})
    out = tmp_path / "usagecounts"
    first = json.loads(_cmd("day", "--date", "2026-11-10", "--out", str(out)))
    assert first["written"] is True and first["claim_tree_calls"] == 4 and first["claim_tree_keys"] == 3
    saved = json.loads((out / "2026-11-10.json").read_text(encoding="utf-8"))
    assert saved["day"] == "2026-11-10" and saved["outside_calls"] == 7 and "trailing_30d" in saved
    (out / "2026-11-10.json").write_text('{"kept": true}', encoding="utf-8")
    again = json.loads(_cmd("day", "--date", "2026-11-10", "--out", str(out)))
    assert again["written"] is False and json.loads((out / "2026-11-10.json").read_text(encoding="utf-8")) == {"kept": True}
    printed = _cmd("day", "--date", "2026-11-10")
    shown = _cmd("show", "--days", "2")
    trip = json.loads(_cmd("tripwire", "--since", "2026-11-10"))
    assert trip["since"] == "2026-11-10" and trip["days"] == 30 and isinstance(trip["claim_tree_keys"], int)
    assert set(trip["tripwire"]) == {"keys_10_or_more", "share_5_percent_or_more", "usage_leg_holds", "rule"}
    for text in (printed, shown, json.dumps(trip), (out / "2026-11-10.json").read_text(encoding="utf-8")):
        for p in ("userkey", "ownkey", "steady", "10034028", "secret"):
            assert p not in text
    assert len(json.loads(shown)["rows"]) == 3
    for bad in (("tripwire",), ("day", "--date", "11/10/2026"), ("show", "--days", "90"), ("tripwire", "--since", "2026-11-10", "--days", "40")):
        with pytest.raises(CommandError):
            _cmd(*bad)


def test_the_wording_of_the_counter():
    from API import lapse_wording

    for text in (usage_counts.__doc__, usage_counts.RULE, requestlog.__doc__):
        assert lapse_wording.check_banned(text) == []
        assert "—" not in text
