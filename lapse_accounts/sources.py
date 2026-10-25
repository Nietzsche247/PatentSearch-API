"""The sign-up source count (PatentRef wave 4 Q1): how many keys were created each week from links that carried a
campaign code, by code, and nothing more.

The sign-up page (/api/v1/meta/signup/) reads one code from its own address, utm_source, keeps it only when it is 1 to 32
letters, digits and hyphens, carries it through the email confirmation (the confirmation link returns to the page with
the same utm_source) and sends it as "source" with POST /api/v1/meta/keys/. When that POST creates the account's key,
`count()` adds one to the row (ISO week, code) of `lapse_accounts_signupsource`. The code is not written to the key
record, the account row or the Supabase user, and no request, address or time is kept with it; a code outside the rule
is dropped, here and in the page. It counts only once Privacy Policy beta-4, which describes it, is in force on this
server (`legal.in_force`).

Read-out for the operator: `python manage.py signup_sources [--weeks N]`.
"""
import re
from datetime import datetime, timedelta, timezone

from django.db import connection

TABLE = "lapse_accounts_signupsource"
CODE = re.compile(r"[A-Za-z0-9-]{1,32}")
POLICY = ("privacy", 4)   # the document and the beta version that describe this count


def clean(code):
    """The code when it keeps the rule (letters, digits and hyphens, 1 to 32), else None. Case is kept as sent."""
    if not isinstance(code, str):
        return None
    return code if CODE.fullmatch(code) else None


def iso_week(now=None):
    """The ISO week of `now` (UTC), as YYYY-Www."""
    now = now or datetime.now(timezone.utc)
    y, w, _ = now.astimezone(timezone.utc).isocalendar()
    return f"{y}-W{w:02d}"


def counting(today=None):
    from lapse_accounts import legal

    return legal.in_force(*POLICY, today=today)[0]


def count(code, now=None, today=None):
    """Add one to this week's row for the code. Returns the new count, or None when nothing was counted (no valid code,
    or the policy that describes the count is not in force yet)."""
    code = clean(code)
    if code is None or not counting(today=today):
        return None
    with connection.cursor() as cur:
        cur.execute(f"INSERT INTO {TABLE} (week, code, count) VALUES (%s, %s, 1) "
                    f"ON CONFLICT(week, code) DO UPDATE SET count = count + 1 RETURNING count", [iso_week(now), code])
        row = cur.fetchone()
    return row[0] if row else None


def table(weeks=12, now=None):
    """[(week, code, count)] for the last `weeks` ISO weeks, newest week first, then the larger count first."""
    from lapse_accounts.models import SignupSource

    now = now or datetime.now(timezone.utc)
    keep = {iso_week(now - timedelta(weeks=i)) for i in range(max(1, int(weeks)))}
    rows = [(r.week, r.code, r.count) for r in SignupSource.objects.filter(week__in=keep)]
    rows.sort(key=lambda r: (-r[2], r[1].lower()))   # within a week: the larger count first
    rows.sort(key=lambda r: r[0], reverse=True)        # stable: the newest week first
    return rows
