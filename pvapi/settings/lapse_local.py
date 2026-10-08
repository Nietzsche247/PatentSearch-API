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
LAPSE_BEHIND_PROXY = os.environ.get("LAPSE_BEHIND_PROXY", "0") == "1"  # also: believe X-Forwarded-For from LAPSE_TRUSTED_PROXIES (gate 5.6)
if LAPSE_BEHIND_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("LAPSE_DJANGO_DB", str(_DATA / "django.sqlite3")),
        # Eight gunicorn workers write this file (throttle counters, the monthly count, the DatabaseCache).
        # IMMEDIATE makes every transaction.atomic() take the write lock at BEGIN, so a write inside it waits
        # for the busy timeout instead of failing at once at the lock upgrade (that failure is what made
        # DatabaseCache drop throttle writes under load, PatentRef gate 5.10 fix of 2026-10-06).
        "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": int(os.environ.get("LAPSE_DJANGO_DB_TIMEOUT", "20"))},
    }
}

# Headers for the documented 501 on deferred endpoints, and X-Data-Version on every response (API/lapse_errors.py)
MIDDLEWARE = ["API.lapse_errors.ProblemDetailsMiddleware", "API.lapse_errors.DataVersionMiddleware",
              "API.lapse_errors.LapseErrorHeadersMiddleware"] + list(MIDDLEWARE)
# Agent plumbing at the site root (PatentRef gate 2.11, lapse_accounts/public.py): the files come from the
# patentref repo clone on the box; LICENSES.md also feeds the page footers (checklist 1.11).
LAPSE_PUBLIC_DIR = os.environ.get("LAPSE_PUBLIC_DIR", "/srv/lapse/public")
# The patentref clone whose `lapse.expiry` is the one status engine (API/lapse_group.py), and the Lapse catalog
# attached read-only beside the search snapshot for the `lapse` group and the verdict tool (gates 1.7, 2.9, 2.12)
LAPSE_REPO_DIR = os.environ.get("LAPSE_REPO_DIR", "/srv/lapse")
LAPSE_CATALOG_PATH = os.environ.get("LAPSE_CATALOG_PATH", "/data/lapse/snapshot_current.db")
# The long-text file (claims, brief summaries, detail and drawing descriptions; PatentRef 1.6), attached read-only
# as `txt` beside the search snapshot; a missing file means the four text endpoints answer the documented 501
LAPSE_TEXT_PATH = os.environ.get("LAPSE_TEXT_PATH", "/data/api/text_current.db")
LAPSE_LICENSES_PATH = os.environ.get("LAPSE_LICENSES_PATH", "/srv/lapse/LICENSES.md")

# Search backend seam (API/search.py:get_searcher)
LAPSE_BACKEND = os.environ.get("LAPSE_BACKEND", "sqlite")
LAPSE_SQLITE = {
    "path": os.environ.get("LAPSE_SQLITE_PATH", str(_DATA / "sample.db")),
    # per-statement ceiling (a progress handler interrupts the statement; the request then answers 500 ERR_ES like an
    # ES timeout); 20 s since gate 5.6, was 60; the per-request budget LAPSE_REQUEST_BUDGET below caps page plus count
    "timeout": int(os.environ.get("LAPSE_SQLITE_TIMEOUT", "20")),
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
INSTALLED_APPS = list(INSTALLED_APPS) + ["django.contrib.humanize", "lapse_accounts"]  # humanize: the status page
ROOT_URLCONF = "lapse_accounts.root_urls"
# RequestLogMiddleware first, so its clock covers everything below it (gate 5.4 dashboard)
MIDDLEWARE = ["lapse_accounts.requestlog.RequestLogMiddleware"] + MIDDLEWARE + ["lapse_accounts.middleware.MeteringMiddleware", "lapse_accounts.abuse.AbuseLimitMiddleware"]
LAPSE_REQUEST_LOG = os.environ.get("LAPSE_REQUEST_LOG", "1") == "1"   # per-request rows (ts, status, ms, prefix, path) in the usage-log file
# Status page inputs (lapse_accounts/status.py); defaults are the box paths from ops/refresh.md and ops/backup.md
LAPSE_STATUS_PATHS = {
    "lapse_data": os.environ.get("LAPSE_DATA", "/data/lapse"),
    "api_dir": os.environ.get("LAPSE_DATA_DIR", "/data/api"),
    "backup_journal": os.environ.get("PATENTREF_BACKUP_DIR", "/data/backups") + "/backup_journal.jsonl",
}

# Throttles (lapse_accounts/throttling.py): upstream's per-minute key throttle with the rate chosen per key
# (account keys: the plan's per-minute allowance, 45/m for Free; keys without an account: LAPSE_THROTTLE_RATE,
# upstream's 45/m by default), then the Free-tier monthly cap, plus a per-IP rate on the account endpoints
REST_FRAMEWORK = dict(REST_FRAMEWORK)
REST_FRAMEWORK["DEFAULT_CONTENT_NEGOTIATION_CLASS"] = "API.lapse_errors.ProblemAwareNegotiation"  # Accept: application/problem+json is JSON to DRF (gate 2.11)
REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = [
    "lapse_accounts.throttling.PlanKeyThrottle",
    "lapse_accounts.throttling.MonthlyKeyThrottle",
    "lapse_accounts.throttling.IPCeilingThrottle",
    "lapse_accounts.throttling.InflightThrottle",
]
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
    "key": os.environ.get("LAPSE_THROTTLE_RATE", "45/m"),
    "meta": os.environ.get("LAPSE_META_THROTTLE_RATE", "30/m"),
    "ip": "300/m",  # the number itself comes from LAPSE_IP_PER_MINUTE below; this entry only satisfies DRF's rate parser
}

# Abuse limits (PatentRef checklist 5.6; lapse_accounts/abuse.py, lapse_accounts/throttling.py, API/lapse_cost.py).
# Every number is recorded with its reason in the patentref repo's decisions.md.
LAPSE_TRUSTED_PROXIES = os.environ.get("LAPSE_TRUSTED_PROXIES", "127.0.0.1,::1,172.16.0.0/12")  # whose X-Forwarded-For is believed: loopback and docker networks (the Caddy container is on a compose network, 172.18.0.x on patentref-us1)
LAPSE_NOKEY_PER_MINUTE = int(os.environ.get("LAPSE_NOKEY_PER_MINUTE", "60"))      # keyless or invalid-key requests per IP per clock minute
LAPSE_IP_PER_MINUTE = int(os.environ.get("LAPSE_IP_PER_MINUTE", "300"))            # admitted keyed requests per IP per clock minute, all keys
LAPSE_INFLIGHT_PER_KEY = int(os.environ.get("LAPSE_INFLIGHT_PER_KEY", "4"))        # requests running at once per key
LAPSE_INFLIGHT_PER_IP = int(os.environ.get("LAPSE_INFLIGHT_PER_IP", "8"))          # requests running at once per IP
LAPSE_MAX_QUERY_STRING = int(os.environ.get("LAPSE_MAX_QUERY_STRING", "16384"))    # bytes
LAPSE_MAX_BODY = int(os.environ.get("LAPSE_MAX_BODY", "65536"))                    # bytes; Caddy request_body max_size carries the same figure
LAPSE_MAX_Q_BYTES = int(os.environ.get("LAPSE_MAX_Q_BYTES", "16384"))              # bytes of the q JSON
DATA_UPLOAD_MAX_MEMORY_SIZE = LAPSE_MAX_BODY                                       # Django's own backstop for bodies without a Content-Length
LAPSE_QUERY_COST_LIMIT = int(os.environ.get("LAPSE_QUERY_COST_LIMIT", "100"))      # API/lapse_cost.py units; above it the query is refused with 400 ERR_Q
LAPSE_QUERY_MAX_DEPTH = int(os.environ.get("LAPSE_QUERY_MAX_DEPTH", "8"))          # nesting of _and/_or/_not
LAPSE_QUERY_MAX_CRITERIA = int(os.environ.get("LAPSE_QUERY_MAX_CRITERIA", "64"))   # leaf criteria in one q (a set of ids counts once; any other list value once per element)
LAPSE_REQUEST_BUDGET = int(os.environ.get("LAPSE_REQUEST_BUDGET", "30"))           # seconds of SQLite time one request may use (page plus count)
# Sign-up limits (lapse_accounts/views.py): new accounts (first key) per email domain per UTC day, and known disposable domains refused
LAPSE_SIGNUP_DOMAIN_DAILY = int(os.environ.get("LAPSE_SIGNUP_DOMAIN_DAILY", "20"))
LAPSE_DISPOSABLE_DOMAINS_FILE = os.environ.get("LAPSE_DISPOSABLE_DOMAINS_FILE", "")  # optional: one domain per line, added to the built-in list

# Console-only logging (upstream writes to <repo>/logs/django.log)
for _name, _logger in LOGGING["loggers"].items():
    _logger["handlers"] = ["console"]
LOGGING["handlers"].pop("logfile", None)

STATIC_ROOT = str(_DATA / "static")
CELERY_BROKER_URL = "memory://"
