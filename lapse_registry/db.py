"""One psycopg 3 connection per thread to the registry database, logged in as registry_api (REGISTRY_DATABASE_URL in
the box's environment file; the password is never in the repo or a log). A broken connection is replaced once; when the
database cannot be reached the views answer 503 (checklist 5.15: registry writes return 503 during a Supabase outage).
prepare_threshold=None keeps it safe behind Supabase's transaction pooler. The connection is in autocommit mode, so
every `with conn.transaction()` of patentref_registry.service is a real BEGIN and COMMIT: outside autocommit, a bare
statement (the health ping) would leave a transaction open and every later transaction on the thread would become a
savepoint inside it, never committed.

During an outage (checklist 5.15) a request must not wait on a database that does not answer. The first failure (a
connect that times out, or an established connection that stops answering: tcp_user_timeout and keepalives end it
within 5 seconds) marks the database down for the whole process: from then on every request fails at once with
Unavailable, so the views answer reads from the local read cache (readcache.py) and writes with 503 and Retry-After,
and no request waits on the database again. A background thread tries to connect every REGISTRY_DB_RETRY_SECONDS (5)
and clears the mark when one succeeds; requests never do that probing themselves."""
import logging
import threading
import time

from django.conf import settings

log = logging.getLogger("lapse_registry")
_local = threading.local()
_down = {"since": 0.0, "prober": None}
_lock = threading.Lock()


class Unavailable(RuntimeError):
    pass


def retry_seconds():
    return float(getattr(settings, "REGISTRY_DB_RETRY_SECONDS", 5))


def is_down():
    return bool(_down["since"])


def _open():
    import psycopg

    dsn = getattr(settings, "REGISTRY_DATABASE_URL", "")
    if not dsn:
        raise Unavailable("REGISTRY_DATABASE_URL is not set")
    try:
        return psycopg.connect(dsn, autocommit=True, prepare_threshold=None, connect_timeout=5,
                               application_name="patentref-registry-api", keepalives=1, keepalives_idle=30,
                               keepalives_interval=10, keepalives_count=3, tcp_user_timeout=5000)
    except psycopg.OperationalError as e:
        raise Unavailable("the registry database is unreachable") from e


def _probe_until_back():
    while _down["since"]:
        time.sleep(retry_seconds())
        try:
            _open().close()
        except Unavailable:
            continue
        with _lock:
            log.warning("registry database reachable again after %.0f s", time.monotonic() - _down["since"])
            _down["since"], _down["prober"] = 0.0, None
        return


def mark_down(why=""):
    """The database did not answer: fail fast in this process until the background probe connects again."""
    with _lock:
        if not _down["since"]:
            _down["since"] = time.monotonic()
            log.warning("registry database unreachable%s; failing fast, probing every %.0f s", f" ({why})" if why else "",
                        retry_seconds())
        if _down["prober"] is None or not _down["prober"].is_alive():
            t = threading.Thread(target=_probe_until_back, name="registry-db-probe", daemon=True)
            _down["prober"] = t
            t.start()


def _connect():
    try:
        return _open()
    except Unavailable as e:
        if getattr(settings, "REGISTRY_DATABASE_URL", ""):
            mark_down(e.__cause__.__class__.__name__ if e.__cause__ else "")
        raise


def connection():
    con = getattr(_local, "con", None)
    if is_down():
        if con is not None:
            reset()
        raise Unavailable("the registry database is unreachable (failing fast until it answers again)")
    if con is not None and not con.closed and not con.broken:
        return con
    if con is not None:
        reset()
    _local.con = _connect()
    return _local.con


def failed(why=""):
    """A query on an established connection failed with an OperationalError: drop it and fail fast until the probe
    connects again (within REGISTRY_DB_RETRY_SECONDS when the database is fine)."""
    reset()
    mark_down(why)


def clear_down():
    """Forget the outage mark (tests)."""
    with _lock:
        _down["since"] = 0.0


def reset():
    con = getattr(_local, "con", None)
    _local.con = None
    if con is not None:
        try:
            con.close()
        except Exception:
            pass


def ping():
    try:
        connection().execute("select 1").fetchone()
        return True
    except Exception:
        reset()
        return False
