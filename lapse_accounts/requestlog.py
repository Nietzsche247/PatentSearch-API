"""Per-request log for the error and latency dashboard (PatentRef gate 5.4).

`RequestLogMiddleware` sits first in the middleware list, so its clock covers everything Django does for a
request: the abuse middleware, the throttles, the view and the SQLite work. One row per response:

  request_log(ts REAL, status INTEGER, ms REAL, prefix TEXT, path TEXT, method TEXT)

in the usage-log file (settings.USAGE_LOG_SQLITE_PATH, /data/api/usage_log.sqlite3 on the box), beside
upstream's `usage_log` table, which keeps its own rows (date, address, key prefix, endpoint; no latency).

Rows are buffered in the worker and written by a background thread every FLUSH_SECONDS or FLUSH_ROWS,
whichever comes first, in one transaction; the request never waits on the file. A write that fails (lock,
disk) is retried on the next flush; the buffer is capped at MAX_BUFFER rows so a stuck file cannot grow the
worker. Rows older than RETENTION_DAYS are deleted once an hour by whichever worker flushes first after the
hour. The file is WAL mode so the status page can read it while the workers write.

Nothing secret is stored: the key prefix is the 8 characters the usage endpoint already shows, the path has
no query string.
"""
import logging
import os
import sqlite3
import threading
import time

from django.conf import settings

log = logging.getLogger("lapse_accounts")

FLUSH_SECONDS = 2.0
FLUSH_ROWS = 500
MAX_BUFFER = 20000
RETENTION_DAYS = 35
PRUNE_EVERY = 3600

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS request_log(ts REAL NOT NULL, status INTEGER NOT NULL, ms REAL NOT NULL,"
    " prefix TEXT, path TEXT, method TEXT);"
    "CREATE INDEX IF NOT EXISTS request_log_ts ON request_log(ts);"
)


def log_path():
    return getattr(settings, "USAGE_LOG_SQLITE_PATH", "usage_log.sqlite3")


def enabled():
    return getattr(settings, "LAPSE_REQUEST_LOG", True) and getattr(settings, "USAGE_LOG_BACKEND", "sqlite") == "sqlite"


def connect(path, timeout=5):
    con = sqlite3.connect(path, timeout=timeout)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


class _Writer:
    def __init__(self):
        self._lock = threading.Lock()
        self._rows = []
        self._thread = None
        self._last_prune = 0.0
        self._con = None
        self._pid = None

    def add(self, row):
        with self._lock:
            if len(self._rows) < MAX_BUFFER:
                self._rows.append(row)
            if len(self._rows) >= FLUSH_ROWS:
                wake = True
            else:
                wake = False
        self._ensure_thread()
        if wake:
            self._event.set()

    def _ensure_thread(self):
        # one thread per worker process; gunicorn forks after import, so check the pid, not just the handle
        if self._thread is not None and self._pid == os.getpid() and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._pid == os.getpid() and self._thread.is_alive():
                return
            self._pid = os.getpid()
            self._con = None
            self._event = threading.Event()
            self._thread = threading.Thread(target=self._run, name="request-log", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            self._event.wait(FLUSH_SECONDS)
            self._event.clear()
            self.flush()

    def flush(self):
        with self._lock:
            rows, self._rows = self._rows, []
        if not rows:
            return
        try:
            if self._con is None:
                self._con = connect(log_path())
            self._con.executemany("INSERT INTO request_log VALUES (?,?,?,?,?,?)", rows)
            self._con.commit()
            now = time.time()
            if now - self._last_prune > PRUNE_EVERY:
                self._last_prune = now
                self._con.execute("DELETE FROM request_log WHERE ts < ?", (now - RETENTION_DAYS * 86400,))
                self._con.commit()
        except sqlite3.Error as exc:
            log.warning("request log write failed (%s); %d rows kept for the next flush", exc.__class__.__name__, len(rows))
            with self._lock:
                self._rows = rows + self._rows
                del self._rows[MAX_BUFFER:]
            try:
                if self._con is not None:
                    self._con.close()
            except sqlite3.Error:
                pass
            self._con = None


writer = _Writer()


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.on = enabled()

    def __call__(self, request):
        if not self.on:
            return self.get_response(request)
        t0 = time.perf_counter()
        response = self.get_response(request)
        ms = (time.perf_counter() - t0) * 1000.0
        key = request.META.get("HTTP_X_API_KEY") or ""
        prefix = key.split(".", 1)[0][:16] if key else ""
        try:
            writer.add((time.time(), int(response.status_code), round(ms, 3), prefix, request.path[:200], request.method[:8]))
        except Exception:  # the log never breaks a response
            pass
        return response
