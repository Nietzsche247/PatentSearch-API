"""manage.py own_keys: which of the operator's own keys the status page's figures and the usage counters leave out, as counts
(release AH, PatentRef 1.25.0). For the operator and the deploy's checks: how many key prefixes each rule names (files: the
operator key, the GitHub probe's key and the beta test account's key, read from their files inside this process; domains:
accounts on the operator's own email domains; names: keys named as the service's own tools name theirs, revoked ones
included; list: the operator's own list of addresses and prefixes), how many prefixes in all, and over the last N days
(default 30) how many distinct key prefixes sent counted data requests, own and outside, with their request counts.

Counts only: no key, prefix, email or address is printed (lapse_accounts/status.py own_key_rules, own_key_prefixes).
"""
import json
import os
import sqlite3
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from lapse_accounts import status, usage_counts


class Command(BaseCommand):
    help = "The operator's own keys left out of the figures, as counts"

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30, help="the window of the request log (1 to 35, default 30)")

    def handle(self, *args, **o):
        days = o["days"]
        if days < 1 or days > 35:
            raise CommandError("--days must be 1 to 35 (the request rows are kept 35 days)")
        rules = status.own_key_rules()
        own = set()
        for got in rules.values():
            own |= got
        out = {"rules": {k: len(v) for k, v in rules.items()}, "own_prefixes": len(own),
               "list_file": "present" if os.path.isfile(str(getattr(settings, "LAPSE_OWN_KEYS_LIST", "") or "")) else "absent",
               "days": days, "data_requests": None}
        path = usage_counts.log_path()
        if path and os.path.exists(path):
            since = time.time() - days * usage_counts.DAY
            try:
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
                try:
                    rows = con.execute(f"SELECT prefix, COUNT(*) FROM request_log WHERE ts >= ? AND COALESCE(prefix, '') != '' AND "
                                       f"path LIKE ? AND path NOT LIKE ? AND {usage_counts.COUNTED} GROUP BY prefix",
                                       (since, usage_counts.DATA + "%", usage_counts.META + "%")).fetchall()
                finally:
                    con.close()
                mine = [(p, n) for p, n in rows if p in own]
                theirs = [(p, n) for p, n in rows if p not in own]
                out["data_requests"] = {"own_keys": len(mine), "own_calls": sum(n for _, n in mine),
                                        "outside_keys": len(theirs), "outside_calls": sum(n for _, n in theirs)}
            except sqlite3.Error as exc:
                out["note"] = f"usage-log read failed ({exc.__class__.__name__})"
        else:
            out["note"] = "no usage-log file"
        self.stdout.write(json.dumps(out, sort_keys=True))
