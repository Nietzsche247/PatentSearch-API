"""Counts keyed data requests into `MonthlyUsage` (see `lapse_accounts.metering` for the rule)."""
import logging

from lapse_accounts import metering

log = logging.getLogger("lapse_accounts")

API_PREFIX = "/api/v1/"
META_PREFIX = "/api/v1/meta/"
NOT_COUNTED = {403, 429}


class MeteringMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path
        if path.startswith(API_PREFIX) and not path.startswith(META_PREFIX):
            key = request.META.get("HTTP_X_API_KEY")
            if key and "." in key and response.status_code < 500 and response.status_code not in NOT_COUNTED:
                try:
                    metering.record_request(key.split(".", 1)[0])
                except Exception:  # never let the counter break a data response
                    log.exception("usage counter failed")
        return response
