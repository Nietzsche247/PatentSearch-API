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

The structure layer's usage counter (release X, the S1 tripwire): a request under STRUCTURE_PREFIX also gets one row in

  structure_log(ts REAL, status INTEGER, prefix TEXT, path TEXT, client TEXT)

in the same file, written in the same transaction and deleted after the same RETENTION_DAYS. `client` is "mcp" when
the request's User-Agent starts with MCP_AGENT (the PatentRef MCP package sends "patentref-mcp/<version>", so its
get_claim_tree calls can be told apart) and "" otherwise; the user agent itself is not kept. The table is created the
way request_log is, by CREATE TABLE IF NOT EXISTS when a writer opens the file; nothing in request_log changes, so an
older release keeps writing request_log as before. lapse_accounts.usage_counts reads both tables.

The kinds of client (release AH, lapse_accounts/clients.py), only while settings.LAPSE_CLIENT_COUNTS is on (off by default):
one count per (UTC day, area, kind) in

  client_counts(day TEXT, area TEXT, kind TEXT, n INTEGER, PRIMARY KEY (day, area, kind))

added to in the same transaction (the worker adds up its counts between flushes). Counts only: no User-Agent, address, key,
prefix, path or time of a request goes into the table. While the setting is off the table is not made and nothing is counted.
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

STRUCTURE_PREFIX = "/api/v1/structure/"
MCP_AGENT = "patentref-mcp/"

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS request_log(ts REAL NOT NULL, status INTEGER NOT NULL, ms REAL NOT NULL,"
    " prefix TEXT, path TEXT, method TEXT);"
    "CREATE INDEX IF NOT EXISTS request_log_ts ON request_log(ts);"
    "CREATE TABLE IF NOT EXISTS structure_log(ts REAL NOT NULL, status INTEGER NOT NULL, prefix TEXT, path TEXT, client TEXT);"
    "CREATE INDEX IF NOT EXISTS structure_log_ts ON structure_log(ts);"
)
# release AH: made only while the client counter is on (lapse_accounts/clients.py)
CLIENT_SCHEMA = ("CREATE TABLE IF NOT EXISTS client_counts(day TEXT NOT NULL, area TEXT NOT NULL, kind TEXT NOT NULL, "
                 "n INTEGER NOT NULL, PRIMARY KEY (day, area, kind));")
CLIENT_UPSERT = ("INSERT INTO client_counts(day, area, kind, n) VALUES (?,?,?,?) "
                 "ON CONFLICT(day, area, kind) DO UPDATE SET n = n + excluded.n")


def client_tag(user_agent):
    """'mcp' for the PatentRef MCP package's requests (its User-Agent is patentref-mcp/<version>), else ''."""
    return "mcp" if (user_agent or "").lower().startswith(MCP_AGENT) else ""


def log_path():
    return getattr(settings, "USAGE_LOG_SQLITE_PATH", "usage_log.sqlite3")


def enabled():
    return getattr(settings, "LAPSE_REQUEST_LOG", True) and getattr(settings, "USAGE_LOG_BACKEND", "sqlite") == "sqlite"


def connect(path, timeout=5, clients=False):
    con = sqlite3.connect(path, timeout=timeout)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    if clients:
        con.executescript(CLIENT_SCHEMA)
    return con


def clients_on():
    from lapse_accounts import clients

    return clients.on()


class _Writer:
    def __init__(self):
        self._lock = threading.Lock()
        self._rows = []
        self._srows = []  # structure_log rows (the structure layer's usage counter)
        self._counts = {}  # release AH: (day, area, kind) -> n since the last flush, while the client counter is on
        self._kinds = {}   # release AH: day -> the (area, kind) pairs this worker counted that day (MAX_KINDS_A_DAY)
        self._thread = None
        self._last_prune = 0.0
        self._con = None
        self._pid = None

    def add(self, row, srow=None, ckey=None):
        with self._lock:
            if len(self._rows) < MAX_BUFFER:
                self._rows.append(row)
            if srow is not None and len(self._srows) < MAX_BUFFER:
                self._srows.append(srow)
            if ckey is not None:
                self._count(ckey)
            if len(self._rows) >= FLUSH_ROWS:
                wake = True
            else:
                wake = False
        self._ensure_thread()
        if wake:
            self._event.set()

    def _count(self, ckey):
        """Add one to (day, area, kind); a pair past MAX_KINDS_A_DAY in a day counts as (day, area, other). Called with the lock."""
        from lapse_accounts.clients import MAX_KINDS_A_DAY

        d, a, k = ckey
        seen = self._kinds.setdefault(d, set())
        if (a, k) not in seen:
            if len(seen) >= MAX_KINDS_A_DAY:
                k = "other"
            seen.add((a, k))
            for old in [x for x in self._kinds if x < d]:
                del self._kinds[old]
        self._counts[(d, a, k)] = self._counts.get((d, a, k), 0) + 1

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
            srows, self._srows = self._srows, []
            counts, self._counts = self._counts, {}
        if not rows and not srows and not counts:
            return
        try:
            on = clients_on()   # a setting: the same for the life of the worker, so the table is made with the connection
            if self._con is None:
                self._con = connect(log_path(), clients=on)
            self._con.executemany("INSERT INTO request_log VALUES (?,?,?,?,?,?)", rows)
            if srows:
                self._con.executemany("INSERT INTO structure_log VALUES (?,?,?,?,?)", srows)
            if counts and on:
                self._con.executemany(CLIENT_UPSERT, [(d, a, k, n) for (d, a, k), n in counts.items()])
            self._con.commit()
            now = time.time()
            if now - self._last_prune > PRUNE_EVERY:
                self._last_prune = now
                self._con.execute("DELETE FROM request_log WHERE ts < ?", (now - RETENTION_DAYS * 86400,))
                self._con.execute("DELETE FROM structure_log WHERE ts < ?", (now - RETENTION_DAYS * 86400,))
                if on:
                    cut = time.strftime("%Y-%m-%d", time.gmtime(now - RETENTION_DAYS * 86400))
                    self._con.execute("DELETE FROM client_counts WHERE day < ?", (cut,))
                self._con.commit()
        except sqlite3.Error as exc:
            log.warning("request log write failed (%s); %d rows kept for the next flush", exc.__class__.__name__, len(rows) + len(srows))
            with self._lock:
                self._rows = rows + self._rows
                del self._rows[MAX_BUFFER:]
                self._srows = srows + self._srows
                del self._srows[MAX_BUFFER:]
                for k, n in counts.items():
                    self._counts[k] = self._counts.get(k, 0) + n
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
            ts, st, path = time.time(), int(response.status_code), request.path[:200]
            srow = None
            if path.startswith(STRUCTURE_PREFIX):
                srow = (ts, st, prefix, path, client_tag(request.META.get("HTTP_USER_AGENT")))
            try:
                ckey = client_key(ts, path, prefix, request)
            except Exception:  # the client counter never costs the request log its row
                ckey = None
            writer.add((ts, st, round(ms, 3), prefix, path, request.method[:8]), srow, ckey)
        except Exception:  # the log never breaks a response
            pass
        return response


def client_key(ts, path, prefix, request):
    """Release AH: the (UTC day, area, kind) count this response adds to while the client counter is on, else None. The
    service's own keys count as own (lapse_accounts.status.own_key_prefixes, kept 10 minutes per worker)."""
    from lapse_accounts import clients

    if not clients.on():
        return None
    own = False
    if prefix:
        from lapse_accounts import status

        own = prefix in status.own_key_prefixes()
    return clients.counter_key(ts, path, request.META.get("HTTP_USER_AGENT"), own)
