"""Per-minute and monthly throttles for API keys, exact across gunicorn workers.

`PlanKeyThrottle` answers the same way upstream's `APIKeyThrottle` does (status 429, `Retry-After`, the
body `{"detail": "Request was throttled. Expected available in N seconds."}`) with the rate chosen per
key: a key minted from a Supabase account gets its plan's per-minute allowance (`LAPSE_FREE_MINUTE_LIMIT`,
45/m), a key without an account (the operator's own) keeps the `key` rate from settings
(`LAPSE_THROTTLE_RATE`, upstream's 45/m by default). It no longer uses DRF's cache-backed history (that
leaked under concurrent traffic, see `lapse_accounts.ratelimit`); it counts admitted requests per
subject in a fixed clock-minute window with one atomic conditional increment on the Django DB.

`MonthlyKeyThrottle` runs after it. It reserves the request against the month's count with the same
atomic conditional increment, so the cap holds exactly even when requests arrive in parallel; when the
plan's allowance is used up it denies with the same 429 shape and `Retry-After` counting to the first of
next month (UTC). `MeteringMiddleware` refunds the reservation when the response turns out not to count
(403, 429, 5xx), so the count is exactly the requests answered below 500 other than 403 and 429.

`MetaThrottle` is the per-IP rate on the account endpoints (sign-up page, keys, usage), on the same
window counter.
"""
import time

from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

from lapse_accounts import metering, ratelimit

timer = time.time  # tests pin this to keep a burst inside one window

SCOPE_KEY = "key"
SCOPE_META = "meta"


def _http_request(request):
    """The Django HttpRequest behind a DRF Request (the middleware sees that one)."""
    return getattr(request, "_request", request)


def _key_subject(request):
    """(subject, account?) for the request's key: `user:<id>` for a key minted from an account,
    `key:<prefix>` for any other key, `ip:<addr>` when no key was sent."""
    key = request.META.get("HTTP_X_API_KEY")
    if key and "." in key:
        info = metering.subject_for_prefix(key.split(".", 1)[0])
        return info["subject"], info["account"], info["plan"]
    return None, False, None


class WindowThrottle(SimpleRateThrottle):
    """Fixed-window counter throttle. Subclasses set `scope` and override `subject()` and, if the rate
    depends on the subject, `limits()`."""

    scope = None

    def __init__(self):
        super().__init__()  # parses THROTTLE_RATES[scope] into num_requests, duration
        self._wait = None

    def subject(self, request):
        raise NotImplementedError

    def limits(self, request, subject):
        return self.num_requests, self.duration

    def allow_request(self, request, view):
        if self.rate is None:
            return True
        subject = self.subject(request)
        if subject is None:
            return True
        num_requests, duration = self.limits(request, subject)
        window, wait = ratelimit.minute_window(self.scope, duration, timer())
        count = ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", window, num_requests)
        if count is None:
            self._wait = wait
            setattr(_http_request(request), "_lapse_minute_denied", True)
            return False
        if count == 1:
            ratelimit.drop_old_windows(subject, window)
        return True

    def wait(self):
        return self._wait


class PlanKeyThrottle(WindowThrottle):
    scope = SCOPE_KEY

    def subject(self, request):
        subject, _, _ = _key_subject(request)
        return subject if subject is not None else "ip:" + (self.get_ident(request) or "unknown")

    def limits(self, request, subject):
        if subject.startswith("user:"):
            _, _, plan = _key_subject(request)
            return metering.plan_limits(plan)[1], 60
        return self.num_requests, self.duration


class MonthlyKeyThrottle(BaseThrottle):
    """Reserve the request against the month's count; deny when the plan's allowance is used up."""

    def __init__(self):
        self._wait = None

    def wait(self):
        return self._wait

    def allow_request(self, request, view):
        key = request.META.get("HTTP_X_API_KEY")
        if not key or "." not in key:
            return True  # no key: the permission class already answered 403
        http = _http_request(request)
        if getattr(http, "_lapse_minute_denied", False):
            return True  # DRF runs every throttle; the per-minute rule already denied, nothing to reserve
        info = metering.subject_for_prefix(key.split(".", 1)[0])
        month = metering.current_month()
        count = ratelimit.bump(ratelimit.MONTH_TABLE, info["subject"], "month", month, info["monthly_limit"])
        if count is None:
            self._wait = metering.seconds_to_reset()
            return False
        http._lapse_reserved = (info["subject"], month)
        return True


class MetaThrottle(WindowThrottle):
    """Per-IP rate for the account endpoints (sign-up page, key minting, usage); scope `meta`."""

    scope = SCOPE_META

    def subject(self, request):
        return "ip:" + (self.get_ident(request) or "unknown")
