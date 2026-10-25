"""manage.py waitlist <summary|purge|check|open|notice>: the operator's side of the waitlist (wave 4 Q1).

  summary                    counts only (confirmed, waiting, per topic, openings); never an address
  purge                      the retention rules of lapse_accounts/waitlist.py (also run daily by patentref-counts.timer)
  check                      whether the Resend relay settings are readable (prints host, port and whether the password
                             file can be read; never the password)
  open <topic> --subject S --body-file F [--send]
                             the opening notice of a topic, once per address; records the opening (the 30-day clock of
                             the retention starts then). Without --send: a dry run that prints the count and the message
  notice <topic> --subject S --body-file F [--send]
                             the one other notice an address may get, to those who chose the topic and never had one

Topics: lapse (Lapse alerts and lists), rules (Rules updates), registry (Registry opening).
"""
import json
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from lapse_accounts import waitlist


class Command(BaseCommand):
    help = "The waitlist: summary, purge, check, open <topic>, notice <topic>"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["summary", "purge", "check", "open", "notice"])
        parser.add_argument("topic", nargs="?", choices=list(waitlist.TOPICS))
        parser.add_argument("--subject", default="")
        parser.add_argument("--body-file", default="")
        parser.add_argument("--send", action="store_true", help="send for real (without it, a dry run)")

    def handle(self, *args, **o):
        act = o["action"]
        if act == "summary":
            self.stdout.write(json.dumps(waitlist.summary(), sort_keys=True))
            return
        if act == "purge":
            self.stdout.write(json.dumps({"purged": waitlist.purge()}, sort_keys=True))
            return
        if act == "check":
            try:
                cfg = waitlist.mail_env()
            except waitlist.NoSender as e:
                self.stdout.write(f"WAITLIST MAIL: NOT READY ({e})")
                sys.exit(1)
            readable = Path(cfg["REGISTRY_SMTP_PASSWORD_FILE"]).is_file()
            try:
                Path(cfg["REGISTRY_SMTP_PASSWORD_FILE"]).read_bytes()
            except OSError:
                readable = False
            state = "READY" if readable else "NOT READY (the password file cannot be read)"
            self.stdout.write(f"WAITLIST MAIL: {state} (relay {cfg['REGISTRY_SMTP_HOST']}:{cfg['REGISTRY_SMTP_PORT']}, "
                              f"open {waitlist.is_open()[0]}, opens {waitlist.is_open()[1]})")
            sys.exit(0 if readable else 1)
        if not o["topic"]:
            raise CommandError(f"{act} needs a topic: {', '.join(waitlist.TOPICS)}")
        if not o["subject"] or not o["body_file"]:
            raise CommandError("--subject and --body-file are required")
        text = Path(o["body_file"]).read_text(encoding="utf-8")
        kind = "opening" if act == "open" else "other"
        try:
            out = waitlist.notify(o["topic"], kind, o["subject"], text, send=o["send"])
        except ValueError as e:
            raise CommandError(str(e)) from None
        if not o["send"]:
            sample = waitlist.message("someone@example.com", o["subject"].strip(), text,
                                      f"you joined the PatentRef waitlist at {waitlist.base_url()}/waitlist/ for: "
                                      f"{waitlist.TOPICS[o['topic']]}.", preview=True)
            self.stdout.write(f"DRY RUN: {out['recipients']} recipients for the {kind} notice of {o['topic']}; "
                              f"add --send to send. The message (to a placeholder address):\n")
            self.stdout.write(sample.as_string())
            return
        self.stdout.write(json.dumps({"topic": o["topic"], "kind": kind, **out}, sort_keys=True))
