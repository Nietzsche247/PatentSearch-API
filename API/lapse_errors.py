"""Lapse-only error types and the middleware that carries their headers.

Upstream `API/exceptions.py` is untouched. Its final branch hands any exception it does not know to
Django REST framework's default handler, which turns an `APIException` into a response with the
exception's `status_code` and `detail` but cannot attach custom headers. The two pieces here close
that gap for the one case the SQLite backend adds: an endpoint whose data set is not in the snapshot
(checklist 1.5 defers pre-grant publications; other views arrive with their Phase 1 loads).

Documented 501 contract (CHANGES_LAPSE.md, "Deferred endpoints"):
  status 501, body {"error": true},
  X-Status-Reason: Endpoint not implemented: the '<index>' data set is not in this snapshot
  X-Status-Reason-Code: ERR_NOT_IMPLEMENTED
"""
import threading

from rest_framework.exceptions import APIException

DEFERRED_CODE = "ERR_NOT_IMPLEMENTED"
DEFERRED_INDICES = {"publications": "pre-grant publications are deferred (PatentRef checklist 1.5)"}

_pending = threading.local()


def deferred_reason(index):
    reason = f"Endpoint not implemented: the '{index}' data set is not in this snapshot"
    note = DEFERRED_INDICES.get(index)
    return f"{reason}; {note}" if note else reason


class LapseNotImplemented(APIException):
    """Raised by the SQLite backend when a view's index is absent from `_lapse_indices`."""

    status_code = 501
    default_code = "not_implemented"

    def __init__(self, index):
        super().__init__(detail={"error": True})
        self.index = index
        self.reason = deferred_reason(index)
        _pending.headers = {"X-Status-Reason": self.reason, "X-Status-Reason-Code": DEFERRED_CODE}


class LapseErrorHeadersMiddleware:
    """Copies the headers recorded by `LapseNotImplemented` onto the 501 response of the same request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _pending.headers = None
        response = self.get_response(request)
        headers = getattr(_pending, "headers", None)
        _pending.headers = None
        if headers and response.status_code == 501:
            for name, value in headers.items():
                if not response.has_header(name):
                    response[name] = value
        return response
