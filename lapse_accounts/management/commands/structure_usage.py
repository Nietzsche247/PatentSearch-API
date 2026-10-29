"""manage.py structure_usage <show|day|tripwire|week>: the structure layer's usage counter for the operator (release X; week:
the extraction trial's weekly report, gate S1.1).

  show [--days N]                 one row per UTC day for the last N complete days (default 30) and today so far, oldest
                                  first, then the trailing 30 days ending today, as JSON
  day [--date YYYY-MM-DD] [--out DIR]
                                  one day's row (default yesterday, UTC) with the trailing 30 days ending that day; with
                                  --out, written once to DIR/<day>.json (a file already there is left as it is), which is
                                  what the daily counts job (patentref ops/systemd/counts.sh) does at 01:13 UTC
  tripwire --since YYYY-MM-DD [--days 30]
                                  the S1 tripwire's usage legs over the window from Stage 1's date
  week [--date YYYY-MM-DD] [--out DIR]
                                  the extraction trial's weekly usage report (gate S1.1) for the ISO week holding the date
                                  (default the last complete week): its requests (asked, cache hits, jobs, refusals, polls,
                                  page views, distinct outside keys) and its own figures (extractions, requesting accounts,
                                  spend, the cap and what is left); with --out, written once to DIR/extract_week_<Monday>.json,
                                  which the daily counts job does

Counts only (lapse_accounts/usage_counts.py): calls, distinct outside key prefixes as a number, shares. No key prefix,
address or patent number is printed or written. The request rows behind them are kept 35 days.
"""
import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from lapse_accounts import usage_counts


class Command(BaseCommand):
    help = "The structure layer's usage counter: show, day, tripwire, week"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["show", "day", "tripwire", "week"])
        parser.add_argument("--days", type=int, default=None, help="show: days back (default 30); tripwire: window length (default 30)")
        parser.add_argument("--date", default="", help="day: the UTC day, YYYY-MM-DD (default yesterday)")
        parser.add_argument("--out", default="", help="day: write the row once to <out>/<day>.json")
        parser.add_argument("--since", default="", help="tripwire: Stage 1's date, YYYY-MM-DD")

    def _day(self, s, name):
        try:
            return usage_counts.parse_day(s)
        except ValueError:
            raise CommandError(f"{name} must be a date, YYYY-MM-DD: {s!r}") from None

    def handle(self, *args, **o):
        act = o["action"]
        if act == "show":
            days = 30 if o["days"] is None else o["days"]
            if days < 0 or days > 40:
                raise CommandError("--days must be 0 to 40 (the request rows are kept 35 days)")
            rows = usage_counts.last_days(days)
            today = usage_counts.parse_day(rows[-1]["day"])
            last = usage_counts.day_row(today)
            self.stdout.write(json.dumps({"rows": rows, "trailing_30d": last["trailing_30d"]}, sort_keys=True))
            return
        if act == "day":
            day = self._day(o["date"], "--date") if o["date"] else usage_counts.yesterday()
            row = usage_counts.day_row(day)
            if not o["out"]:
                self.stdout.write(json.dumps(row, sort_keys=True))
                return
            out = Path(o["out"])
            out.mkdir(parents=True, exist_ok=True)
            f = out / f"{day.isoformat()}.json"
            if f.exists():
                self.stdout.write(json.dumps({"day": day.isoformat(), "written": False, "file": str(f), "note": "already written"}))
                return
            tmp = out / f".{day.isoformat()}.json.tmp"
            tmp.write_text(json.dumps(row, sort_keys=True, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, f)
            self.stdout.write(json.dumps({"day": day.isoformat(), "written": True, "file": str(f), "claim_tree_calls": row["claim_tree_calls"],
                                          "claim_tree_keys": row["claim_tree_keys"], "share_percent": row["share_percent"]}))
            return
        if act == "week":
            monday = usage_counts.week_start(self._day(o["date"], "--date")) if o["date"] else usage_counts.last_week()
            row = usage_counts.week_row(monday)
            if not o["out"]:
                self.stdout.write(json.dumps(row, sort_keys=True))
                return
            out = Path(o["out"])
            out.mkdir(parents=True, exist_ok=True)
            f = out / f"extract_week_{monday.isoformat()}.json"
            if f.exists():
                self.stdout.write(json.dumps({"week": monday.isoformat(), "written": False, "file": str(f), "note": "already written"}))
                return
            tmp = out / f".extract_week_{monday.isoformat()}.json.tmp"
            tmp.write_text(json.dumps(row, sort_keys=True, indent=1) + "\n", encoding="utf-8")
            os.replace(tmp, f)
            self.stdout.write(json.dumps({"week": monday.isoformat(), "written": True, "file": str(f),
                                          "extractions": row["trial"].get("extractions"), "asked": row["requests"]["asked"],
                                          "cache_hits": row["requests"]["cache_hits"], "extract_keys": row["requests"]["extract_keys"],
                                          "spend_usd": row["trial"].get("spend_usd")}))
            return
        if not o["since"]:
            raise CommandError("tripwire needs --since YYYY-MM-DD (the day Stage 1 opened)")
        days = 30 if o["days"] is None else o["days"]
        if days < 1 or days > 35:
            raise CommandError("--days must be 1 to 35 (the request rows are kept 35 days)")
        self.stdout.write(json.dumps(usage_counts.tripwire(self._day(o["since"], "--since"), days), sort_keys=True))
