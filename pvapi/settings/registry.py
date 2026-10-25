"""Settings of the registry API process (PatentRef checklist phase 10): pvapi.settings.lapse_local plus the app
lapse_registry as the only URLconf, its own body limits, and the registry database.

  DJANGO_SETTINGS_MODULE=pvapi.settings.registry gunicorn pvapi.wsgi -b 127.0.0.1:8767

It keeps its own small app DB (Django's SQLite at LAPSE_DJANGO_DB, /data/registry/django.sqlite3 on the box: the JWKS
cache and the per-address counters), so it never writes the API's /data/api, and the snapshot is read-only to it (its
unit mounts /data/api and /data/lapse read-only). It shares the Supabase project with the API, so a person has one
account for both; it does not load the API's routes, throttles or middleware.

Env: REGISTRY_DATABASE_URL (registry_api's DSN; secret, only in the box's environment file), REGISTRY_PUBLIC_BASE
(https://patentref.io), REGISTRY_MAX_BODY (25 MB), REGISTRY_FILE_MAX (20 MB), REGISTRY_READ_PER_MINUTE (120),
REGISTRY_WRITE_PER_MINUTE (20), REGISTRY_PUBLISH_PER_ADDRESS_DAY (30), REGISTRY_SCANNER (none or clamd, read
by patentref_registry.scan), REGISTRY_RECEIPTS_ENABLED and REGISTRY_SEALED_ENABLED (0 until 10.3, 10.4 and 10.6),
REGISTRY_MONITORING_ENABLED, REGISTRY_CONTRIBUTION_LOGS_ENABLED, REGISTRY_CLAIMS_ENABLED, REGISTRY_NOTICES_ENABLED and
REGISTRY_PAYMENTS_REQUIRED (registry 2: 0 until the owner offers them), REGISTRY_OPEN (0) and REGISTRY_PUBLISHERS (lane Q2:
who may publish while the registry is not open to outside records),
REGISTRY_CRYPTO_PATH (where registry_crypto lives when not in the patentref clone, gate 5.14), REGISTRY_KEYS_DIR (its
keyring, /data/registry/keys), REGISTRY_READ_CACHE (the local read cache of checklist 5.15, default
<LAPSE_DATA_DIR>/read_cache.sqlite3), REGISTRY_READ_CACHE_FILE_MAX (20 MB), REGISTRY_DB_RETRY_SECONDS (5: how often a
background thread tries the database again while requests fail fast during an outage), plus everything lapse_local reads.
"""
import os

from pvapi.settings.lapse_local import *  # noqa: F401,F403
from pvapi.settings.lapse_local import INSTALLED_APPS

# The registry process serves no admin and no session pages, so the apps that need session middleware are left out
# (Django's system checks would otherwise stop `manage.py migrate` on the missing middleware).
INSTALLED_APPS = [a for a in INSTALLED_APPS if a not in ("django.contrib.admin", "django.contrib.messages")] + ["lapse_registry"]
ROOT_URLCONF = "lapse_registry.urls"
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
APPEND_SLASH = False

REGISTRY_DATABASE_URL = os.environ.get("REGISTRY_DATABASE_URL", "")
REGISTRY_PUBLIC_BASE = os.environ.get("REGISTRY_PUBLIC_BASE", "https://patentref.io")
REGISTRY_MAX_BODY = int(os.environ.get("REGISTRY_MAX_BODY", str(25 * 1024 * 1024)))
REGISTRY_FILE_MAX = int(os.environ.get("REGISTRY_FILE_MAX", str(20 * 1024 * 1024)))
REGISTRY_READ_PER_MINUTE = int(os.environ.get("REGISTRY_READ_PER_MINUTE", "120"))
REGISTRY_WRITE_PER_MINUTE = int(os.environ.get("REGISTRY_WRITE_PER_MINUTE", "20"))
REGISTRY_PUBLISH_PER_ADDRESS_DAY = int(os.environ.get("REGISTRY_PUBLISH_PER_ADDRESS_DAY", "30"))
REGISTRY_RECEIPTS_ENABLED = os.environ.get("REGISTRY_RECEIPTS_ENABLED", "0") == "1"
REGISTRY_SEALED_ENABLED = os.environ.get("REGISTRY_SEALED_ENABLED", "0") == "1"
# registry 2: each offered when the owner decides (prices, checklist 10.6; the open forms of 10.9); off by default
REGISTRY_MONITORING_ENABLED = os.environ.get("REGISTRY_MONITORING_ENABLED", "0") == "1"
REGISTRY_CONTRIBUTION_LOGS_ENABLED = os.environ.get("REGISTRY_CONTRIBUTION_LOGS_ENABLED", "0") == "1"
REGISTRY_CLAIMS_ENABLED = os.environ.get("REGISTRY_CLAIMS_ENABLED", "0") == "1"
REGISTRY_NOTICES_ENABLED = os.environ.get("REGISTRY_NOTICES_ENABLED", "0") == "1"
REGISTRY_PAYMENTS_REQUIRED = os.environ.get("REGISTRY_PAYMENTS_REQUIRED", "0") == "1"
# Who may publish (PatentRef lane Q2): the registry is not open to outside records yet (the terms; the first outside record
# waits for the entity and counsel, decisions.md 2026-10-01). Until REGISTRY_OPEN=1, only the accounts named in
# REGISTRY_PUBLISHERS may publish or run the preflight: comma separated Supabase user ids or confirmed email addresses,
# set in /data/registry/api.env, never in code. Everyone else gets 403 ERR_NOT_OPEN. Empty and closed: nobody publishes.
REGISTRY_OPEN = os.environ.get("REGISTRY_OPEN", "0") == "1"
REGISTRY_PUBLISHERS = tuple(x.strip() for x in os.environ.get("REGISTRY_PUBLISHERS", "").split(",") if x.strip())
REGISTRY_READ_CACHE = os.environ.get("REGISTRY_READ_CACHE", os.path.join(os.environ.get("LAPSE_DATA_DIR", "/data/registry"),
                                                                          "read_cache.sqlite3"))
REGISTRY_READ_CACHE_FILE_MAX = int(os.environ.get("REGISTRY_READ_CACHE_FILE_MAX", str(20 * 1024 * 1024)))
REGISTRY_DB_RETRY_SECONDS = float(os.environ.get("REGISTRY_DB_RETRY_SECONDS", "5"))
# a JSON submission carries up to 1,000,000 characters of full text (about 4 MB as UTF-8); files are streamed to disk
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("REGISTRY_JSON_MAX", str(5 * 1024 * 1024)))
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440
