"""Abuse limits on the data endpoints (PatentRef checklist gate 5.6).

Three things live here, all additive (a valid request's body is unchanged):

1. `client_ip(request)`: the address the limits are keyed on. Behind Caddy (`LAPSE_BEHIND_PROXY=1`)
   every request reaches gunicorn from the proxy's address, so the client is the LAST entry of
   `X-Forwarded-For`, which the proxy itself appended or set (`ops/caddy/patentref.caddy` sets it to
   the connecting address, overwriting whatever the client sent). The header is trusted only when the
   connecting address is one of `LAPSE_TRUSTED_PROXIES` (default: loopback and 172.16.0.0/12, the docker
   address pool; on patentref-us1 the Caddy container speaks from its compose network, 172.18.0.x, not
   from the 172.17.0.1 bridge address gunicorn listens on); from any other address the header is ignored
   and `REMOTE_ADDR` is used.

2. `AbuseLimitMiddleware`, in front of the views, on `/api/v1/` data paths (not `/api/v1/meta/`):
   - size caps, answered with the upstream 400 shape (`{"error": true}`, `X-Status-Reason`,
     `X-Status-Reason-Code: ERR_Q`): the query string above `LAPSE_MAX_QUERY_STRING` bytes (16 KB),
     a body above `LAPSE_MAX_BODY` bytes (64 KB, the same figure Caddy's `request_body max_size` and
     Django's `DATA_UPLOAD_MAX_MEMORY_SIZE` carry), a `q` parameter above `LAPSE_MAX_Q_BYTES` (16 KB;
     a POST body's `q` is measured by `API.lapse_cost.check` after parsing);
   - the per-IP limit on requests that carry no usable key: a request with no `X-Api-Key`, or with a
     key this worker has already seen rejected, counts against `nokey` for its IP
     (`LAPSE_NOKEY_PER_MINUTE`, 60 a clock minute) and gets 429 with DRF's body and `Retry-After` once
     the minute is used up; below the limit the view answers 403 as upstream does. A 403 from the view
     (unknown or revoked key) is counted after the fact and the prefix remembered, so a flood of
     invalid keys is throttled the same way. Keyed requests from the same address are not affected:
     a shared NAT with one abuser still serves its honest keys.
   - settlement of the in-flight reservations made by `InflightThrottle` (every response).

3. The DRF throttles `IPCeilingThrottle` and `InflightThrottle` are in `lapse_accounts.throttling`;
   they use the same atomic counter rows (`lapse_accounts.ratelimit`).

The limits and the reasons for each number are in the patentref repo (`decisions.md`, audit
`ops/audits/2026-10-06_gate-5.6_abuse-limits_patentref-us1.txt`).
"""
import ipaddress
import json
import threading
import time

from django.conf import settings
from django.http import HttpResponse

from lapse_accounts import ratelimit

SCOPE_NOKEY = "nokey"
DATA_PREFIX = "/api/v1/"
META_PREFIX = "/api/v1/meta/"

timer = time.time  # tests pin this

_networks = None
_rejected_lock = threading.Lock()
_rejected = {}  # key prefix -> time it was last seen rejected (per worker process)
_REJECTED_TTL = 600
_denied_lock = threading.Lock()
_denied = {}  # (subject, window) -> True: windows already known to be used up (per worker; saves the write)


def setting(name, default):
    return getattr(settings, name, default)


def _trusted_networks():
    global _networks
    if _networks is None:
        nets = []
        for item in str(setting("LAPSE_TRUSTED_PROXIES", "127.0.0.1,::1,172.16.0.0/12")).split(","):
            item = item.strip()
            if not item:
                continue
            try:
                nets.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue
        _networks = nets
    return _networks


def reset_caches():
    """Tests: forget trusted networks, rejected prefixes and known-denied windows."""
    global _networks
    _networks = None
    with _rejected_lock:
        _rejected.clear()
    with _denied_lock:
        _denied.clear()


def _is_trusted(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _trusted_networks())


def client_ip(request):
    """The client address the limits are keyed on (see the module docstring)."""
    remote = request.META.get("REMOTE_ADDR", "") or "unknown"
    if setting("LAPSE_BEHIND_PROXY", False) and _is_trusted(remote):
        xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            hops = [h.strip() for h in xff.split(",") if h.strip()]
            if hops:
                return hops[-1]
    return remote


def is_data_path(path):
    return path.startswith(DATA_PREFIX) and not path.startswith(META_PREFIX)


def throttled_response(wait):
    """DRF's 429: the same body and header upstream's throttle produces."""
    wait = max(1, int(wait))
    body = json.dumps({"detail": f"Request was throttled. Expected available in {wait} seconds."}).encode()
    resp = HttpResponse(body, status=429, content_type="application/json")
    resp["Retry-After"] = str(wait)
    return resp


def bad_request(reason):
    """Upstream's 400 shape (API/exceptions.py custom_exception_handler)."""
    resp = HttpResponse(b'{"error":true}', status=400, content_type="application/json")
    resp["X-Status-Reason"] = reason
    resp["X-Status-Reason-Code"] = "ERR_Q"
    return resp


def remember_rejected(prefix):
    if not prefix:
        return
    with _rejected_lock:
        if len(_rejected) > 10000:
            _rejected.clear()
        _rejected[prefix] = timer()


def recently_rejected(prefix):
    if not prefix:
        return False
    with _rejected_lock:
        seen = _rejected.get(prefix)
        if seen is None:
            return False
        if seen + _REJECTED_TTL < timer():
            _rejected.pop(prefix, None)
            return False
        return True


def _known_denied(subject, window):
    with _denied_lock:
        return (subject, window) in _denied


def _mark_denied(subject, window):
    with _denied_lock:
        if len(_denied) > 10000:
            _denied.clear()
        _denied[(subject, window)] = True


def count_nokey(ip, now=None):
    """Add one to the IP's keyless window. Returns (allowed, wait seconds)."""
    limit = int(setting("LAPSE_NOKEY_PER_MINUTE", 60))
    subject = "ip:" + ip
    window, wait = ratelimit.minute_window(SCOPE_NOKEY, 60, now if now is not None else timer())
    if _known_denied(subject, window):
        return False, wait
    count = ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", window, limit)
    if count is None:
        _mark_denied(subject, window)
        return False, wait
    if count == 1:
        ratelimit.drop_old_windows(subject, window)
    return True, wait


def drain(request, limit):
    """Read and discard up to `limit` bytes of the request body from the WSGI input; returns the count.

    Why read a body we are about to refuse: Caddy in front carries `request_body max_size` at the same
    figure, and its reader trips the limit while the proxy is still copying the body upstream. If the app
    has already answered by then (an early 400 or 403), Go's net/http sets a response header from the
    body-copy goroutine while the proxy goroutine is writing the response headers, and the Caddy process
    dies with "fatal error: concurrent map writes" (seen five times in the gate 5.6 hostile run). Reading
    `max_body + 1` bytes first means the app cannot answer before Caddy's own limit has fired and closed
    the upstream request, so the race never happens; the read returns short in that case. Without Caddy
    (a direct client) the read costs the cap's worth of bytes and gunicorn discards the rest."""
    stream = request.META.get("wsgi.input")
    if stream is None:
        return 0
    got = 0
    try:
        while got < limit:
            chunk = stream.read(min(65536, limit - got))
            if not chunk:
                break
            got += len(chunk)
    except Exception:  # a closed or reset upstream connection: nothing more to read
        pass
    return got


class AbuseLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_data_path(request.path):
            return self.get_response(request)
        early = self._size_caps(request) or self._nokey(request)
        if early is not None:
            return early
        response = self.get_response(request)
        inflight = getattr(request, "_lapse_inflight", None)
        if inflight:
            for subject, window in inflight:
                try:
                    ratelimit.refund(ratelimit.WINDOW_TABLE, subject, "win", window)
                except Exception:  # never let the counter break a data response
                    pass
        if response.status_code == 403 and getattr(request, "_lapse_nokey_counted", False) is False:
            key = request.META.get("HTTP_X_API_KEY")
            if key:
                remember_rejected(key.split(".", 1)[0])
                try:
                    count_nokey(client_ip(request))
                except Exception:
                    pass
        return response

    @staticmethod
    def _size_caps(request):
        max_qs = int(setting("LAPSE_MAX_QUERY_STRING", 16384))
        qs = request.META.get("QUERY_STRING", "") or ""
        if len(qs) > max_qs:
            return bad_request(f"Query string too long: {len(qs)} bytes, the limit is {max_qs}")
        max_body = int(setting("LAPSE_MAX_BODY", 65536))
        try:
            length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        if length > max_body:
            drain(request, max_body + 1)
            return bad_request(f"Request body too large: {length} bytes, the limit is {max_body}")
        if length == 0 and "chunked" in (request.META.get("HTTP_TRANSFER_ENCODING") or "").lower():
            # no Content-Length: Django reads nothing from such a body, so take it off the wire here up to
            # the cap (gunicorn de-chunks it); over the cap it is refused like a declared one
            if drain(request, max_body + 1) > max_body:
                return bad_request(f"Request body too large: more than {max_body} bytes")
        max_q = int(setting("LAPSE_MAX_Q_BYTES", 16384))
        q = request.GET.get("q")
        if q is not None and len(q.encode("utf-8", "replace")) > max_q:
            return bad_request(f"'q' parameter too large: the limit is {max_q} bytes")
        return None

    @staticmethod
    def _nokey(request):
        key = request.META.get("HTTP_X_API_KEY")
        if key and "." in key and not recently_rejected(key.split(".", 1)[0]):
            return None
        request._lapse_nokey_counted = True
        try:
            allowed, wait = count_nokey(client_ip(request))
        except Exception:
            return None  # a counter failure never blocks the upstream 403
        if not allowed:
            return throttled_response(wait)
        return None
