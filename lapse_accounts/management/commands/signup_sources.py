"""manage.py signup_sources [--weeks N] [--json]: the weekly sign-up source count for the operator (wave 4 Q1).

One line per (ISO week, campaign code) with the number of keys created from a sign-up link that carried that code,
newest week first. The table holds nothing else: no account, key, address or time (lapse_accounts/sources.py).
"""
import json

from django.core.management.base import BaseCommand

from lapse_accounts import sources


class Command(BaseCommand):
    help = "Weekly count of keys created from sign-up links with a campaign code (utm_source), by code"

    def add_arguments(self, parser):
        parser.add_argument("--weeks", type=int, default=12, help="how many ISO weeks back, this one included (default 12)")
        parser.add_argument("--json", action="store_true", help="rows as JSON instead of a table")

    def handle(self, *args, **options):
        rows = sources.table(weeks=options["weeks"])
        counting = sources.counting()
        if options["json"]:
            self.stdout.write(json.dumps({"counting": counting, "rows": [{"week": w, "code": c, "count": n} for w, c, n in rows]}))
            return
        if not counting:
            self.stdout.write("note: not counting yet; the count starts when Privacy Policy beta-4 is in force on this server")
        if not rows:
            self.stdout.write(f"no sign-ups with a campaign code in the last {options['weeks']} weeks")
            return
        wc = max(4, max(len(c) for _, c, _ in rows))
        self.stdout.write(f"{'week':<8}  {'code':<{wc}}  count")
        last = None
        for week, code, n in rows:
            if last is not None and week != last:
                self.stdout.write("")
            self.stdout.write(f"{week:<8}  {code:<{wc}}  {n:>5}")
            last = week
        self.stdout.write(f"\ntotal {sum(n for _, _, n in rows)} in {len({w for w, _, _ in rows})} weeks")
