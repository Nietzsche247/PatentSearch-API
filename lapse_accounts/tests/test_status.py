"""Gate 5.4: the health route, the per-request log and the status page figures."""
import json
import sqlite3
import time

import pytest
from django.conf import settings

from lapse_accounts import requestlog, status


def test_health_no_key(client):
    r = client.get("/api/v1/meta/health/")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["db"] == "ok"
    assert body["data_version"] and body["version"]
    assert r["Cache-Control"] == "no-store"
    assert r["X-Data-Version"] == body["data_version"]


def test_health_503_when_db_missing(client, monkeypatch):
    monkeypatch.setitem(settings.LAPSE_SQLITE, "path", "/nonexistent/none.db")
    r = client.get("/api/v1/meta/health/")
    assert r.status_code == 503
    assert r.json()["ok"] is False


def test_request_log_rows(tmp_path, monkeypatch):
    path = str(tmp_path / "usage.sqlite3")
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    monkeypatch.setattr(settings, "USAGE_LOG_BACKEND", "sqlite")
    w = requestlog._Writer()
    t = time.time()
    w.add((t, 200, 12.5, "abcdefgh", "/api/v1/patent/", "GET"))
    w.add((t, 403, 0.4, "", "/api/v1/patent/", "GET"))
    w.add((t, 500, 20000.0, "abcdefgh", "/api/v1/patent/", "POST"))
    w.add((t, 200, 3.0, "", "/api/v1/meta/health/", "GET"))
    w.flush()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 4
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()
    f = status.request_figures(path, t - 60)
    assert f["requests"] == 3 and f["meta_requests"] == 1
    assert f["ok"] == 1 and f["rejected"] == 1 and f["server_errors"] == 1 and f["client_errors"] == 0
    assert f["admitted"] == 2 and f["p50_ms"] == 12.5 and f["p95_ms"] == 20000.0
    assert f["server_error_rate"] == pytest.approx(33.333, abs=0.01)


def test_request_log_middleware_records(client, monkeypatch, tmp_path):
    path = str(tmp_path / "usage2.sqlite3")
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    monkeypatch.setattr(settings, "USAGE_LOG_BACKEND", "sqlite")
    monkeypatch.setattr(requestlog, "writer", requestlog._Writer())
    # the middleware instance read its switch at construction; drive it directly
    from django.http import HttpResponse
    from django.test import RequestFactory

    mw = requestlog.RequestLogMiddleware(lambda req: HttpResponse("x"))
    mw.on = True

    req = RequestFactory().get("/api/v1/meta/health/", HTTP_X_API_KEY="prefix12.secretpart")
    mw(req)
    requestlog.writer.flush()
    con = sqlite3.connect(path)
    row = con.execute("SELECT status, prefix, path, method FROM request_log").fetchone()
    assert row == (200, "prefix12", "/api/v1/meta/health/", "GET")


def _hist(tmp_path, probes):
    p = tmp_path / "history.json"
    p.write_text(json.dumps({"probes": probes}))
    return str(p)


def test_uptime_rule(tmp_path):
    base = "2026-10-06T04:%02d:00Z"
    probes = [{"t": base % m, "ok": ok, "status": 200 if ok else 0, "ms": 100}
              for m, ok in ((0, True), (5, True), (10, False), (15, True), (20, False), (25, False), (30, False), (35, True), (40, True))]
    now = status.parse_iso("2026-10-06T04:45:00Z")
    u = status.uptime_figures(_hist(tmp_path, probes), now)
    # 04:10 alone: nothing. 04:20 -> 04:25 -> 04:30: two spans of 5 minutes.
    assert u["down_minutes"] == 10.0
    assert u["failed_probes"] == 4 and u["probes"] == 9
    assert u["elapsed_minutes"] == 58
    assert u["uptime_percent"] == pytest.approx(100 * (1 - 10 / 58), abs=0.001)
    assert len(u["outages"]) == 1 and u["outages"][0]["from"] == "2026-10-06T04:20:00Z" and u["outages"][0]["to"] == "2026-10-06T04:30:00Z"
    assert u["last_probe"]["ok"] is True


def test_uptime_gap_cap_and_open_outage(tmp_path):
    probes = [{"t": "2026-10-06T05:00:00Z", "ok": False}, {"t": "2026-10-06T07:00:00Z", "ok": False}]
    u = status.uptime_figures(_hist(tmp_path, probes), status.parse_iso("2026-10-06T07:01:00Z"))
    assert u["down_minutes"] == status.MAX_DOWN_SPAN_MIN  # a two-hour gap between runs is missing runs, not two hours down
    assert u["outages"][-1].get("open") is True


def test_uptime_no_history(tmp_path):
    u = status.uptime_figures(str(tmp_path / "missing.json"))
    assert u["uptime_percent"] is None and "no probe history" in u["note"]


def test_status_page_and_json(client, monkeypatch, tmp_path):
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path),
                                                         "backup_journal": str(tmp_path / "bj.jsonl")})
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "alerts.jsonl").write_text(json.dumps({"time": status.iso(time.time()), "check": "disk_data", "state": "breach", "detail": "12 GB free"}) + "\n")
    (tmp_path / "bj.jsonl").write_text('{"time":"2026-10-07T00:18:26Z","ok":true,"drive":"ok","storage_box":"skipped","seconds":95}\n')
    r = client.get("/api/v1/meta/status/")
    assert r.status_code == 200 and r["Content-Type"].startswith("text/html")
    assert r["Cache-Control"] == "public, max-age=60"
    html = r.content.decode()
    assert "Status | PatentRef" in html and "Degraded" in html and "disk_data" in html and "prefers-color-scheme: dark" in html
    assert "n/a" in html  # no probe history: the uptime tile says so instead of a number
    assert "—" not in html
    j = client.get("/api/v1/meta/status.json")
    assert j.status_code == 200
    d = j.json()
    assert d["state"][0] == "degraded" and d["box"]["backup"]["ok"] is True and d["alerts"]["open"][0]["check"] == "disk_data"
    assert d["uptime"]["clock_start"] == "2026-10-06T03:47:00Z"
    j2 = client.get("/api/v1/meta/status/?format=json")
    assert j2.json()["generated_at"] == d["generated_at"]  # the 60 s cache served the same figures
