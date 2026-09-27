"""Lapse local settings: SQLite for Django's own tables, SQLite search backend, no Redis/MySQL/ES/OAuth.

  set DJANGO_SETTINGS_MODULE=pvapi.settings.lapse_local
  python manage.py migrate && python manage.py createcachetable
  python manage.py runserver 127.0.0.1:8765

Env overrides: LAPSE_SQLITE_PATH (search DB), LAPSE_DJANGO_DB, LAPSE_BACKEND (sqlite|elasticsearch),
LAPSE_THROTTLE_RATE (default upstream 45/m), LAPSE_USAGE_LOG (sqlite|none|redis).
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
from pvapi.settings.base import ALLOWED_HOSTS, LOGGING, REST_FRAMEWORK  # noqa: E402

DEBUG = os.environ.get("LAPSE_DEBUG", "0") == "1"
ALLOWED_HOSTS = ALLOWED_HOSTS + ["127.0.0.1", "localhost", "testserver"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("LAPSE_DJANGO_DB", str(_DATA / "django.sqlite3")),
    }
}

# Search backend seam (API/search.py:get_searcher)
LAPSE_BACKEND = os.environ.get("LAPSE_BACKEND", "sqlite")
LAPSE_SQLITE = {
    "path": os.environ.get("LAPSE_SQLITE_PATH", str(_DATA / "sample.db")),
    "timeout": int(os.environ.get("LAPSE_SQLITE_TIMEOUT", "60")),
}

# Usage logging without Redis (API/UsageLogging.py)
USAGE_LOG_BACKEND = os.environ.get("LAPSE_USAGE_LOG", "sqlite")
USAGE_LOG_SQLITE_PATH = os.environ.get("LAPSE_USAGE_LOG_PATH", str(_DATA / "usage_log.sqlite3"))

# Throttle: upstream rate unless overridden
REST_FRAMEWORK = dict(REST_FRAMEWORK)
REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {"key": os.environ.get("LAPSE_THROTTLE_RATE", "45/m")}

# Console-only logging (upstream writes to <repo>/logs/django.log)
for _name, _logger in LOGGING["loggers"].items():
    _logger["handlers"] = ["console"]
LOGGING["handlers"].pop("logfile", None)

STATIC_ROOT = str(_DATA / "static")
CELERY_BROKER_URL = "memory://"
