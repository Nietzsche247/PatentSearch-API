"""Per-minute and monthly throttles for keys minted from an account.

`PlanKeyThrottle` is upstream's `APIKeyThrottle` (same ident, same cache key, same 429) with the
rate chosen per key: a key minted from a Supabase account gets its plan's per-minute allowance
(`LAPSE_FREE_MINUTE_LIMIT`, 45/m), a key without an account (the operator's own) keeps the `key`
rate from settings (`LAPSE_THROTTLE_RATE`, upstream's 45/m by default).

`MonthlyKeyThrottle` runs after it. When the month's count has reached the plan's allowance it
denies the request and DRF answers the same way it does for the per-minute rule: status 429,
`Retry-After` (seconds until the month resets, UTC) and the body
`{"detail": "Request was throttled. Expected available in N seconds."}`.
"""
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

from API.throttler import APIKeyThrottle
from lapse_accounts import metering


class PlanKeyThrottle(APIKeyThrottle):
    def allow_request(self, request, view):
        key = request.META.get("HTTP_X_API_KEY")
        if key and "." in key:
            info = metering.subject_for_prefix(key.split(".", 1)[0])
            if info["account"]:
                self.num_requests, self.duration = metering.plan_limits(info["plan"])[1], 60
        return super().allow_request(request, view)


class MonthlyKeyThrottle(BaseThrottle):
    def __init__(self):
        self._wait = None

    def allow_request(self, request, view):
        key = request.META.get("HTTP_X_API_KEY")
        if not key or "." not in key:
            return True  # no key: the permission class already answered 403
        if metering.over_cap(key.split(".", 1)[0]):
            self._wait = metering.seconds_to_reset()
            return False
        return True

    def wait(self):
        return self._wait


class MetaThrottle(SimpleRateThrottle):
    """Per-IP rate for the account endpoints (sign-up page, key minting, usage); scope `meta`."""

    scope = "meta"

    def get_cache_key(self, request, view):
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}
