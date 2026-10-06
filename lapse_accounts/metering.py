"""Monthly request counter and Free-tier cap (checklist gate 5.10).

What counts: every request to a data endpoint under `/api/v1/` (not `/api/v1/meta/`) that carried an
`X-Api-Key` and was answered with a status below 500 other than 403 and 429. Bad queries (400) count,
because the query ran; a rejected key, a throttled request and a server error do not. The count is
reserved at admission by `lapse_accounts.throttling.MonthlyKeyThrottle` with one atomic conditional
increment (`lapse_accounts.ratelimit.bump`), so the cap holds exactly under parallel traffic, and
`lapse_accounts.middleware.MeteringMiddleware` refunds a reservation whose response does not count.

The month is the UTC calendar month; the count resets at 00:00 UTC on the first of the next month.
Keys minted from a Supabase account are counted per user (`user:<id>`), so rotating a key does not
reset the month. Keys without an account (the operator's own) are counted per key prefix and have no
monthly cap.
"""
import threading
import time
from datetime import datetime, timezone

from django.conf import settings
from django.core.cache import cache

from lapse_accounts.models import PLAN_FREE, AccountKey, MonthlyUsage

_lock = threading.Lock()
_subjects = {}  # prefix -> (expires_at, info)
_TTL = 300


def plan_limits(plan):
    """(monthly_limit or None, per_minute_limit) for a plan. Only Free exists until gate 6.2."""
    if plan == PLAN_FREE:
        return int(getattr(settings, "LAPSE_FREE_MONTHLY_LIMIT", 1000)), int(getattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 45))
    return None, int(getattr(settings, "LAPSE_FREE_MINUTE_LIMIT", 45))


def current_month(now=None):
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m")


def month_reset(now=None):
    """First instant of the next UTC month."""
    now = now or datetime.now(timezone.utc)
    if now.month == 12:
        return datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    return datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)


def seconds_to_reset(now=None):
    now = now or datetime.now(timezone.utc)
    return max(1, int((month_reset(now) - now).total_seconds()))


def subject_for_prefix(prefix):
    """{"subject", "plan", "monthly_limit", "account": bool} for a key prefix, cached in process and in
    the Django cache so a database hiccup does not take the counter down with it."""
    now = time.time()
    with _lock:
        hit = _subjects.get(prefix)
        if hit and hit[0] > now:
            return hit[1]
    cache_key = "lapse_subject:" + prefix
    info = cache.get(cache_key)
    if info is None:
        acct = AccountKey.objects.filter(api_key__prefix=prefix).select_related("api_key").first()
        if acct is None:
            info = {"subject": "key:" + prefix, "plan": None, "monthly_limit": None, "account": False}
        else:
            info = {"subject": acct.subject, "plan": acct.plan, "monthly_limit": plan_limits(acct.plan)[0], "account": True}
        cache.set(cache_key, info, _TTL)
    with _lock:
        if len(_subjects) > 10000:
            _subjects.clear()
        _subjects[prefix] = (now + _TTL, info)
    return info


def forget_prefix(prefix):
    with _lock:
        _subjects.pop(prefix, None)
    cache.delete("lapse_subject:" + prefix)


def usage_count(subject, month=None):
    month = month or current_month()
    row = MonthlyUsage.objects.filter(subject=subject, month=month).only("count").first()
    return row.count if row else 0


def usage_summary(prefix=None, account=None):
    """The body of GET /api/v1/meta/usage/."""
    now = datetime.now(timezone.utc)
    month = current_month(now)
    if account is not None:
        subject, plan = account.subject, account.plan
        prefix = account.prefix
    elif prefix is not None:
        info = subject_for_prefix(prefix)
        subject, plan = info["subject"], info["plan"]
    else:
        subject, plan = None, PLAN_FREE
    monthly, per_minute = plan_limits(plan)
    count = usage_count(subject, month) if subject else 0
    return {
        "error": False,
        "plan": plan,
        "key_prefix": prefix,
        "month": month,
        "count": count,
        "monthly_limit": monthly,
        "remaining": (max(0, monthly - count) if monthly is not None else None),
        "per_minute_limit": per_minute,
        "resets_at": month_reset(now).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
