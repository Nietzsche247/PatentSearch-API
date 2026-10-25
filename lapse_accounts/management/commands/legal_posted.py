"""manage.py legal_posted [--stamp] [--unstamp <doc> <version>]: the posted date of each legal document version that the
repo writes with {{posted}} and {{effective}} (release wave4-Q, lapse_accounts/legal.py).

Without an option it prints, for each such document, its version, the posted date and the effective date 14 days later,
and whether the deploy has recorded them; nothing changes. --stamp records today (UTC) for every version not recorded
yet and never moves a recorded date: ops/deploy_release.sh runs it once the release has passed its checks, so the
posted date is the day the text went live. --unstamp forgets one version's date, for a rollback that took the text off
the site (the next deploy that serves it records its own day). The last line is LEGAL POSTED: PASS when every such
document has a recorded date, else LEGAL POSTED: WAITING.
"""
import json

from django.core.management.base import BaseCommand, CommandError

from lapse_accounts import legal


class Command(BaseCommand):
    help = "Show or record the posted date of the legal documents the deploy dates"

    def add_arguments(self, parser):
        parser.add_argument("--stamp", action="store_true", help="record today (UTC) for each version not recorded yet")
        parser.add_argument("--unstamp", nargs=2, metavar=("DOC", "VERSION"), help="forget one version's recorded date")
        parser.add_argument("--json", action="store_true", help="one JSON object instead of the lines")

    def handle(self, *args, **o):
        if o["stamp"] and o["unstamp"]:
            raise CommandError("--stamp and --unstamp do not go together")
        if o["unstamp"]:
            doc, version = o["unstamp"]
            if doc not in legal.DOCS:
                raise CommandError(f"unknown document {doc}")
            gone = legal.unstamp(doc, version)
            self.stdout.write(f"{doc} {version}: {'recorded date removed' if gone else 'no recorded date'}")
        rows = legal.stamp() if o["stamp"] else [p for p in (legal.posting(n) for n in legal.ORDER) if p]
        out = [{"doc": p["name"], "version": p["version"], "posted": p["posted"].isoformat(),
                "effective": p["effective"].isoformat(), "recorded": p["stamped"]} for p in rows]
        ok = all(r["recorded"] for r in out)
        if o["json"]:
            self.stdout.write(json.dumps({"file": str(legal.posted_file()), "documents": out, "ok": ok}, sort_keys=True))
            return
        for r in out:
            state = "recorded" if r["recorded"] else "not recorded yet (shown as today)"
            self.stdout.write(f"{r['doc']} {r['version']}: posted {r['posted']}, effective {r['effective']} ({state})")
        self.stdout.write(f"LEGAL POSTED: {'PASS' if ok else 'WAITING'} ({len(out)} document(s) dated by the deploy; {legal.posted_file()})")
