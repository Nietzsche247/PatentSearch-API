"""The Free-tier monthly cap as a second DRF throttle, after the upstream per-minute one.

Listed after `API.throttler.APIKeyThrottle` in `DEFAULT_THROTTLE_CLASSES`, so the per-minute rule
and its 429 are exactly upstream's. When the month's count has reached the plan's allowance this
throttle denies the request and DRF answers the same way it does for the per-minute rule: status 429,
`Retry-After` (seconds until the month resets, UTC) and the body
`{"detail": "Request was throttled. Expected available in N seconds."}`.
"""
from rest_framework.throttling import BaseThrottle, SimpleRateThrottle

from lapse_accounts import metering


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
