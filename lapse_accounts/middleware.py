"""Settles the monthly reservation made by `MonthlyKeyThrottle` (see `lapse_accounts.metering` for the rule).

The throttle adds one to the month's count when it admits a keyed data request. This middleware takes it
back when the response turns out not to count: a 403 (the key was rejected after admission), a 429 (a
later throttle denied it) or a server error. Every other response, 200 and 400 alike, stays counted.
A request the throttle never reserved (a path under /api/v1/ that resolves to no view, a meta endpoint,
no key) has nothing to settle.
"""
import logging

from lapse_accounts import ratelimit

log = logging.getLogger("lapse_accounts")

NOT_COUNTED = {403, 429}


class MeteringMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        reserved = getattr(request, "_lapse_reserved", None)
        if reserved and (response.status_code >= 500 or response.status_code in NOT_COUNTED):
            try:
                ratelimit.refund(ratelimit.MONTH_TABLE, reserved[0], "month", reserved[1])
            except Exception:  # never let the counter break a data response
                log.exception("usage refund failed")
        return response
