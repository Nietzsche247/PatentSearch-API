"""Health route and the public status page (PatentRef gate 5.4).

  GET /api/v1/meta/health/      no auth, no throttle, no search query: {"ok", "version", "data_version", "db",
                                "time", "text_data_version", "patentref_version"} (version is the fork's
                                commit, patentref_version the /srv/lapse commit, both for the 5.2 pin
                                guard); 200 when the served search file opens and answers a one-row read,
                                503 otherwise. This is what the external probe and the on-box watchdog call.
  GET /api/v1/meta/status/      the public status page (HTML, phone width, dark mode); the same figures as
                                JSON at /api/v1/meta/status.json. Built from:
    - request_log in the usage-log file (lapse_accounts.requestlog): counts, error rates, p50 and p95 for
      the last 24 hours and 30 days;
    - the external probe history mirrored to the box by the watchdog (ops/systemd/watchdog.sh in the
      patentref repo) from the public status repository: uptime since the 5.8 clock started;
    - the refresh runner's state (refresh_state.json, the newest refresh_check log, snapshot_next.json,
      snapshot_current.json);
    - the nightly backup journal, the watchdog state and the alerts log;
    - disk and memory headroom read live.
  Everything is computed at most once a minute per worker and served with Cache-Control max-age=60.
  Both public forms go through public_view(): no server paths, desk ids, disk or memory sizes, host names
  or load figures reach a keyless reader (2.11 re-audit 2026-10-08).

Uptime rule (ops/monitoring.md in the patentref repo): probes run every 5 minutes from GitHub's network;
the span between two consecutive failed probes counts as down; a lone failed probe between two passes
counts as nothing; uptime = 1 - down minutes / minutes elapsed since the clock started.
"""
import copy
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

from lapse_accounts import public

CACHE_SECONDS = 60
UPTIME_CLOCK_START = "2026-10-06T03:47:00Z"  # checklist 5.8: the weekly refresh task went live
PROBE_CADENCE_MIN = 5
MAX_DOWN_SPAN_MIN = 3 * PROBE_CADENCE_MIN  # a gap longer than this between two failed probes is a missing run, not an outage

_lock = threading.Lock()
_cache = {"at": 0.0, "data": None}
_version_cache = {}


def paths():
    p = getattr(settings, "LAPSE_STATUS_PATHS", {})
    data = p.get("lapse_data", "/data/lapse")
    api = p.get("api_dir", "/data/api")
    return {
        "request_log": getattr(settings, "USAGE_LOG_SQLITE_PATH", ""),
        "probe_history": p.get("probe_history", f"{data}/status_mirror/history.json"),
        "refresh_state": p.get("refresh_state", f"{data}/refresh_state.json"),
        "refresh_logs": p.get("refresh_logs", f"{data}/logs"),
        "next_sidecar": p.get("next_sidecar", f"{api}/snapshot_next.json"),
        "current_sidecar": p.get("current_sidecar", f"{api}/snapshot_current.json"),
        "backup_journal": p.get("backup_journal", "/data/backups/backup_journal.jsonl"),
        "alerts": p.get("alerts", f"{data}/reports/alerts.jsonl"),
        "watchdog_state": p.get("watchdog_state", f"{data}/reports/watchdog_state.json"),
        "disks": p.get("disks", ["/", "/data"]),
    }


def now_utc():
    return datetime.now(timezone.utc)


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError, AttributeError):
            return None


def _git_short(repo):
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def code_version():
    """Short commit of the fork serving this process, read once with git (the repo directory is the
    parent of the package); 'unknown' when git or the repo is not there."""
    if "v" not in _version_cache:
        _version_cache["v"] = _git_short(Path(__file__).resolve().parent.parent)
    return _version_cache["v"]


def patentref_version():
    """Short commit of the patentref clone this server reads (LAPSE_REPO_DIR, /srv/lapse on the box: the
    expiry engine, public/ and LICENSES.md), read once a minute so a pull without a reload still shows.
    The pin-drift guard (patentref ops/check_pins.py, checklist 5.2) compares it with ops/deploy.pins."""
    now = time.time()
    hit = _version_cache.get("p")
    if hit and now - hit[0] < 60:
        return hit[1]
    repo = getattr(settings, "LAPSE_REPO_DIR", None) or os.environ.get("LAPSE_REPO_DIR", "/srv/lapse")
    v = _git_short(repo)
    _version_cache["p"] = (now, v)
    return v


def db_check():
    """(ok, data_version, detail): open the served search file read-only and read one row from the patent
    index; never a query a client could make expensive."""
    try:
        from API.search_sqlite import LapseSQLiteSearch

        s = LapseSQLiteSearch.from_django_settings()
        con = s.connection()
        row = con.execute("SELECT tbl FROM _lapse_indices WHERE idx='patents'").fetchone()
        if row is None:
            return False, s.data_version(), "no patents index"
        con.execute(f'SELECT 1 FROM "{row[0]}" LIMIT 1').fetchone()
        return True, s.data_version(), "ok"
    except Exception as exc:  # any failure is a 503 with the class name, no internals
        return False, getattr(settings, "LAPSE_DATA_VERSION", "") or "", exc.__class__.__name__


def text_version():
    """The attached long-text file's data_version (PatentRef 1.6), or None when the box serves none."""
    try:
        from API.search_sqlite import LapseSQLiteSearch

        return LapseSQLiteSearch.from_django_settings().text_data_version()
    except Exception:  # noqa: BLE001
        return None


@require_GET
def health(request):
    ok, data_version, detail = db_check()
    body = {"ok": ok, "version": code_version(), "data_version": data_version, "db": detail, "time": iso(time.time()),
            "text_data_version": text_version(), "patentref_version": patentref_version()}
    resp = JsonResponse(body, status=200 if ok else 503)
    resp["Cache-Control"] = "no-store"
    resp["X-Data-Version"] = data_version or "unversioned"
    return resp


# ---------------------------------------------------------------- figures


def pct(sorted_values, q):
    if not sorted_values:
        return None
    k = max(0, min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[k]


def request_figures(path, since, api_prefix="/api/v1/", meta_prefix="/api/v1/meta/"):
    """Counts and latency over request_log rows newer than `since` (epoch). API figures cover the data
    paths (under /api/v1/ but not /api/v1/meta/); the meta paths (sign-up, keys, usage, health, this page)
    and everything else (scanners asking for wp-admin, the root redirect) are counted apart so probes and
    noise do not pad the numbers."""
    out = {"requests": 0, "meta_requests": 0, "other_requests": 0, "ok": 0, "client_errors": 0, "rejected": 0, "server_errors": 0,
           "admitted": 0, "p50_ms": None, "p95_ms": None, "max_ms": None, "error_rate": None, "server_error_rate": None}
    if not path or not os.path.exists(path):
        return out
    api_where = "ts >= ? AND path LIKE ? AND path NOT LIKE ?"
    api_args = (since, api_prefix + "%", meta_prefix + "%")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='request_log'").fetchone():
            con.close()
            return out
        rows = con.execute(f"SELECT status, COUNT(*) FROM request_log WHERE {api_where} GROUP BY status", api_args).fetchall()
        out["meta_requests"] = con.execute("SELECT COUNT(*) FROM request_log WHERE ts >= ? AND path LIKE ?", (since, meta_prefix + "%")).fetchone()[0]
        out["other_requests"] = con.execute("SELECT COUNT(*) FROM request_log WHERE ts >= ? AND path NOT LIKE ?", (since, api_prefix + "%")).fetchone()[0]
        total = sum(c for _, c in rows)
        out["requests"] = total
        for status, c in rows:
            if status >= 500:
                out["server_errors"] += c
            elif status in (403, 429):
                out["rejected"] += c
            elif status >= 400:
                out["client_errors"] += c
            else:
                out["ok"] += c
        if total:
            # latency over admitted requests (not the 403/429 refusals, which answer in microseconds)
            n = con.execute(f"SELECT COUNT(*) FROM request_log WHERE {api_where} AND status NOT IN (403, 429)", api_args).fetchone()[0]
            out["admitted"] = n
            if n:
                def nth(q):
                    k = max(0, min(n - 1, int(round(q * (n - 1)))))
                    return con.execute(f"SELECT ms FROM request_log WHERE {api_where} AND status NOT IN (403, 429) ORDER BY ms LIMIT 1 OFFSET ?",
                                       api_args + (k,)).fetchone()[0]
                out["p50_ms"] = round(nth(0.5), 1)
                out["p95_ms"] = round(nth(0.95), 1)
                out["max_ms"] = round(nth(1.0), 1)
            out["error_rate"] = round(100.0 * (out["client_errors"] + out["server_errors"]) / total, 3)
            out["server_error_rate"] = round(100.0 * out["server_errors"] / total, 3)
        con.close()
    except sqlite3.Error as exc:
        out["error"] = exc.__class__.__name__
    return out


def uptime_figures(history_path, now=None):
    """The 5.8 running figure from the mirrored probe history."""
    now = now or time.time()
    start = parse_iso(UPTIME_CLOCK_START)
    out = {"clock_start": UPTIME_CLOCK_START, "cadence_minutes": PROBE_CADENCE_MIN, "elapsed_minutes": int((now - start) / 60),
           "down_minutes": 0.0, "uptime_percent": None, "probes": 0, "failed_probes": 0, "probes_24h": 0, "failed_24h": 0,
           "last_probe": None, "mirror_age_minutes": None, "outages": [], "source": history_path, "note": ""}
    if not os.path.exists(history_path):
        out["note"] = "no probe history mirrored yet"
        return out
    try:
        out["mirror_age_minutes"] = int((now - os.path.getmtime(history_path)) / 60)
        hist = json.loads(Path(history_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["note"] = f"history unreadable: {exc.__class__.__name__}"
        return out
    probes = [p for p in hist.get("probes", []) if isinstance(p, dict) and parse_iso(p.get("t", "")) is not None]
    probes.sort(key=lambda p: parse_iso(p["t"]))
    probes = [p for p in probes if parse_iso(p["t"]) >= start]
    out["probes"] = len(probes)
    out["failed_probes"] = sum(1 for p in probes if not p.get("ok"))
    if probes:
        # the probe began after the clock: the minutes before the first probe are unobserved and counted as up
        out["measured_since"] = probes[0]["t"]
        out["unobserved_minutes"] = int((parse_iso(probes[0]["t"]) - start) / 60)
    day_ago = now - 86400
    out["probes_24h"] = sum(1 for p in probes if parse_iso(p["t"]) >= day_ago)
    out["failed_24h"] = sum(1 for p in probes if parse_iso(p["t"]) >= day_ago and not p.get("ok"))
    down = 0.0
    outages = []
    prev = None
    cur = None
    for p in probes:
        t = parse_iso(p["t"])
        if not p.get("ok") and prev is not None and not prev.get("ok"):
            span = min((t - parse_iso(prev["t"])) / 60.0, MAX_DOWN_SPAN_MIN)
            down += span
            if cur is None:
                cur = {"from": prev["t"], "to": p["t"], "minutes": span}
            else:
                cur["to"] = p["t"]
                cur["minutes"] += span
        elif p.get("ok") and cur is not None:
            cur["minutes"] = round(cur["minutes"], 1)
            outages.append(cur)
            cur = None
        prev = p
    if cur is not None:
        cur["minutes"] = round(cur["minutes"], 1)
        cur["open"] = True
        outages.append(cur)
    out["down_minutes"] = round(down, 1)
    if out["elapsed_minutes"] > 0:
        out["uptime_percent"] = round(100.0 * (1 - down / out["elapsed_minutes"]), 4)
    out["outages"] = outages[-10:]
    if probes:
        last = probes[-1]
        out["last_probe"] = {"t": last.get("t"), "ok": bool(last.get("ok")), "status": last.get("status"), "ms": last.get("ms"),
                             "keyed": last.get("keyed"), "data_version": last.get("data_version"), "runner": last.get("runner")}
        out["last_probe_age_minutes"] = int((now - parse_iso(last["t"])) / 60)
    return out


def refresh_figures(p):
    out = {"last_check": None, "current": None, "next": None}
    try:
        logs = sorted(Path(p["refresh_logs"]).glob("refresh_check_*.txt"))
        if logs:
            last = logs[-1]
            lines = last.read_text(encoding="utf-8", errors="replace").splitlines()
            rc = None
            for line in reversed(lines):
                if line.startswith("exit "):
                    rc = int(line.split()[1])
                    break
            stamp = last.stem.replace("refresh_check_", "")
            t = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
            verdict = {0: "nothing new", 10: "new data, build started", None: "no exit line"}.get(rc, f"error (exit {rc})")
            out["last_check"] = {"t": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "exit": rc, "verdict": verdict,
                                 "products": [ln.split()[0] for ln in lines if ln and not ln.startswith("exit ") and len(ln.split()) > 2][:8]}
    except (OSError, ValueError):
        pass
    for key, name in (("current", "current_sidecar"), ("next", "next_sidecar")):
        try:
            if os.path.exists(p[name]):
                d = json.loads(Path(p[name]).read_text(encoding="utf-8"))
                gates = d.get("gates", {})
                out[key] = {"data_version": d.get("data_version"), "verdict": d.get("verdict"), "built_at": d.get("built_at"),
                            "swapped": bool(d.get("swapped")), "swapped_at": d.get("swapped_at"),
                            "gates": {g: x.get("status") for g, x in gates.items() if isinstance(x, dict)}}
        except (OSError, ValueError):
            out[key] = {"error": "sidecar unreadable"}
    return out


def box_figures(p):
    out = {"disks": [], "memory": None, "load": None, "backup": None, "watchdog": None}
    for mount in p["disks"]:
        try:
            u = shutil.disk_usage(mount)
            out["disks"].append({"mount": mount, "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1),
                                 "free_percent": round(100.0 * u.free / u.total, 1)})
        except OSError:
            out["disks"].append({"mount": mount, "error": "unreadable"})
    try:
        mem = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _, v = line.partition(":")
            mem[k] = int(v.split()[0])
        out["memory"] = {"available_gb": round(mem["MemAvailable"] / 1e6, 1), "total_gb": round(mem["MemTotal"] / 1e6, 1),
                         "available_percent": round(100.0 * mem["MemAvailable"] / mem["MemTotal"], 1)}
    except (OSError, KeyError, ValueError):
        pass
    try:
        out["load"] = [round(x, 2) for x in os.getloadavg()]
        out["cpus"] = os.cpu_count()
    except OSError:
        pass
    try:
        lines = Path(p["backup_journal"]).read_text(encoding="utf-8").splitlines()
        if lines:
            j = json.loads(lines[-1])
            out["backup"] = {"t": j.get("time"), "ok": bool(j.get("ok")), "drive": j.get("drive"), "storage_box": j.get("storage_box"),
                             "seconds": j.get("seconds")}
    except (OSError, ValueError):
        pass
    try:
        if os.path.exists(p["watchdog_state"]):
            w = json.loads(Path(p["watchdog_state"]).read_text(encoding="utf-8"))
            out["watchdog"] = {"t": w.get("last_run"), "checks": w.get("checks", {})}
    except (OSError, ValueError):
        pass
    return out


def alert_figures(path, days=7):
    """The newest line per check from alerts.jsonl in the last `days`; open means the newest line is a breach."""
    latest = {}
    try:
        cutoff = time.time() - days * 86400
        for line in Path(path).read_text(encoding="utf-8").splitlines()[-2000:]:
            try:
                a = json.loads(line)
            except ValueError:
                continue
            t = parse_iso(a.get("time", ""))
            if t is None or t < cutoff:
                continue
            latest[a.get("check", "?")] = a
    except OSError:
        return {"open": [], "recent": []}
    items = sorted(latest.values(), key=lambda a: a.get("time", ""), reverse=True)
    return {"open": [a for a in items if a.get("state") == "breach"], "recent": items[:20]}


def collect():
    p = paths()
    now = time.time()
    ok, data_version, detail = db_check()
    data = {
        "generated_at": iso(now),
        "cache_seconds": CACHE_SECONDS,
        "service": {"name": "PatentRef API", "base": "https://patentref.io/api/v1/", "health": ok, "health_detail": detail,
                    "code_version": code_version(), "data_version": data_version},
        "uptime": uptime_figures(p["probe_history"], now),
        "requests_24h": request_figures(p["request_log"], now - 86400),
        "requests_30d": request_figures(p["request_log"], now - 30 * 86400),
        "refresh": refresh_figures(p),
        "box": box_figures(p),
        "alerts": alert_figures(p["alerts"]),
    }
    up = data["uptime"]
    if not ok:
        data["state"] = ("down", "The API is not answering health checks on the box.")
    elif up.get("outages") and up["outages"][-1].get("open"):
        data["state"] = ("down", "The last two external probes failed.")
    elif data["alerts"]["open"]:
        data["state"] = ("degraded", f"{len(data['alerts']['open'])} alert(s) open on the box.")
    elif up.get("last_probe") and not up["last_probe"]["ok"]:
        data["state"] = ("degraded", "The last external probe failed; one more failure counts as down.")
    else:
        data["state"] = ("ok", "All checks passing.")
    return data


PUBLIC_PROBE_SOURCE = "https://github.com/patentref/status"
PUBLIC_DISK_NAMES = {"/": ("system", "disk_root"), "/data": ("data", "disk_data")}
PUBLIC_CHECK_KEYS = ("ok", "fails", "checked", "breach_since", "last_alert")


def public_view(data):
    """The figures a keyless reader gets from status.json and the status page: no server paths, no desk ids,
    no disk or memory sizes, no host names, no load figures. Disks and memory keep the free share and whether
    the watchdog's threshold holds; watchdog checks keep their state and times without the detail text (it
    carries sizes and paths); alert lines keep the check, the state and the time. collect() keeps the full
    figures for tools on the box (the watchdog reads its own state file, not this view)."""
    d = copy.deepcopy(data)
    u = d.get("uptime") or {}
    if "source" in u:
        u["source"] = PUBLIC_PROBE_SOURCE
    box = d.get("box") or {}
    checks = (box.get("watchdog") or {}).get("checks") or {}
    disks = []
    for i, disk in enumerate(box.get("disks") or []):
        name, check = PUBLIC_DISK_NAMES.get(disk.get("mount"), (f"disk {i + 1}", ""))
        row = {"name": name, "free_percent": disk.get("free_percent"), "ok": (checks.get(check) or {}).get("ok")}
        if disk.get("error"):
            row["error"] = "unreadable"
        disks.append(row)
    mem = box.get("memory")
    public_box = {
        "disks": disks,
        "memory": {"available_percent": mem.get("available_percent"), "ok": (checks.get("memory") or {}).get("ok")} if mem else None,
        "backup": box.get("backup"),
        "watchdog": None,
    }
    if box.get("watchdog"):
        public_box["watchdog"] = {"t": box["watchdog"].get("t"),
                                  "checks": {name: {k: c.get(k) for k in PUBLIC_CHECK_KEYS} for name, c in checks.items() if isinstance(c, dict)}}
    d["box"] = public_box
    alerts = d.get("alerts") or {}
    d["alerts"] = {k: [{"time": a.get("time"), "check": a.get("check"), "state": a.get("state")} for a in alerts.get(k, [])]
                   for k in ("open", "recent")}
    return d


def cached():
    now = time.time()
    with _lock:
        if _cache["data"] is not None and now - _cache["at"] < CACHE_SECONDS:
            return _cache["data"]
    data = collect()
    with _lock:
        _cache["at"], _cache["data"] = now, data
    return data


def _as_of(data_version):
    cfg = getattr(settings, "LAPSE_SQLITE", None) or {}
    path = cfg.get("path", "")
    try:
        return datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).strftime("%Y-%m-%d")
    except OSError:
        return ""


@require_GET
def status_json(request):
    data = public_view(cached())
    resp = JsonResponse(data, json_dumps_params={"indent": 1})
    resp["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    resp["Access-Control-Allow-Origin"] = "*"
    return resp


@require_GET
def status_page(request):
    if request.GET.get("format") == "json":
        return status_json(request)
    data = public_view(cached())
    html = render_to_string("lapse_accounts/status.html", {
        "d": data, "state": data["state"][0], "state_text": data["state"][1],
        "data_version": data["service"]["data_version"] or "unversioned", "as_of": _as_of(data["service"]["data_version"]),
        "json_url": "/api/v1/meta/status.json",
        "footer_lines": public.footer_lines(),
    })
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"
    return resp
