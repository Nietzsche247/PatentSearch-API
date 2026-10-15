"""Settings of the registry API process (PatentRef checklist phase 10): pvapi.settings.lapse_local plus the app
lapse_registry as the only URLconf, its own body limits, and the registry database.

  DJANGO_SETTINGS_MODULE=pvapi.settings.registry gunicorn pvapi.wsgi -b 127.0.0.1:8767

It keeps its own small app DB (Django's SQLite at LAPSE_DJANGO_DB, /data/registry/django.sqlite3 on the box: the JWKS
cache and the per-address counters), so it never writes the API's /data/api, and the snapshot is read-only to it (its
unit mounts /data/api and /data/lapse read-only). It shares the Supabase project with the API, so a person has one
account for both; it does not load the API's routes, throttles or middleware.

Env: REGISTRY_DATABASE_URL (registry_api's DSN; secret, only in the box's environment file), REGISTRY_PUBLIC_BASE
(https://patentref.io), REGISTRY_MAX_BODY (25 MB), REGISTRY_FILE_MAX (20 MB), REGISTRY_READ_PER_MINUTE (120),
REGISTRY_WRITE_PER_MINUTE (20), REGISTRY_RECEIPTS_ENABLED and REGISTRY_SEALED_ENABLED (0 until 10.3, 10.4 and 10.6),
REGISTRY_CRYPTO_PATH (where registry_crypto lives when not in the patentref clone, gate 5.14), plus everything lapse_local reads.
"""
import os

from pvapi.settings.lapse_local import *  # noqa: F401,F403
from pvapi.settings.lapse_local import INSTALLED_APPS

INSTALLED_APPS = list(INSTALLED_APPS) + ["lapse_registry"]
ROOT_URLCONF = "lapse_registry.urls"
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
APPEND_SLASH = False

REGISTRY_DATABASE_URL = os.environ.get("REGISTRY_DATABASE_URL", "")
REGISTRY_PUBLIC_BASE = os.environ.get("REGISTRY_PUBLIC_BASE", "https://patentref.io")
REGISTRY_MAX_BODY = int(os.environ.get("REGISTRY_MAX_BODY", str(25 * 1024 * 1024)))
REGISTRY_FILE_MAX = int(os.environ.get("REGISTRY_FILE_MAX", str(20 * 1024 * 1024)))
REGISTRY_READ_PER_MINUTE = int(os.environ.get("REGISTRY_READ_PER_MINUTE", "120"))
REGISTRY_WRITE_PER_MINUTE = int(os.environ.get("REGISTRY_WRITE_PER_MINUTE", "20"))
REGISTRY_RECEIPTS_ENABLED = os.environ.get("REGISTRY_RECEIPTS_ENABLED", "0") == "1"
REGISTRY_SEALED_ENABLED = os.environ.get("REGISTRY_SEALED_ENABLED", "0") == "1"
# a JSON submission carries up to 1,000,000 characters of full text (about 4 MB as UTF-8); files are streamed to disk
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("REGISTRY_JSON_MAX", str(5 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
