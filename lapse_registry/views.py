"""Registry API routes (PatentRef checklist phase 10; scaffold 5.3). Served by its own gunicorn (patentref-registry-api,
settings pvapi.settings.registry) behind the same Caddy and domain as the API; the API process does not load this app.

  POST /registry/disclosures                 Bearer: publish a record version (JSON or multipart with `file`)
  POST /registry/disclosures/check           Bearer: the preflight: normalized fields, PII warnings, the consent text
  GET  /registry/disclosures                 Bearer: the caller's records
  GET  /registry/disclosures/<id>            the record (the owner's view with a Bearer token of its owner)
  GET  /registry/disclosures/<id>/proof      the proof object (public)
  POST /registry/disclosures/<id>/receipt    Bearer, hook for 10.3: 501 until receipts are offered
  POST /registry/disclosures/<id>/seal       hook for 10.4: 501 (a sealed record is published with visibility "sealed")
  GET  /registry/disclosures/<id>/sealed     Bearer, hook for 10.4: the owner's sealed bundle text, when sealing is on
  GET  /registry/search?q=                   public records matching q (basic; 10.5 adds the index and MCP tool)
  GET  /registry/health                      database and keys state
  GET  /r/<id>.json                          the record (public), the machine twin of the page /r/<id>
  GET  /r/<id>/file                          the record's file, always as a download

The signed-in user is the `sub` of a Supabase access token verified by lapse_accounts.supabase_jwt (ES256 against the
project's JWKS, email confirmed). Errors: {"error": true, "code", "reason", ...} with X-Status-Reason and
X-Status-Reason-Code, like the account routes. User text comes back inside `user_content`, as entered.
"""
import json
import logging
import urllib.parse

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from lapse_accounts import abuse, ratelimit, supabase_jwt
from lapse_registry import core, db

log = logging.getLogger("lapse_registry")

SAFE_DOWNLOAD_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}
TRUE = {"1", "true", "yes", "on"}


class HttpError(Exception):
    def __init__(self, status, code, reason, extra=None, headers=None):
        super().__init__(reason)
        self.status, self.code, self.reason, self.extra, self.headers = status, code, reason, extra or {}, headers or {}


def _json(data, status=200, private=False):
    resp = JsonResponse(data, status=status, json_dumps_params={"ensure_ascii": False, "separators": (",", ":")})
    resp["Cache-Control"] = "no-store" if private else "public, max-age=30"
    resp["X-Content-Type-Options"] = "nosniff"
    return resp


def _error_response(e):
    body = {"error": True, "code": e.code, "reason": e.reason}
    body.update(e.extra)
    resp = _json(body, e.status, private=True)
    resp["X-Status-Reason"] = e.reason.encode("ascii", "replace").decode("ascii")
    resp["X-Status-Reason-Code"] = e.code
    for k, v in e.headers.items():
        resp[k] = v
    return resp


def _limit(request, scope, per_minute):
    """Per-address limit in a clock minute, on the app DB's exact counters (lapse_accounts.ratelimit)."""
    ip = abuse.client_ip(request)
    subject = "ip:" + ip
    window, wait = ratelimit.minute_window(f"registry_{scope}", 60)
    n = ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", window, per_minute)
    if n is None:
        raise HttpError(429, "ERR_RATE", f"Too many registry requests from this address; try again in {wait} seconds.",
                        headers={"Retry-After": str(wait)})
    if n == 1:
        ratelimit.drop_old_windows(subject, window)


def _user(request, required=True):
    token = supabase_jwt.bearer_token(request)
    if not token:
        if required:
            raise HttpError(401, "ERR_AUTH", "Authorization: Bearer <Supabase access token> required")
        return None
    try:
        claims = supabase_jwt.verify(token)
    except supabase_jwt.TokenError as e:
        raise HttpError(401, "ERR_AUTH", e.reason)
    return claims["sub"]


def _base():
    return getattr(settings, "REGISTRY_PUBLIC_BASE", "https://patentref.io").rstrip("/")


def _guard_body(request):
    """Refuse a body above REGISTRY_MAX_BODY before reading it into memory (reading up to the cap first; see abuse.drain)."""
    limit = int(getattr(settings, "REGISTRY_MAX_BODY", 25 * 1024 * 1024))
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length > limit:
        abuse.drain(request, limit + 1)
        raise HttpError(413, "ERR_SIZE", f"The request body is over {limit} bytes.")


def _payload(request):
    """The submission from a JSON body or a multipart form, and the file as (name, media_type, bytes) or None."""
    ctype = (request.content_type or "").lower()
    file = None
    if ctype == "application/json":
        try:
            payload = json.loads(request.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise HttpError(400, "ERR_INPUT", "The body is not valid JSON.")
        if not isinstance(payload, dict):
            raise HttpError(400, "ERR_INPUT", "The body must be a JSON object.")
        return payload, None
    if ctype in ("multipart/form-data", "application/x-www-form-urlencoded"):
        p = request.POST
        payload = {k: p.get(k) for k in ("title", "abstract", "body_text", "visibility", "prev_version_id", "inventor_consent")}
        payload["cpc_guess"] = p.get("cpc_guess") or ""
        payload["inventors_as_entered"] = p.get("inventors_as_entered") or ""
        payload["pii_acknowledged"] = (p.get("pii_acknowledged") or "").lower() in TRUE
        up = request.FILES.get("file")
        if up is not None:
            max_file = int(getattr(settings, "REGISTRY_FILE_MAX", 20 * 1024 * 1024))
            if up.size > max_file:
                raise HttpError(413, "ERR_SIZE", f"The file is over {max_file} bytes; larger files arrive with object storage.")
            file = (up.name, up.content_type, up.read())
        return payload, file
    raise HttpError(415, "ERR_INPUT", "Send application/json or multipart/form-data.")


def _call(fn):
    """Run a view body, mapping every known failure to the error shape."""
    try:
        return fn()
    except HttpError as e:
        return _error_response(e)
    except db.Unavailable:
        return _error_response(HttpError(503, "ERR_UNAVAILABLE", "The registry database is not reachable; try again shortly.",
                                         headers={"Retry-After": "30"}))
    except Exception as e:  # noqa: BLE001
        service = core.module("service")
        textnorm = core.module("textnorm")
        if isinstance(e, service.ServiceError):
            return _error_response(HttpError(e.status, e.code, e.reason, e.extra))
        if isinstance(e, textnorm.InputError):
            return _error_response(HttpError(400, "ERR_INPUT", f"{e.field}: {e.reason}", {"field": e.field}))
        try:
            import psycopg
            if isinstance(e, psycopg.OperationalError):
                db.reset()
                return _error_response(HttpError(503, "ERR_UNAVAILABLE", "The registry database is not reachable; try again shortly.",
                                                 headers={"Retry-After": "30"}))
        except ImportError:
            pass
        log.exception("registry request failed")
        db.reset()
        return _error_response(HttpError(500, "ERR_SERVER", "The registry could not complete the request."))


def _write_limit(request):
    _limit(request, "write", int(getattr(settings, "REGISTRY_WRITE_PER_MINUTE", 20)))


def _read_limit(request):
    _limit(request, "read", int(getattr(settings, "REGISTRY_READ_PER_MINUTE", 120)))


# ------------------------------------------------------------------ routes

@csrf_exempt
@require_http_methods(["GET", "POST"])
def disclosures(request):
    def run():
        if request.method == "POST":
            _guard_body(request)
            _write_limit(request)
            user = _user(request)
            payload, file = _payload(request)
            rec = core.module("service").publish(
                db.connection(), user_id=user, payload=payload, file=file, base_url=_base(),
                sealed_enabled=bool(getattr(settings, "REGISTRY_SEALED_ENABLED", False)), keys=_keys_or_none())
            resp = _json(rec, 201, private=True)
            resp["Location"] = rec["permalink"]
            return resp
        _read_limit(request)
        user = _user(request)
        return _json(core.module("service").list_mine(db.connection(), user, _base()), private=True)
    return _call(run)


@csrf_exempt
@require_POST
def check(request):
    def run():
        _guard_body(request)
        _write_limit(request)
        _user(request)
        payload, file = _payload(request)
        return _json(core.module("service").check(payload, file), private=True)
    return _call(run)


@require_GET
def record(request, rid):
    def run():
        _read_limit(request)
        user = _user(request, required=False)
        rec = core.module("service").get_record(db.connection(), rid, _base(), user_id=user)
        if rec is None:
            raise HttpError(404, "ERR_NOT_FOUND", "No visible record has this id.")
        resp = _json(rec, private=bool(user))
        if rec["noindex"]:
            resp["X-Robots-Tag"] = "noindex"
        return resp
    return _call(run)


@require_GET
def proof(request, rid):
    def run():
        _read_limit(request)
        out = core.module("service").get_proof(db.connection(), rid)
        if out is None:
            raise HttpError(404, "ERR_NOT_FOUND", "No visible record has this id.")
        return _json(out)
    return _call(run)


@require_GET
def record_file(request, rid):
    def run():
        _read_limit(request)
        user = _user(request, required=False)
        got = core.module("service").get_file(db.connection(), rid, user_id=user)
        if got is None:
            raise HttpError(404, "ERR_NOT_FOUND", "This record has no file, or no visible record has this id.")
        name, media_type, data = got
        ctype = media_type if media_type in SAFE_DOWNLOAD_TYPES else "application/octet-stream"
        if ctype == "text/plain":
            ctype += "; charset=utf-8"
        resp = HttpResponse(data, content_type=ctype)
        ascii_name = "".join(c if 32 < ord(c) < 127 and c not in '"\\;' else "_" for c in name) or "file"
        resp["Content-Disposition"] = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{urllib.parse.quote(name)}"
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
        resp["Cache-Control"] = "public, max-age=86400, immutable"
        resp["X-Robots-Tag"] = "noindex"
        return resp
    return _call(run)


@require_GET
def search(request):
    def run():
        _read_limit(request)
        return _json(core.module("service").search(db.connection(), request.GET.get("q", ""), _base()))
    return _call(run)


@csrf_exempt
@require_POST
def receipt(request, rid):
    """Hook for checklist 10.3: answers 501 until receipts are offered (pricing 10.6) and registry_crypto is installed."""
    def run():
        _write_limit(request)
        user = _user(request)
        if not getattr(settings, "REGISTRY_RECEIPTS_ENABLED", False):
            raise HttpError(501, "ERR_NOT_YET", "Corpus receipts are not offered yet (checklist 10.3).")
        keys = _keys()
        from lapse_registry import receipt_search
        out = core.module("service").create_receipt(db.connection(), user_id=user, record_id=rid, base_url=_base(),
                                                     similar=receipt_search.similar, keys=keys)
        return _json(out, 201, private=True)
    return _call(run)


@csrf_exempt
@require_POST
def seal(request, rid):
    """Hook for checklist 10.4: a record is sealed when it is published (visibility "sealed"), never afterwards."""
    return _error_response(HttpError(501, "ERR_NOT_YET", "Sealed copies are published with visibility sealed and are not "
                                     "offered yet (checklist 10.4)."))


@require_GET
def sealed_release(request, rid):
    """Hook for checklist 10.4: the owner's sealed bundle text, checked against the public hash."""
    def run():
        _read_limit(request)
        user = _user(request)
        if not getattr(settings, "REGISTRY_SEALED_ENABLED", False):
            raise HttpError(501, "ERR_NOT_YET", "Sealed copies are not offered yet (checklist 10.4).")
        return _json(core.module("service").release_sealed(db.connection(), user_id=user, record_id=rid, keys=_keys()),
                     private=True)
    return _call(run)


@require_GET
def health(request):
    ok = db.ping()
    try:
        core.module("keys").load()
        keys_state = "present"
    except Exception:
        keys_state = "absent"
    body = {"ok": ok, "database": "ok" if ok else "unreachable", "keys": keys_state,
            "receipts_enabled": bool(getattr(settings, "REGISTRY_RECEIPTS_ENABLED", False)),
            "sealed_enabled": bool(getattr(settings, "REGISTRY_SEALED_ENABLED", False))}
    resp = _json(body, 200 if ok else 503, private=True)
    return resp


def _keys():
    k = core.module("keys")
    try:
        return k.load()
    except k.KeysUnavailable as e:
        raise HttpError(501, "ERR_NOT_YET", f"{e} (checklist 5.14).")


def _keys_or_none():
    try:
        return core.module("keys").load()
    except Exception:
        return None


def not_found(request, exception=None):
    return _error_response(HttpError(404, "ERR_NOT_FOUND", "No registry route here."))
