from datetime import date
from uuid import uuid4

import redis
from django.conf import settings


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip


def log_usage(request, endpoint, status_code, exception, fields_involved):
    d = date.today().isoformat()
    # endpoint = request.get_full_path().split("?")[0]
    # from API.models import APIUserKey
    # api_key = APIUserKey.objects.get_from_key(request.META['HTTP_X_API_KEY'])
    # ident = api_key.username
    try:
        usage_data = {
            "request_date": d,
            "client_ip": get_client_ip(request),
            "api_prefix": request.META["HTTP_X_API_KEY"].split(".")[0],
            "status_code": status_code,
            "request": endpoint,
            "user_agent": request.META["HTTP_USER_AGENT"],
            "method": request.META["REQUEST_METHOD"],
            "exception": exception,
        }
    except KeyError:
        return

    # Lapse: USAGE_LOG_BACKEND = "none" | "sqlite" | "redis" (default, upstream behavior)
    backend = getattr(settings, "USAGE_LOG_BACKEND", "redis")
    if backend == "none":
        return
    if backend == "sqlite":
        return _log_usage_sqlite(usage_data, endpoint, fields_involved)

    r = redis.StrictRedis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DATABASES["STATS"],
    )
    op_name = "request_{uid}".format(uid=str(uuid4()))
    pipe = r.pipeline()
    for key, value in usage_data.items():
        pipe.hset(name=op_name, key=key, value=value)
    field_set_name = f"field_analytics_{d}_{endpoint}"
    for field in fields_involved:
        pipe.zincrby(name=field_set_name, amount=1, value=field)
    pipe.execute()


def _log_usage_sqlite(usage_data, endpoint, fields_involved):
    """Lapse: same records as the Redis logger, stored in a local SQLite file (settings.USAGE_LOG_SQLITE_PATH)."""
    import json
    import sqlite3

    path = getattr(settings, "USAGE_LOG_SQLITE_PATH", "usage_log.sqlite3")
    try:
        con = sqlite3.connect(path, timeout=5)
        con.execute(
            "CREATE TABLE IF NOT EXISTS usage_log(request_date TEXT, client_ip TEXT, api_prefix TEXT,"
            " status_code INTEGER, request TEXT, user_agent TEXT, method TEXT, exception INTEGER, fields TEXT)"
        )
        con.execute(
            "INSERT INTO usage_log VALUES (?,?,?,?,?,?,?,?,?)",
            (*[usage_data[k] for k in ("request_date", "client_ip", "api_prefix", "status_code", "request",
                                       "user_agent", "method", "exception")],
             json.dumps(list(fields_involved))),
        )
        con.commit()
        con.close()
    except sqlite3.Error:
        pass
