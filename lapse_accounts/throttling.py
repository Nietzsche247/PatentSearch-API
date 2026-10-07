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

Gate 5.6 adds two more, run after the two above and skipped when one of them already denied:
`IPCeilingThrottle` caps the keyed requests one client address may have admitted per clock minute across
all its keys (`LAPSE_IP_PER_MINUTE`, 300), so one address cannot multiply its allowance by minting keys;
`InflightThrottle` caps the requests one key (`LAPSE_INFLIGHT_PER_KEY`, 4) and one address
(`LAPSE_INFLIGHT_PER_IP`, 8) may have running at once, which bounds how many of the 16 gunicorn threads
any one client can hold; the reservation is released by `AbuseLimitMiddleware` when the response leaves.
The client address comes from `lapse_accounts.abuse.client_ip` (X-Forwarded-For from the trusted proxy).
"""
import time

from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

from lapse_accounts import abuse, metering, ratelimit

timer = time.time  # tests pin this to keep a burst inside one window

SCOPE_KEY = "key"
SCOPE_META = "meta"
SCOPE_IP = "ip"
SCOPE_INFLIGHT = "inflight"


def _setting(name, default):
    from django.conf import settings

    return int(getattr(settings, name, default))


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

    def get_ident(self, request):
        return abuse.client_ip(_http_request(request))

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
            http = _http_request(request)
            http._lapse_minute_denied = True
            http._lapse_denied = True
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
            http._lapse_denied = True
            return False
        http._lapse_reserved = (info["subject"], month)
        return True


class IPCeilingThrottle(WindowThrottle):
    """Keyed requests admitted per client address per clock minute, across every key it uses (gate 5.6).
    Counts only what the per-key and monthly rules admitted, so a burst a key already has denied does not
    use up its address's ceiling (an honest key behind the same NAT keeps working)."""

    scope = SCOPE_IP

    def subject(self, request):
        http = _http_request(request)
        if getattr(http, "_lapse_denied", False):
            return None
        key = request.META.get("HTTP_X_API_KEY")
        if not key or "." not in key:
            return None  # no key: the permission class answers 403, the middleware counts it per IP
        return "ip:" + self.get_ident(request)

    def limits(self, request, subject):
        return _setting("LAPSE_IP_PER_MINUTE", 300), 60


class InflightThrottle(BaseThrottle):
    """At most N requests running at once per key and per client address (gate 5.6). Reserved here with
    the atomic counter and released by `AbuseLimitMiddleware` on the way out; the reservation row is the
    current clock minute's, so a reservation a crashed worker never released vanishes with the minute."""

    def __init__(self):
        self._wait = 1

    def wait(self):
        return self._wait

    def allow_request(self, request, view):
        http = _http_request(request)
        if getattr(http, "_lapse_denied", False):
            return True
        key = request.META.get("HTTP_X_API_KEY")
        if not key or "." not in key:
            return True
        subject, _, _ = _key_subject(request)
        ip = "ip:" + abuse.client_ip(http)
        window, _ = ratelimit.minute_window(SCOPE_INFLIGHT, 60, timer())
        caps = [(subject, _setting("LAPSE_INFLIGHT_PER_KEY", 4)), (ip, _setting("LAPSE_INFLIGHT_PER_IP", 8))]
        held = []
        for subj, limit in caps:
            count = ratelimit.bump(ratelimit.WINDOW_TABLE, subj, "win", window, limit)
            if count is None:
                for s2, w2 in held:
                    ratelimit.refund(ratelimit.WINDOW_TABLE, s2, "win", w2)
                http._lapse_denied = True
                return False
            if count == 1:
                ratelimit.drop_old_windows(subj, window)
            held.append((subj, window))
        http._lapse_inflight = held
        return True


class MetaThrottle(WindowThrottle):
    """Per-IP rate for the account endpoints (sign-up page, key minting, usage); scope `meta`."""

    scope = SCOPE_META

    def subject(self, request):
        return "ip:" + (self.get_ident(request) or "unknown")
