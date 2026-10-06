"""Lapse local settings: SQLite for Django's own tables, SQLite search backend, no Redis/MySQL/ES/OAuth.

  set DJANGO_SETTINGS_MODULE=pvapi.settings.lapse_local
  python manage.py migrate && python manage.py createcachetable
  python manage.py runserver 127.0.0.1:8765

Env overrides: LAPSE_SQLITE_PATH (search DB), LAPSE_DJANGO_DB, LAPSE_BACKEND (sqlite|elasticsearch),
LAPSE_THROTTLE_RATE (default upstream 45/m), LAPSE_USAGE_LOG (sqlite|none|redis),
LAPSE_ALLOWED_HOSTS (public hostnames, comma separated), LAPSE_BEHIND_PROXY=1 (trust X-Forwarded-Proto/Host),
SUPABASE_URL, SUPABASE_ANON_KEY (accounts), LAPSE_FREE_MONTHLY_LIMIT (1000), LAPSE_FREE_MINUTE_LIMIT (45), LAPSE_DATA_VERSION.
"""
import os
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_DATA = Path(os.environ.get("LAPSE_DATA_DIR", _REPO.parent / "data"))

# base.py / databases.py read these unconditionally; give harmless local defaults
for _k, _v in {
    "DJANGO_SECRET_KEY": "lapse-local-not-secret",
    "MYSQL_DATABASE": "unused", "MYSQL_USER": "unused", "MYSQL_PASSWORD": "unused", "MYSQL_HOST": "unused",
    "ELASTIC_HOST": "http://localhost", "ELASTIC_USER": "", "ELASTIC_PASSWORD": "",
    "SOCIAL_AUTH_GOOGLE_OAUTH2_KEY": "", "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET": "",
    "REDIS_HOST": "localhost", "REDIS_PORT": "6379",
}.items():
    os.environ.setdefault(_k, _v)

from pvapi.settings.base import *  # noqa: E402,F403
from pvapi.settings.base import ALLOWED_HOSTS, INSTALLED_APPS, LOGGING, MIDDLEWARE, REST_FRAMEWORK  # noqa: E402

DEBUG = os.environ.get("LAPSE_DEBUG", "0") == "1"
ALLOWED_HOSTS = ALLOWED_HOSTS + ["127.0.0.1", "localhost", "testserver"]
# Public hostnames behind the reverse proxy, comma separated (LAPSE_ALLOWED_HOSTS=patentref.io,patentref.com)
ALLOWED_HOSTS += [h.strip() for h in os.environ.get("LAPSE_ALLOWED_HOSTS", "").split(",") if h.strip()]
# Behind Caddy, which terminates TLS and sets X-Forwarded-Proto; lets Django build https links
if os.environ.get("LAPSE_BEHIND_PROXY", "0") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("LAPSE_DJANGO_DB", str(_DATA / "django.sqlite3")),
    }
}

# Headers for the documented 501 on deferred endpoints, and X-Data-Version on every response (API/lapse_errors.py)
MIDDLEWARE = ["API.lapse_errors.DataVersionMiddleware", "API.lapse_errors.LapseErrorHeadersMiddleware"] + list(MIDDLEWARE)

# Search backend seam (API/search.py:get_searcher)
LAPSE_BACKEND = os.environ.get("LAPSE_BACKEND", "sqlite")
LAPSE_SQLITE = {
    "path": os.environ.get("LAPSE_SQLITE_PATH", str(_DATA / "sample.db")),
    "timeout": int(os.environ.get("LAPSE_SQLITE_TIMEOUT", "60")),
    # per-connection page cache (KB) and mmap window; the OS page cache does the heavy lifting on a
    # 140 GB file, this is the working set one query touches (PatentRef 2.10)
    "cache_kb": int(os.environ.get("LAPSE_SQLITE_CACHE_KB", "400000")),
    "mmap_bytes": int(os.environ.get("LAPSE_SQLITE_MMAP", str(1 << 30))),
    # shared total_hits memo (a small SQLite file every worker reads and writes); "none" turns it off
    "count_cache": (lambda v: None if v.lower() == "none" else v)(
        os.environ.get("LAPSE_COUNT_CACHE", str(_DATA / "count_cache.sqlite3"))),
    "count_cache_min_ms": float(os.environ.get("LAPSE_COUNT_CACHE_MIN_MS", "20")),
}

# Usage logging without Redis (API/UsageLogging.py)
USAGE_LOG_BACKEND = os.environ.get("LAPSE_USAGE_LOG", "sqlite")
USAGE_LOG_SQLITE_PATH = os.environ.get("LAPSE_USAGE_LOG_PATH", str(_DATA / "usage_log.sqlite3"))

# Accounts (checklist 5.10): Supabase Auth is the identity system; keys are minted from it (lapse_accounts/).
# SUPABASE_URL and SUPABASE_ANON_KEY come from the server's env file; the anon key is public by design
# (it is what the browser uses). The service_role key is never read by this app.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_JWKS_URL = os.environ.get("SUPABASE_JWKS_URL", f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json" if SUPABASE_URL else "")
SUPABASE_JWT_ISSUER = os.environ.get("SUPABASE_JWT_ISSUER", f"{SUPABASE_URL}/auth/v1" if SUPABASE_URL else "")
# Free tier: requests per UTC calendar month and per minute (the per-minute figure is upstream's 45/m)
LAPSE_FREE_MONTHLY_LIMIT = int(os.environ.get("LAPSE_FREE_MONTHLY_LIMIT", "1000"))
LAPSE_FREE_MINUTE_LIMIT = int(os.environ.get("LAPSE_FREE_MINUTE_LIMIT", "45"))
# Fallback data_version for a search database with no `_lapse_build.data_version` row (files built before the
# refresh runner); otherwise the version comes from the served file (API/search_sqlite.py read_data_version)
LAPSE_DATA_VERSION = os.environ.get("LAPSE_DATA_VERSION", "")
INSTALLED_APPS = list(INSTALLED_APPS) + ["lapse_accounts"]
ROOT_URLCONF = "lapse_accounts.root_urls"
MIDDLEWARE = MIDDLEWARE + ["lapse_accounts.middleware.MeteringMiddleware"]

# Throttles (lapse_accounts/throttling.py): upstream's per-minute key throttle with the rate chosen per key
# (account keys: the plan's per-minute allowance, 45/m for Free; keys without an account: LAPSE_THROTTLE_RATE,
# upstream's 45/m by default), then the Free-tier monthly cap, plus a per-IP rate on the account endpoints
REST_FRAMEWORK = dict(REST_FRAMEWORK)
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = ["lapse_accounts.throttling.PlanKeyThrottle", "lapse_accounts.throttling.MonthlyKeyThrottle"]
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "key": os.environ.get("LAPSE_THROTTLE_RATE", "45/m"),
    "meta": os.environ.get("LAPSE_META_THROTTLE_RATE", "30/m"),
}

# Console-only logging (upstream writes to <repo>/logs/django.log)
for _name, _logger in LOGGING["loggers"].items():
    _logger["handlers"] = ["console"]
LOGGING["handlers"].pop("logfile", None)

STATIC_ROOT = str(_DATA / "static")
CELERY_BROKER_URL = "memory://"
