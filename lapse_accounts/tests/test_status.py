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
    assert "patentref_version" in body and "text_data_version" in body  # the pin-drift guard reads both commits here
    assert r["Cache-Control"] == "no-store"
    assert r["X-Data-Version"] == body["data_version"]


def test_health_answers_head_without_a_body(client, monkeypatch):
    """UptimeRobot's HTTP check sends HEAD first; until 2026-10-15 the route answered 405 and every check fell back to GET."""
    r = client.head("/api/v1/meta/health/")
    assert r.status_code == 200 and r.content == b""
    assert r["Cache-Control"] == "no-store" and r["X-Data-Version"]
    monkeypatch.setitem(settings.LAPSE_SQLITE, "path", "/nonexistent/none.db")
    assert client.head("/api/v1/meta/health/").status_code == 503
    assert client.post("/api/v1/meta/health/").status_code == 405


def _ur_mirror(tmp_path, read_ago_s, state="up", fetch_ok=True):
    t = time.time()
    doc = {"source": "UptimeRobot", "fetched_at": status.iso(t - read_ago_s), "fetch_ok": fetch_ok, "error": None if fetch_ok else "HTTP 401",
           "last_ok_at": status.iso(t - read_ago_s),
           "monitor": {"url": "https://patentref.io/api/v1/meta/health/", "state": state, "status_raw": state.upper(), "interval_s": 300,
                       "state_since": status.iso(t - 3600), "last_check_at": status.iso(t - read_ago_s - 120), "last_check_ms": 108,
                       "checks_24h": 288, "regions": ["eu"],
                       "last_incident": {"started_at": "2026-10-15T02:52:31.361Z", "duration_s": 276, "reason": "404 Not Found", "status": "Resolved"}}}
    d = tmp_path / "status_mirror"
    d.mkdir(exist_ok=True)
    (d / "uptimerobot.json").write_text(json.dumps(doc))
    return str(d / "uptimerobot.json")


def test_external_check_figures(tmp_path):
    x = status.external_check_figures(str(tmp_path / "missing.json"))
    assert x["configured"] is False and x["state"] is None and x["note"] == "not set up yet"
    x = status.external_check_figures(_ur_mirror(tmp_path, 60))
    assert x["configured"] and x["fresh"] and x["state"] == "up" and x["interval_minutes"] == 5 and x["last_check_age_minutes"] == 3
    assert x["last_incident"] == {"started_at": "2026-10-15T02:52:31Z", "duration_s": 276, "reason": "404 Not Found", "status": "resolved"}
    assert x["regions"] == ["eu"] and x["checks_24h"] == 288 and x["note"] == ""
    x = status.external_check_figures(_ur_mirror(tmp_path, 20 * 60, fetch_ok=False))
    assert x["fresh"] is False and x["note"] == "the last read of the UptimeRobot record failed"


def test_status_page_shows_the_external_check(client, monkeypatch, tmp_path):
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path),
                                                         "backup_journal": str(tmp_path / "bj.jsonl")})
    (tmp_path / "reports").mkdir()
    html = client.get("/api/v1/meta/status/").content.decode()
    assert "External check" in html and "its record is not set up yet" in html
    _ur_mirror(tmp_path, 60)
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    html = client.get("/api/v1/meta/status/").content.decode()
    assert "UptimeRobot checks <code>/api/v1/meta/health/</code> from its own network (region EU) every 5 minutes" in html
    assert '<span class="pill ok">up</span>' in html and "answered in 108 ms" in html and "stale" not in html
    d = client.get("/api/v1/meta/status.json").json()
    assert d["external_check"]["state"] == "up" and d["external_check"]["fresh"] is True and d["state"][0] == "ok"
    text = client.get("/api/v1/meta/status.json").content.decode()
    for banned in (str(tmp_path), "apiKey", "status_mirror"):
        assert banned not in text, banned
    low = html.lower()
    for banned in ("gate 5", "desk", "checklist", "ops/"):
        assert banned not in low, banned
    _ur_mirror(tmp_path, 60, state="down")
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    d = client.get("/api/v1/meta/status.json").json()
    assert d["state"] == ["degraded", "UptimeRobot reports the API down from its network; the box itself answers."]
    _ur_mirror(tmp_path, 30 * 60, state="down")  # an old reading never changes the state line
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    d = client.get("/api/v1/meta/status.json").json()
    assert d["state"][0] == "ok" and d["external_check"]["fresh"] is False
    assert "stale" in client.get("/api/v1/meta/status/").content.decode()


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
    w.add((t, 404, 1.0, "", "/wp-admin/install.php", "GET"))
    w.flush()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 5
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()
    f = status.request_figures(path, t - 60)
    assert f["requests"] == 3 and f["meta_requests"] == 1 and f["other_requests"] == 1
    assert f["ok"] == 1 and f["rejected"] == 1 and f["server_errors"] == 1 and f["client_errors"] == 0
    assert f["admitted"] == 2 and f["p50_ms"] == 12.5 and f["p95_ms"] == 20000.0
    assert f["server_error_rate"] == pytest.approx(33.333, abs=0.01)


def _log_rows(path, rows):
    con = requestlog.connect(path)
    con.executemany("INSERT INTO request_log VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def test_request_figures_count_documented_501s_and_own_requests_apart(tmp_path):
    """Independent audit 7, B22: the 24-hour 5xx figure read 7 percent on the box, all of it the documented 501s of the
    deferred endpoints and the contract run's deliberate 500s. Those are not server errors of the service."""
    path = str(tmp_path / "usage_b22.sqlite3")
    t = time.time()
    _log_rows(path, [
        (t, 200, 10.0, "userkey1", "/api/v1/patent/", "GET"),
        (t, 501, 2.0, "userkey1", "/api/v1/publication/", "GET"),           # documented: not served
        (t, 501, 2.0, "userkey1", "/api/v1/publication/rel_app_text/", "GET"),
        (t, 501, 2.0, "userkey1", "/api/v1/wipo", "GET"),
        (t, 501, 2.0, "userkey1", "/api/v1/nber_subcategory/12/", "GET"),
        (t, 500, 30.0, "userkey1", "/api/v1/patent/", "GET"),               # a server error another client got
        (t, 501, 3.0, "userkey1", "/api/v1/patent/", "GET"),                # a 501 on a served route is one too
        (t, 403, 0.2, "", "/api/v1/patent/", "GET"),
        (t, 500, 4.0, "ownkey01", "/api/v1/patent/", "GET"),                # the contract run's deliberate 500
        (t, 501, 1.0, "ownkey01", "/api/v1/cpc_class/", "GET"),
        (t, 200, 900.0, "ownkey01", "/api/v1/patent/", "GET"),              # the service's own latency is not shown either
        (t, 200, 1.0, "ownkey01", "/api/v1/meta/usage/", "GET"),            # meta and other paths are counted as before
        (t - 7200, 500, 1.0, "userkey1", "/api/v1/patent/", "GET"),         # outside the window
    ])
    f = status.request_figures(path, t - 60, own={"ownkey01"})
    assert f["requests"] == 8 and f["own_requests"] == 3 and f["meta_requests"] == 1
    assert f["ok"] == 1 and f["rejected"] == 1 and f["client_errors"] == 0
    assert f["not_served"] == 4 and f["server_errors"] == 2
    assert f["server_error_rate"] == pytest.approx(25.0) and f["error_rate"] == pytest.approx(25.0)
    assert f["max_ms"] == 30.0  # the own 900 ms request is not in the latency figures
    f = status.request_figures(path, t - 60, own=set())  # without the operator key everything counts, the 501s still apart
    assert f["requests"] == 11 and f["own_requests"] == 0 and f["not_served"] == 5 and f["server_errors"] == 3


def test_own_key_prefixes_from_the_operator_key_file(tmp_path, monkeypatch):
    monkeypatch.setattr(status, "_own_cache", {"at": 0.0, "prefixes": frozenset()})
    k = tmp_path / "test_api_key.txt"
    k.write_text("abcd1234.notarealsecretvalue\n")
    monkeypatch.setattr(settings, "LAPSE_OWN_KEY_FILES", None, raising=False)
    monkeypatch.setenv("LAPSE_API_KEY_FILE", str(k))
    assert status.own_key_prefixes() == {"abcd1234"}
    k.write_text("rotated9.another\n")  # read again only after OWN_KEY_SECONDS
    assert status.own_key_prefixes() == {"abcd1234"}
    monkeypatch.setattr(status, "_own_cache", {"at": 0.0, "prefixes": frozenset()})
    assert status.own_key_prefixes() == {"rotated9"}
    for files, want in (([str(tmp_path / "missing.txt")], set()), ([str(k)], {"rotated9"})):
        monkeypatch.setattr(status, "_own_cache", {"at": 0.0, "prefixes": frozenset()})
        monkeypatch.setattr(settings, "LAPSE_OWN_KEY_FILES", files, raising=False)
        assert status.own_key_prefixes() == want
    k.write_text("nodotinthisline\n")  # never compare a whole key
    monkeypatch.setattr(status, "_own_cache", {"at": 0.0, "prefixes": frozenset()})
    assert status.own_key_prefixes() == set()


def test_deferred_routes_are_the_routes_of_the_deferred_data_sets():
    """DEFERRED_ROUTES names exactly the first path segments of API/urls.py whose view reads an index of
    DEFERRED_INDICES, and no route of a served data set shares one."""
    import re

    from API import urls
    from API.lapse_errors import DEFERRED_INDICES, DEFERRED_ROUTES, is_deferred_route

    deferred, served = set(), set()
    for p in urls.urlpatterns:
        cls = getattr(p.callback, "view_class", None) or getattr(p.callback, "cls", None)
        try:
            index = getattr(cls(), "index", None)
        except Exception:  # noqa: BLE001 - a view that needs arguments to build reads no index
            index = None
        seg = re.split(r"[/?<$]", str(p.pattern).lstrip("^"))[0]
        if index is None:
            continue
        (deferred if index in DEFERRED_INDICES else served).add(seg)
    assert deferred == set(DEFERRED_ROUTES)
    assert not deferred & served
    assert is_deferred_route("/api/v1/publication/rel_app_text/") and is_deferred_route("/api/v1/wipo")
    assert not is_deferred_route("/api/v1/patent/rel_app_text/") and not is_deferred_route("/api/v1/g_claim/")
    assert not is_deferred_route("/api/v1/meta/") and not is_deferred_route("/registry/publication/")


def test_status_page_shows_not_served_and_own_checks(client, monkeypatch, tmp_path):
    path = str(tmp_path / "usage_page.sqlite3")
    t = time.time()
    _log_rows(path, [(t, 501, 2.0, "userkey1", "/api/v1/publication/", "GET"), (t, 500, 4.0, "ownkey01", "/api/v1/patent/", "GET")])
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(status, "_own_cache", {"at": time.time(), "prefixes": frozenset({"ownkey01"})})
    d = client.get("/api/v1/meta/status.json").json()["requests_24h"]
    assert d["requests"] == 1 and d["not_served"] == 1 and d["server_errors"] == 0 and d["own_requests"] == 1
    assert d["server_error_rate"] == 0.0
    html = client.get("/api/v1/meta/status/").content.decode()
    assert "Not served (documented 501)" in html and "own checks (not in the figures above)" in html
    text = client.get("/api/v1/meta/status.json").content.decode()
    assert "ownkey01" not in text and "ownkey01" not in html  # no key prefix reaches a reader
    low = html.lower()
    for banned in ("contract", "deploy", "audit", "gate ", "bundle", "test_api_key", "operator key"):
        assert banned not in low, banned


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


def test_official_uptime_github_before_the_switch_uptimerobot_after(tmp_path):
    # GitHub: a 10-minute outage before the switch, and a 5-minute one after it (which no longer counts)
    probes = [{"t": t, "ok": ok} for t, ok in (("2026-10-10T00:00:00Z", True), ("2026-10-10T00:05:00Z", False), ("2026-10-10T00:10:00Z", False),
                                               ("2026-10-10T00:15:00Z", False), ("2026-10-10T00:20:00Z", True),
                                               ("2026-10-16T00:00:00Z", False), ("2026-10-16T00:05:00Z", False), ("2026-10-16T00:10:00Z", True))]
    now = status.parse_iso("2026-10-17T00:00:00Z")
    up = status.uptime_figures(_hist(tmp_path, probes), now)
    assert up["down_minutes"] == 15.0 and up["down_minutes_before_switch"] == 10.0
    # no UptimeRobot reading on the box: the GitHub rule counts after the switch too
    o = status.official_uptime(up, status.external_check_figures(str(tmp_path / "missing.json"), now), now)
    assert o["down_minutes"] == 15.0 and o["observer_after"] == "GitHub probe" and "not on the box yet" in o["note"]
    # with the mirror: the setup incident before the switch is not counted; a resolved 6-minute incident after it is,
    # and an open one counts up to now while the reading is fresh
    path = tmp_path / "status_mirror" / "uptimerobot.json"
    path.parent.mkdir(exist_ok=True)
    doc = {"fetch_ok": True, "last_ok_at": "2026-10-16T23:59:00Z", "monitor": {"state": "down", "interval_s": 300},
           "incidents": [{"started_at": "2026-10-15T02:52:31.361Z", "duration_s": 276, "status": "Resolved", "reason": "404 Not Found"},
                         {"started_at": "2026-10-16T12:00:00Z", "duration_s": 360, "status": "Resolved", "reason": "503 Service Unavailable"},
                         {"started_at": "2026-10-16T23:50:00Z", "duration_s": 300, "status": "Ongoing", "reason": "Connection Timeout"}]}
    path.write_text(json.dumps(doc))
    ext = status.external_check_figures(str(path), now)
    o = status.official_uptime(up, ext, now)
    assert o["observer_after"] == "UptimeRobot" and o["down_minutes_before_switch"] == 10.0
    assert o["down_minutes_after_switch"] == 16.0  # 6 + 10 (the open one, 23:50 to now)
    assert o["down_minutes"] == 26.0 and [r["minutes"] for r in o["incidents_after_switch"]] == [6.0, 10.0]
    elapsed = int((now - status.parse_iso(status.UPTIME_CLOCK_START)) / 60)
    assert o["elapsed_minutes"] == elapsed and o["uptime_percent"] == pytest.approx(100 * (1 - 26 / elapsed), abs=0.0001)
    # a stale reading: the open incident counts only up to the last read, and the note says so
    later = now + 3600
    o = status.official_uptime(status.uptime_figures(_hist(tmp_path, probes), later), status.external_check_figures(str(path), later), later)
    assert o["down_minutes_after_switch"] == 15.0 and "last read 61 minutes ago" in o["note"]


def test_status_page_tile_uses_the_official_figure(client, monkeypatch, tmp_path):
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path),
                                                         "backup_journal": str(tmp_path / "bj.jsonl")})
    (tmp_path / "reports").mkdir()
    _ur_mirror(tmp_path, 60)
    d = client.get("/api/v1/meta/status.json").json()
    assert d["uptime_official"]["observer_after"] == "UptimeRobot" and d["uptime_official"]["switch_at"] == "2026-10-15T02:57:08Z"
    html = client.get("/api/v1/meta/status/").content.decode()
    assert "the GitHub probe until 2026-10-15 02:57 UTC" in html and "then UptimeRobot" in html
    assert "its incidents are the down time of the uptime figure" in html


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


def test_public_status_shows_no_paths_desk_ids_or_sizes(client, monkeypatch, tmp_path):
    """2.11 re-audit 2026-10-08: status.json is keyless and carried a server path, desk ids, disk and memory sizes.
    Both public forms now go through public_view()."""
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path),
                                                         "backup_journal": str(tmp_path / "bj.jsonl"), "disks": ["/"]})
    (tmp_path / "reports").mkdir()
    (tmp_path / "status_mirror").mkdir()
    (tmp_path / "status_mirror" / "history.json").write_text(json.dumps({"probes": [{"t": status.iso(time.time() - 60), "ok": True, "status": 200}]}))
    (tmp_path / "reports" / "alerts.jsonl").write_text(json.dumps({"time": status.iso(time.time()), "check": "disk_data", "state": "breach",
                                                                   "detail": "/data: 12.0 GB free (1.0%)", "host": "patentref-us1", "desk_id": "0d1609c2c8"}) + "\n")
    (tmp_path / "reports" / "watchdog_state.json").write_text(json.dumps({"last_run": status.iso(time.time()), "checks": {
        "disk_root": {"ok": True, "fails": 0, "detail": "/: 106.1 GB free (84.0%)", "checked": "2026-10-08T23:37:13Z", "desk_id": None},
        "disk_data": {"ok": False, "fails": 3, "detail": "/data: 12.0 GB free (1.0%), threshold 250 GB", "checked": "2026-10-08T23:37:13Z",
                      "breach_since": "2026-10-08T23:30:00Z", "desk_id": "0d1609c2c8"},
        "memory": {"ok": True, "fails": 0, "detail": "MemAvailable 55.1 GB, threshold 4 GB", "desk_id": "9c141fcaa7"}}}))
    (tmp_path / "bj.jsonl").write_text('{"time":"2026-10-07T00:18:26Z","ok":true,"drive":"ok","storage_box":"skipped","seconds":95}\n')

    full = status.collect()
    assert full["uptime"]["source"].startswith(str(tmp_path))  # the internal figures still know where they came from
    assert full["box"]["disks"][0]["total_gb"] > 0

    r = client.get("/api/v1/meta/status.json")
    assert r.status_code == 200
    text = r.content.decode()
    d = r.json()
    for banned in (str(tmp_path), "/data", "0d1609c2c8", "9c141fcaa7", "desk_id", "_gb", "GB", "MemAvailable", "patentref-us1",
                   '"load"', '"cpus"', '"detail"', '"mount"'):
        assert banned not in text, banned
    assert d["uptime"]["source"] == status.PUBLIC_PROBE_SOURCE
    assert d["box"]["disks"] == [{"name": "system", "free_percent": d["box"]["disks"][0]["free_percent"], "ok": True}]
    assert isinstance(d["box"]["disks"][0]["free_percent"], float)
    assert d["box"]["memory"]["ok"] is True and "available_percent" in d["box"]["memory"]
    assert d["box"]["watchdog"]["checks"]["disk_data"] == {"ok": False, "fails": 3, "checked": "2026-10-08T23:37:13Z",
                                                          "breach_since": "2026-10-08T23:30:00Z", "last_alert": None}
    assert d["alerts"]["open"][0] == {"time": d["alerts"]["open"][0]["time"], "check": "disk_data", "state": "breach"}
    assert d["state"][0] == "degraded"

    html = client.get("/api/v1/meta/status/").content.decode()
    for banned in (str(tmp_path), "/data", "0d1609c2c8", "GB", "MemAvailable", "CPUs", "Load average"):
        assert banned not in html, banned
    assert "Disk, system" in html and "disk_data" in html
    # 2026-10-11 audits, finding G1: no internal machinery on the public page (it read "(gate 5.8 clock)" and "operator desk")
    low = html.lower()
    for banned in ("gate 5", "desk", "checklist", "scaffold", "ops/"):
        assert banned not in low, banned


def test_status_page_names_the_build_checks_in_plain_words(client, monkeypatch, tmp_path):
    """Independent audit 3 of 2026-10-12, B4: the HTML page gives the build checks plain labels; status.json keeps the keys
    (stable field names)."""
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path),
                                                         "backup_journal": str(tmp_path / "bj.jsonl")})
    (tmp_path / "reports").mkdir()
    keys = ["fetch", "tripwire", "build", "integrity", "count_deltas", "contract_examples", "backtest_gate", "expiry_audit", "new_check"]
    (tmp_path / "snapshot_current.json").write_text(json.dumps({
        "data_version": "20261006.5", "verdict": "PASS", "built_at": "2026-10-09T01:20:00Z", "swapped": True,
        "swapped_at": "2026-10-09T01:23:38Z", "gates": {k: {"status": "pass"} for k in keys}}))
    html = client.get("/api/v1/meta/status/").content.decode()
    # the labels of the bundle 4 wording review of 2026-10-12 (patentref ops/reviews/2026-10-12_bundle-4_wording_review.md, item 1)
    assert ("Checks of the live build</dt><dd>USPTO files fetched: pass; file formats unchanged: pass; database built: pass; database "
            "integrity: pass; row counts against the previous build: pass; the PatentsView API's example queries: pass; "
            "history-match score tested on past patents: pass; expiry audit of known patents: pass; new check: pass</dd>") in html.replace("&#x27;", "'")
    for code in ("count_deltas", "tripwire", "contract_examples", "backtest_gate", "expiry_audit", "<code>fetch"):
        assert code not in html, code
    d = client.get("/api/v1/meta/status.json").json()
    assert list(d["refresh"]["current"]["gates"]) == keys and d["refresh"]["current"]["gates"]["count_deltas"] == "pass"
