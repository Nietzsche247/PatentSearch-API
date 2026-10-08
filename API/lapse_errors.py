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
import json
import threading
from http import HTTPStatus

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
        _pending.status = 501


class LapseTimeout(APIException):
    """Raised by the SQLite backend when a statement is interrupted by the per-statement timeout or the
    per-request budget (PatentRef gate 5.6). Answers the way upstream reports an Elasticsearch timeout
    (500, `{"error": true}`, `X-Status-Reason-Code: ERR_ES`) with the reason naming the budget, so a
    client can tell a timeout from a server fault. Upstream's handler cannot carry a plain TimeoutError
    (it reads `.info` from it), hence this class."""

    status_code = 500
    default_code = "timeout"

    def __init__(self, reason="Search timed out"):
        super().__init__(detail={"error": True})
        self.reason = reason
        _pending.headers = {"X-Status-Reason": reason, "X-Status-Reason-Code": "ERR_ES"}
        _pending.status = 500


class LapseBadRequest(APIException):
    """A 400 in the upstream shape (`{"error": true}`, X-Status-Reason, X-Status-Reason-Code: ERR_Q) raised from the
    SQLite backend, where upstream's own validation classes are out of reach: q or s on a virtual group such as
    `lapse` (PatentRef 2.9), which is returned with f only."""

    status_code = 400
    default_code = "bad_request"

    def __init__(self, reason):
        super().__init__(detail={"error": True})
        self.reason = reason
        _pending.headers = {"X-Status-Reason": reason, "X-Status-Reason-Code": "ERR_Q"}
        _pending.status = 400


BODY_501 = b'{"error":true}'


class LapseErrorHeadersMiddleware:
    """Puts the headers recorded by `LapseNotImplemented` on the 501 response of the same request, and
    restores the upstream error body: DRF renders an APIException's detail dict with every value coerced
    to a string, which would turn `{"error": true}` into `{"error": "True"}`."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _pending.headers = None
        _pending.status = None
        response = self.get_response(request)
        headers = getattr(_pending, "headers", None)
        status = getattr(_pending, "status", None) or 501
        _pending.headers = None
        _pending.status = None
        if headers and response.status_code == status:
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


PROBLEM_JSON = "application/problem+json"
PROBLEM_TYPE_BASE = "https://patentref.io/docs/errors/"


class ProblemDetailsMiddleware:
    """RFC 9457 problem details by content negotiation (PatentRef gate 2.11; docs/SCAFFOLD.md 5.1).

    When the request's Accept header names `application/problem+json`, every error response (status 400
    and up) is rewritten as a problem details object carrying the same facts the upstream form carries in
    headers: `type` (https://patentref.io/docs/errors/<code>), `title` (the HTTP reason phrase), `status`,
    `detail` (the X-Status-Reason text, or the body's `detail`), `instance` (the request path), `code`
    (X-Status-Reason-Code), `data_version`, and `retry_after` on a 429. The upstream headers stay on the
    response untouched. Without that Accept value nothing changes: the bodies the contract tests check
    (`{"error": true}` byte for byte, DRF's throttle and 404 bodies) are what every other client gets.
    Listed before DataVersionMiddleware and LapseErrorHeadersMiddleware so it sees their headers."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        wants_problem = PROBLEM_JSON in request.META.get("HTTP_ACCEPT", "")  # read before DRF's negotiation edits it
        response = self.get_response(request)
        try:
            if response.status_code < 400 or not wants_problem:
                return response
            if getattr(response, "streaming", False):
                return response
            body = self.problem(request, response)
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            response.content = payload
            response["Content-Type"] = PROBLEM_JSON
            response["Content-Length"] = str(len(payload))
            vary = response.get("Vary", "")
            if "accept" not in vary.lower():
                response["Vary"] = (vary + ", " if vary else "") + "Accept"
        except Exception:  # the negotiation never breaks an error response
            pass
        return response

    @staticmethod
    def problem(request, response):
        status = response.status_code
        try:
            title = HTTPStatus(status).phrase
        except ValueError:
            title = f"HTTP {status}"
        code = response.get("X-Status-Reason-Code") or {429: "THROTTLED", 404: "NOT_FOUND", 403: "ERR_KEY"}.get(status)
        detail = response.get("X-Status-Reason")
        if not detail:
            try:
                parsed = json.loads(response.content.decode("utf-8"))
                if isinstance(parsed, dict):
                    detail = parsed.get("detail") or parsed.get("X-Status-Reason")
            except (ValueError, AttributeError):
                detail = None
        body = {
            "type": PROBLEM_TYPE_BASE + code if code else "about:blank",
            "title": title,
            "status": status,
            "instance": request.path,
        }
        if detail:
            body["detail"] = str(detail)
        if code:
            body["code"] = code
        version = response.get(DATA_VERSION_HEADER)
        if version:
            body["data_version"] = version
        if response.get("Retry-After"):
            try:
                body["retry_after"] = int(response["Retry-After"])
            except ValueError:
                body["retry_after"] = response["Retry-After"]
        return body


class ProblemAwareNegotiation:
    """DRF content negotiation that treats `application/problem+json` in Accept as a request for JSON.

    DRF picks a renderer from the Accept header and answers 406 when none matches; a client that sends
    only `application/problem+json` (the RFC 9457 opt-in) would get a 406 before the view ran. This
    class removes that media type from the header DRF sees (keeping everything else, so the usual
    negotiation still applies) and lets ProblemDetailsMiddleware rewrite the error body afterwards.
    Registered by `lapse_local` as DEFAULT_CONTENT_NEGOTIATION_CLASS."""

    def __init__(self):
        from rest_framework.negotiation import DefaultContentNegotiation

        self._inner = DefaultContentNegotiation()

    def select_parser(self, request, parsers):
        return self._inner.select_parser(request, parsers)

    def select_renderer(self, request, renderers, format_suffix=None):
        accept = request.META.get("HTTP_ACCEPT", "")
        if PROBLEM_JSON in accept:
            kept = [p.strip() for p in accept.split(",") if p.strip() and not p.strip().startswith(PROBLEM_JSON)]
            request.META["HTTP_ACCEPT"] = ", ".join(kept) if kept else "application/json"
        return self._inner.select_renderer(request, renderers, format_suffix)
