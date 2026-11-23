"""manage.py client_counts <day|show>: the daily counts of client kinds (release AH, PatentRef 1.25.0; lapse_accounts/clients.py),
for the operator. The counter is OFF unless settings.LAPSE_CLIENT_COUNTS is on; while it is off these print that it is off and
write nothing.

  day [--date YYYY-MM-DD] [--out DIR]   one UTC day's counts (default yesterday): per area, the requests of each kind, the
                                        service's own apart; with --out, written once to DIR/clients_<day>.json (a file
                                        already there is left as it is), which the daily counts job (patentref
                                        ops/systemd/counts.sh) does at 01:13 UTC
  show [--days N]                       the last N days (default 7) and today so far, oldest first

Counts only: the table holds (day, area, kind, n) and nothing else; no User-Agent text, address, key, prefix or path.
"""
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from lapse_accounts import clients, requestlog

RULE = ("per UTC day and area (data, meta, file, page), the responses of each kind of client, read from the User-Agent and not "
        "kept; own: the service's own keys and tools; counts only")


def day_row(day, path=None):
    """{day, rule, counter, areas: {area: {kind: n}}, own: {area: n}, requests} from client_counts, or with a note."""
    path = path if path is not None else requestlog.log_path()
    row = {"day": day, "rule": RULE, "counter": "on" if clients.on() else "off", "areas": {}, "own": {}, "requests": 0}
    if not path or not os.path.exists(path):
        row["note"] = "no usage-log file"
        return row
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        row["note"] = f"usage-log file unreadable ({exc.__class__.__name__})"
        return row
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'client_counts'").fetchone():
            row["note"] = "no client_counts table: the counter has not run"
            return row
        for area, kind, n in con.execute("SELECT area, kind, n FROM client_counts WHERE day = ? ORDER BY area, kind", (day,)):
            if kind == "own":
                row["own"][area] = row["own"].get(area, 0) + int(n)
            else:
                row["areas"].setdefault(area, {})[kind] = int(n)
            row["requests"] += int(n)
    except sqlite3.Error as exc:
        row["note"] = f"usage-log read failed ({exc.__class__.__name__})"
    finally:
        con.close()
    return row


class Command(BaseCommand):
    help = "The daily counts of client kinds: day, show (off unless settings.LAPSE_CLIENT_COUNTS is on)"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["day", "show"])
        parser.add_argument("--date", default="", help="day: the UTC day, YYYY-MM-DD (default yesterday)")
        parser.add_argument("--out", default="", help="day: write the row once to <out>/clients_<day>.json")
        parser.add_argument("--days", type=int, default=7, help="show: days back (0 to 40, default 7)")

    def handle(self, *args, **o):
        today = datetime.now(timezone.utc).date()
        if o["action"] == "show":
            if o["days"] < 0 or o["days"] > 40:
                raise CommandError("--days must be 0 to 40")
            rows = [day_row((today - timedelta(days=i)).isoformat()) for i in range(o["days"], -1, -1)]
            self.stdout.write(json.dumps({"counter": "on" if clients.on() else "off", "rows": rows}, sort_keys=True))
            return
        if o["date"]:
            try:
                d = datetime.strptime(o["date"], "%Y-%m-%d").date()
            except ValueError:
                raise CommandError(f"--date must be a date, YYYY-MM-DD: {o['date']!r}") from None
        else:
            d = today - timedelta(days=1)
        day = d.isoformat()
        if not clients.on():
            self.stdout.write(json.dumps({"day": day, "counter": "off", "written": False,
                                          "note": "the client counter is off (settings.LAPSE_CLIENT_COUNTS)"}))
            return
        row = day_row(day)
        if not o["out"]:
            self.stdout.write(json.dumps(row, sort_keys=True))
            return
        out = Path(o["out"])
        out.mkdir(parents=True, exist_ok=True)
        f = out / f"clients_{day}.json"
        if f.exists():
            self.stdout.write(json.dumps({"day": day, "counter": "on", "written": False, "file": str(f), "note": "already written"}))
            return
        tmp = out / f".clients_{day}.json.tmp"
        tmp.write_text(json.dumps(row, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, f)
        self.stdout.write(json.dumps({"day": day, "counter": "on", "written": True, "file": str(f), "requests": row["requests"]}))
