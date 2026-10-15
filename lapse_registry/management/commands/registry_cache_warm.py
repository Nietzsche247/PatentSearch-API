"""manage.py registry_cache_warm: copy every visible registry record to the local read cache (PatentRef checklist 5.15),
so a Supabase outage still serves records nobody has read lately. Run by patentref-registry-cache.timer every 10
minutes (settings pvapi.settings.registry, /data/registry/api.env). Prints one line of counts; exit 1 when the
database cannot be reached (the cache keeps what it had)."""
import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand

from lapse_registry import core, db, readcache


class Command(BaseCommand):
    help = "Copy every visible registry record, its proof, its file and the sitemap to the local read cache"

    def handle(self, *args, **options):
        base = getattr(settings, "REGISTRY_PUBLIC_BASE", "https://patentref.io").rstrip("/")
        try:
            out = readcache.warm(db.connection(), core.module("service"), base)
        except db.Unavailable as e:
            self.stdout.write(json.dumps({"ok": False, "error": str(e), "cache": readcache.figures()}))
            sys.exit(1)
        out.update({"ok": True, "cache": readcache.figures()})
        self.stdout.write(json.dumps(out, sort_keys=True))
