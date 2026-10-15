"""One psycopg 3 connection per thread to the registry database, logged in as registry_api (REGISTRY_DATABASE_URL in
the box's environment file; the password is never in the repo or a log). A broken connection is replaced once; when the
database cannot be reached the views answer 503 (checklist 5.15: registry writes return 503 during a Supabase outage).
prepare_threshold=None keeps it safe behind Supabase's transaction pooler."""
import logging
import threading

from django.conf import settings

log = logging.getLogger("lapse_registry")
_local = threading.local()


class Unavailable(RuntimeError):
    pass


def _connect():
    import psycopg

    dsn = getattr(settings, "REGISTRY_DATABASE_URL", "")
    if not dsn:
        raise Unavailable("REGISTRY_DATABASE_URL is not set")
    try:
        return psycopg.connect(dsn, prepare_threshold=None, connect_timeout=5, application_name="patentref-registry-api")
    except psycopg.OperationalError as e:
        log.warning("registry database unreachable: %s", e.__class__.__name__)
        raise Unavailable("the registry database is unreachable") from e


def connection():
    con = getattr(_local, "con", None)
    if con is not None and not con.closed and not con.broken:
        return con
    if con is not None:
        try:
            con.close()
        except Exception:
            pass
    _local.con = _connect()
    return _local.con


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
