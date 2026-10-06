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


BODY_501 = b'{"error":true}'


class LapseErrorHeadersMiddleware:
    """Puts the headers recorded by `LapseNotImplemented` on the 501 response of the same request, and
    restores the upstream error body: DRF renders an APIException's detail dict with every value coerced
    to a string, which would turn `{"error": true}` into `{"error": "True"}`."""

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
            if getattr(response, "content", None) != BODY_501:
                response.content = BODY_501
                response["Content-Length"] = str(len(BODY_501))
        return response


DATA_VERSION_HEADER = "X-Data-Version"


class DataVersionMiddleware:
    """Puts `X-Data-Version` on every response (PatentRef gate 4.3, CHANGES_LAPSE.md "X-Data-Version").

    The value is the data_version of the search database connection the request's thread holds after
    the view ran: the same connection produced the body, so header and body never name different
    files. A response whose view never opened the search database (403 without a key, the account
    endpoints) gets the version of the file the configured path resolves to right now, read through
    the same thread cache. The header is additive: upstream bodies and other headers are unchanged."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not response.has_header(DATA_VERSION_HEADER):
            version = self._version()
            if version:
                response[DATA_VERSION_HEADER] = version
        return response

    @staticmethod
    def _version():
        try:
            from django.conf import settings

            if getattr(settings, "LAPSE_BACKEND", "elasticsearch") != "sqlite":
                return None
            from API.search_sqlite import LapseSQLiteSearch, current_data_version

            version = current_data_version()
            if version is None:
                version = LapseSQLiteSearch.from_django_settings().data_version()
            return version
        except Exception:  # the header never breaks a response
            return None
