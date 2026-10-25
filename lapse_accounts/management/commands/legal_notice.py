"""manage.py legal_notice <privacy|terms|acceptable-use> [--send]: the email notice of a coming change to a legal document
(wave 4 Q1). The Privacy Policy and the terms promise that material changes are announced by email, and on the status
page, at least 14 days before they take effect; the status page shows the notice by itself (legal.upcoming), this sends
the mail.

The document must be posted ahead of its effective date (its "Effective:" line in the future). Recipients: every distinct
address of an API key account (lapse_accounts AccountKey.email), less the suppression list (bounces and complaints). It is
a notice the terms require, so it carries no unsubscribe link (Privacy Policy section 1, Email). Each address gets it
once per document version (table legal_notice of the mail state file), so a rerun after a failure sends only to the rest.
It leaves through the Resend relay the waitlist uses (LAPSE_MAIL_ENV_FILE). Without --send: a dry run that prints the
count and the message and sends nothing.
"""
import json
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from django.core.management.base import BaseCommand, CommandError

from lapse_accounts import legal, mail, waitlist
from lapse_accounts.models import AccountKey

SCHEMA = "CREATE TABLE IF NOT EXISTS legal_notice(address TEXT NOT NULL, doc TEXT NOT NULL, version TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(address, doc, version))"


def notice(doc):
    """(subject, text) for the coming version of one document, or CommandError when it is not posted ahead."""
    coming = [c for c in legal.upcoming() if c["name"] == doc]
    if not coming:
        raise CommandError(f"{doc} has no version posted ahead of its effective date; nothing to announce")
    c = coming[0]
    base = waitlist.base_url()
    box = "privacy" if doc == "privacy" else "legal"
    subject = f"PatentRef {c['title']}: version {c['version']} takes effect on {c['effective']}"
    text = (f"The PatentRef {c['title']} changes on {c['effective']}. The new version, {c['version']}, is at {base}{c['url']}. "
            f"Until {c['effective']} the current version applies.\n\n"
            + (f"What is new: {c['summary']}\n\n" if c["summary"] else "")
            + "Nothing else changes. You get this mail because you have a PatentRef account, and we announce changes like "
              f"this by email at least 14 days before they take effect. Questions: {box}@patentref.io (until that mailbox "
              "is live: hello@patentref.io).\n\n" + waitlist.FOOTER_POSTAL + "\n")
    if waitlist.check_text(text) or waitlist.check_text(subject):
        raise CommandError("the notice text breaks the wording rules (an em-dash or an en-dash)")
    return c, subject, text


class Command(BaseCommand):
    help = "Email account holders the notice of a legal document posted ahead of its effective date"

    def add_arguments(self, parser):
        parser.add_argument("doc", choices=["privacy", "terms", "acceptable-use"])
        parser.add_argument("--send", action="store_true", help="send for real (without it, a dry run)")

    def handle(self, *args, **o):
        c, subject, text = notice(o["doc"])
        addresses = sorted({mail.norm(e) for e in AccountKey.objects.values_list("email", flat=True)} - {""})
        with mail.db() as con:
            con.execute(SCHEMA)
            done = {a for (a,) in con.execute("SELECT address FROM legal_notice WHERE doc = ? AND version = ?", (o["doc"], c["version"]))}
        todo = [a for a in addresses if a not in done]
        if not o["send"]:
            self.stdout.write(f"DRY RUN: {len(todo)} of {len(addresses)} account addresses still to get this notice; add --send.\n")
            self.stdout.write(f"Subject: {subject}\n\n{text}")
            return
        out = {"doc": o["doc"], "version": c["version"], "accounts": len(addresses), "already": len(done), "sent": 0,
               "suppressed": 0, "failed": 0}
        for a in todo:
            if mail.is_suppressed(a):
                out["suppressed"] += 1
                continue
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = mail.FROM, a, subject
            msg["Date"], msg["Message-ID"] = format_datetime(waitlist._now()), make_msgid(domain="mail.patentref.io")
            msg.set_content(text)
            try:
                waitlist.transport(msg)
            except Exception:  # noqa: BLE001 - counted; a rerun sends to the rest
                out["failed"] += 1
                continue
            with mail.db() as con:
                con.execute(SCHEMA)
                con.execute("INSERT INTO legal_notice(address, doc, version, at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                            (a, o["doc"], c["version"], waitlist._iso(waitlist._now())))
            out["sent"] += 1
        self.stdout.write(json.dumps(out, sort_keys=True))
        self.stdout.write(f"LEGAL NOTICE: {'PASS' if not out['failed'] else 'PARTIAL'} ({out['sent']} sent, {out['failed']} failed)")
