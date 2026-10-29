"""manage.py mail_unsubscribe <token or unsubscribe URL>: record an unsubscribe that came by mail (release W; gate 5.16,
independent audit 11 finding F5). Every List-Unsubscribe header PatentRef sends names the https one-click URL and a mailto
to hello@patentref.io whose subject is "unsubscribe <token>" (lapse_accounts/mail.py list_unsubscribe). A mail client that
only mails sends that; the operator reads the token in the subject and enters it here. It records exactly what the
one-click POST of /u/<token> records (and leaving the waitlist deletes the waitlist row), and prints the masked address
and the kind, never the address.
"""
import re

from django.core.management.base import BaseCommand, CommandError

from lapse_accounts import mail


class Command(BaseCommand):
    help = "Record an unsubscribe requested by mail (the token of the mailto's subject, or the https unsubscribe URL)"

    def add_arguments(self, parser):
        parser.add_argument("token", help="the token, the subject 'unsubscribe <token>' or https://patentref.io/u/<token>")

    def handle(self, *args, **o):
        m = re.search(r"([A-Za-z0-9_-]{16,64})/?\s*$", o["token"].strip())
        done = mail.record_unsubscribe(m.group(1)) if m else None
        if done is None:
            raise CommandError("unknown unsubscribe token: nothing recorded (check the subject of the mail for the whole token)")
        address, kind = done
        self.stdout.write(f"UNSUBSCRIBED: {mail.masked(address)} from {mail.KINDS.get(kind, kind)}"
                          + (" (a security kind: it keeps arriving)" if kind in mail.SECURITY_KINDS else ""))
