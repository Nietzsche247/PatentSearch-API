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
  GET  /registry/disclosures/terms           Bearer: whether the account accepted the current terms (5.11), and the links
  GET  /registry/disclosures/account/export  Bearer: everything the registry holds for the account (5.12)
  POST /registry/disclosures/account/erase   Bearer, {"confirm": "ERASE"}: the account's records become tombstones (5.12)
  POST /registry/disclosures/<id>/monitor    Bearer, {"on": true|false}: monitoring of the record (10.7), when offered
  POST /registry/disclosures/<id>/contribution-log   Bearer: the owner's contribution log (10.4), when offered
  GET  /registry/disclosures/<id>/contribution-log.zip   the log with its C2PA manifest (10.4)
  POST /registry/disclosures/<id>/claim      Bearer: an ownership claim on a record (10.9), when offered
  POST /registry/disclosures/notices         a copyright notice (10.9), when offered; agents also take notices by email
  GET  /registry/disclosures/notices/<case>  the case's kind, state and dates
  POST /registry/disclosures/notices/<case>/counter  Bearer, the record's owner: the counter-notice (10.9)
  GET  /registry/search?q=                   public records matching q (the MCP tool search_disclosures calls it, 10.5)
  GET  /registry/sitemap.xml                 the records a search engine may list (10.5, 10.10)
  GET  /registry/health                      database, keys and read-cache state
  GET  /registry/.well-known/jwks.json       the receipt signing keys with their validity windows (checklist 5.14)
  GET  /r/<id>.json                          the record (public), the machine twin of the page /r/<id>
  GET  /r/<id>/file                          the record's file, always as a download

The signed-in user is the `sub` of a Supabase access token verified by lapse_accounts.supabase_jwt (ES256 against the
project's JWKS, email confirmed). Errors: {"error": true, "code", "reason", ...} with X-Status-Reason and
X-Status-Reason-Code, like the account routes. User text comes back inside `user_content`, as entered.

During a registry database (Supabase) outage (checklist 5.15) the public reads (a record, its proof and file, search,
the sitemap) are answered from the local read cache (readcache.py) with X-Registry-Source: cache and an Age header;
what the cache does not hold, and every write, answers 503 with Retry-After.
"""
import json
import logging
import urllib.parse

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from lapse_accounts import abuse, ratelimit, supabase_jwt
from lapse_registry import core, db, readcache

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


def _claims(request, required=True):
    token = supabase_jwt.bearer_token(request)
    if not token:
        if required:
            raise HttpError(401, "ERR_AUTH", "Authorization: Bearer <Supabase access token> required")
        return None
    try:
        return supabase_jwt.verify(token)
    except supabase_jwt.TokenError as e:
        raise HttpError(401, "ERR_AUTH", e.reason)


def _user(request, required=True):
    claims = _claims(request, required)
    return claims["sub"] if claims else None


def _publisher(request):
    """The signed-in user who may publish: not on a disposable email domain (checklist 10.10, the list of gate 5.10).
    Returns the verified claims (their user metadata may carry the terms version accepted at sign-up: 5.11)."""
    from lapse_accounts import views as account_views

    claims = _claims(request)
    domain = account_views.email_domain(claims.get("email"))
    if account_views.is_disposable(domain):
        raise HttpError(403, "ERR_ACCOUNT", f"Records from disposable email domains are not accepted ({domain}).")
    return claims


def _publish_day_limit(request):
    """Records published per address per UTC day, all accounts together (checklist 10.10: many accounts, one sender)."""
    limit = int(getattr(settings, "REGISTRY_PUBLISH_PER_ADDRESS_DAY", 30))
    subject = "ip:" + abuse.client_ip(request)
    window, wait = ratelimit.minute_window("registry_publish_day", 86400)
    if ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", window, limit) is None:
        raise HttpError(429, "ERR_CAP", f"This address has published {limit} records today (UTC day); try again tomorrow.",
                        headers={"Retry-After": str(wait)})
    return subject, window


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
        payload = {k: p.get(k) for k in ("title", "abstract", "body_text", "visibility", "prev_version_id", "inventor_consent",
                                         "terms_version")}
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
                db.failed(e.__class__.__name__)
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


def _db_down(e):
    """Is this failure the registry database being unreachable (the outage case of checklist 5.15)?"""
    if isinstance(e, db.Unavailable):
        return True
    try:
        import psycopg
    except ImportError:
        return False
    if isinstance(e, psycopg.OperationalError):
        db.failed(e.__class__.__name__)
        return True
    return False


OUTAGE = "The registry database is not reachable and this answer is not in the local copy; try again shortly."


def _cached(body, ctype, age, status=200):
    resp = HttpResponse(body, content_type=ctype, status=status)
    resp["Cache-Control"] = "public, max-age=30"
    resp["X-Content-Type-Options"] = "nosniff"
    resp["X-Registry-Source"] = "cache"
    resp["Age"] = str(age)
    return resp


def _live(fn, fallback):
    """fn() against the database; on an outage, fallback() (an HttpResponse from the read cache, or None for 503)."""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        if not _db_down(e):
            raise
        log.warning("registry database unreachable; answering from the read cache")
        resp = fallback()
        if resp is None:
            raise HttpError(503, "ERR_UNAVAILABLE", OUTAGE, headers={"Retry-After": "30"})
        return resp


# ------------------------------------------------------------------ routes

@csrf_exempt
@require_http_methods(["GET", "POST"])
def disclosures(request):
    def run():
        if request.method == "POST":
            _guard_body(request)
            _write_limit(request)
            claims = _publisher(request)
            user = claims["sub"]
            payload, file = _payload(request)
            subject, window = _publish_day_limit(request)
            try:
                rec = core.module("service").publish(
                    db.connection(), user_id=user, payload=payload, file=file, base_url=_base(),
                    sealed_enabled=bool(getattr(settings, "REGISTRY_SEALED_ENABLED", False)), keys=_keys_or_none(),
                    claims=claims, contact_email=claims.get("email"))
            except Exception:
                ratelimit.refund(ratelimit.WINDOW_TABLE, subject, "win", window)   # only stored records count
                raise
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

        def live():
            rec = core.module("service").get_record(db.connection(), rid, _base(), user_id=user)
            if rec is None:
                if not user:
                    readcache.drop_record(rid)
                raise HttpError(404, "ERR_NOT_FOUND", "No visible record has this id.")
            if rec.get("hidden") and "owner" not in rec:
                # the content is hidden (checklist 10.9): the hash and the proof stay public; never from the cache
                readcache.drop_record(rid)
                resp = _json(rec, 451 if rec.get("hidden_reason") == "dmca" else 410)
                resp["X-Robots-Tag"] = "noindex"
                return resp
            if not user:
                readcache.put_record(rec)
            resp = _json(rec, private=bool(user))
            if rec["noindex"]:
                resp["X-Robots-Tag"] = "noindex"
            return resp

        def cached():
            got = readcache.record(rid)
            if not got:
                return None
            rec, age = got
            resp = _cached(json.dumps(rec, ensure_ascii=False, separators=(",", ":")), "application/json", age)
            if rec.get("noindex"):
                resp["X-Robots-Tag"] = "noindex"
            return resp
        return _live(live, cached)
    return _call(run)


@require_GET
def proof(request, rid):
    def run():
        _read_limit(request)

        def live():
            out = core.module("service").get_proof(db.connection(), rid)
            if out is None:
                raise HttpError(404, "ERR_NOT_FOUND", "No visible record has this id.")
            readcache.put(f"proof:{rid}", json.dumps(out, ensure_ascii=False, separators=(",", ":")), "application/json")
            return _json(out)

        def cached():
            got = readcache.get(f"proof:{rid}")
            return _cached(got[0], got[1], got[2]) if got else None
        return _live(live, cached)
    return _call(run)


@require_GET
def record_file(request, rid):
    def run():
        _read_limit(request)
        user = _user(request, required=False)
        source = {}

        def live():
            got = core.module("service").get_file(db.connection(), rid, user_id=user)
            if got is not None and not user:
                readcache.put_file(rid, *got)
            return got

        def cached():
            got = readcache.get_file(rid)
            if got is None:
                return None
            source["cache"] = True
            return got
        got = _live(live, cached)
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
        if source.get("cache"):
            resp["X-Registry-Source"] = "cache"
        return resp
    return _call(run)


@require_GET
def search(request):
    def run():
        _read_limit(request)
        q = request.GET.get("q", "")

        def cached():
            recs = readcache.search(q, _base())
            body = {"error": False, "q": (q or "").strip()[:200], "count": len(recs), "records": recs}
            return _cached(json.dumps(body, ensure_ascii=False, separators=(",", ":")), "application/json", 0)
        return _live(lambda: _json(core.module("service").search(db.connection(), q, _base())), cached)
    return _call(run)


@require_GET
def sitemap(request):
    def run():
        _read_limit(request)

        def live():
            xml = core.module("service").sitemap_xml(db.connection(), _base())
            readcache.put("sitemap", xml, "application/xml; charset=utf-8")
            resp = HttpResponse(xml, content_type="application/xml; charset=utf-8")
            resp["Cache-Control"] = "public, max-age=3600"
            resp["X-Content-Type-Options"] = "nosniff"
            return resp

        def cached():
            got = readcache.get("sitemap")
            return _cached(got[0], got[1], got[2]) if got else None
        return _live(live, cached)
    return _call(run)


@csrf_exempt
@require_POST
def receipt(request, rid):
    """Hook for checklist 10.3: answers 501 until receipts are offered (pricing 10.6) and registry_crypto is installed."""
    def run():
        _write_limit(request)
        user = _user(request)
        if not getattr(settings, "REGISTRY_RECEIPTS_ENABLED", False):
            raise HttpError(501, "ERR_NOT_YET", "Corpus receipts are not offered yet.")
        keys = _keys()
        from lapse_registry import receipt_search
        payment = _paid(request, user, rid, "receipt")
        out = core.module("service").create_receipt(db.connection(), user_id=user, record_id=rid, base_url=_base(),
                                                     similar=receipt_search.similar, keys=keys, payment=payment,
                                                     refunds=core.module("payments").provider())
        return _json(out, 201, private=True)
    return _call(run)


def _paid(request, user, rid, kind):
    """The confirmed payment a priced request names (payment_ref in the JSON body), or None while prices are off
    (REGISTRY_PAYMENTS_REQUIRED, checklist 10.6: the provider is not wired yet)."""
    ref = None
    if (request.content_type or "").lower() == "application/json" and request.body:
        try:
            ref = (json.loads(request.body.decode("utf-8")) or {}).get("payment_ref")
        except (ValueError, UnicodeDecodeError, AttributeError):
            ref = None
    if not ref:
        if getattr(settings, "REGISTRY_PAYMENTS_REQUIRED", False):
            raise HttpError(402, "ERR_PAYMENT", "This service is paid; pay for it first.")
        return None
    service = core.module("service")
    with service.transaction(db.connection(), "registry_member", user) as cur:
        cur.execute("select * from registry.payments where provider_ref = %s and record_id = %s and kind = %s "
                    "and status = 'paid' and owner_user_id = registry.uid()", (str(ref), rid, kind))
        row = cur.fetchone()
    if not row:
        raise HttpError(402, "ERR_PAYMENT", "No confirmed payment of this account has that reference for this record.")
    return row


def _body_json(request):
    if (request.content_type or "").lower() != "application/json":
        raise HttpError(415, "ERR_INPUT", "Send application/json.")
    try:
        out = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HttpError(400, "ERR_INPUT", "The body is not valid JSON.")
    if not isinstance(out, dict):
        raise HttpError(400, "ERR_INPUT", "The body must be a JSON object.")
    return out


def _offered(setting, what):
    if not getattr(settings, setting, False):
        raise HttpError(501, "ERR_NOT_YET", f"{what} is not offered yet.")


@require_GET
def terms_status(request):
    """Checklist 5.11: has this account accepted the current terms (at sign-up or at a publish), and what to accept."""
    def run():
        _read_limit(request)
        claims = _claims(request)
        return _json(core.module("service").terms_status(db.connection(), claims["sub"], claims, _base()), private=True)
    return _call(run)


@require_GET
def account_export(request):
    """Checklist 5.12: everything the registry holds for the signed-in account, as one JSON download."""
    def run():
        _read_limit(request)
        user = _user(request)
        out = core.module("account").export(db.connection(), user, _base(), keys=_keys_or_none())
        resp = _json(out, private=True)
        resp["Content-Disposition"] = 'attachment; filename="patentref-registry-export.json"'
        return resp
    return _call(run)


@csrf_exempt
@require_POST
def account_erase(request):
    """Checklist 5.12: the account's records become tombstones (hash and proof kept; content, files, sealed copies gone)."""
    def run():
        _write_limit(request)
        user = _user(request)
        out = core.module("account").erase(db.connection(), user, _body_json(request).get("confirm"))
        for rid in out["erased"]:
            readcache.drop_record(rid)   # the next warm run caches the tombstone
        return _json(out, private=True)
    return _call(run)


@csrf_exempt
@require_POST
def monitor(request, rid):
    """Checklist 10.7: monitoring on or off for one of the account's records (REGISTRY_MONITORING_ENABLED; priced, 10.6)."""
    def run():
        _write_limit(request)
        claims = _claims(request)
        on = _body_json(request).get("on", True)
        m = core.module("monitor")
        if on:
            _offered("REGISTRY_MONITORING_ENABLED", "Monitoring")
            return _json(m.opt_in(db.connection(), claims["sub"], rid, claims.get("email")), private=True)
        return _json(m.opt_out(db.connection(), claims["sub"], rid), private=True)
    return _call(run)


@csrf_exempt
@require_POST
def contribution_log(request, rid):
    """Checklist 10.4: the owner's contribution log, countersigned now; its C2PA manifest follows from the proof worker."""
    def run():
        _guard_body(request)
        _write_limit(request)
        user = _user(request)
        _offered("REGISTRY_CONTRIBUTION_LOGS_ENABLED", "Contribution logs")
        out = core.module("service").submit_contribution_log(db.connection(), user_id=user, record_id=rid,
                                                             log=_body_json(request), keys=_keys(), base_url=_base())
        return _json(out, 201, private=True)
    return _call(run)


@require_GET
def contribution_zip(request, rid):
    """Checklist 10.4: the contribution log with its C2PA manifest, always as a download."""
    def run():
        _read_limit(request)
        user = _user(request, required=False)
        got = core.module("service").get_contribution_asset(db.connection(), rid, user_id=user)
        if got is None:
            raise HttpError(404, "ERR_NOT_FOUND", "No contribution log with a C2PA manifest for this record.")
        resp = HttpResponse(got[1], content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="contribution_log_{rid}.zip"'
        resp["X-Content-Type-Options"] = "nosniff"
        resp["Content-Security-Policy"] = "default-src 'none'; sandbox"
        resp["Cache-Control"] = "public, max-age=86400, immutable"
        resp["X-Robots-Tag"] = "noindex"
        return resp
    return _call(run)


@csrf_exempt
@require_POST
def claim(request, rid):
    """Checklist 10.9: a signed-in account says it owns a record; the record freezes until USPTO assignment records
    decide (an agent closes the case). REGISTRY_CLAIMS_ENABLED."""
    def run():
        _write_limit(request)
        user = _user(request)
        _offered("REGISTRY_CLAIMS_ENABLED", "Ownership claims")
        return _json(core.module("cases").public_case(
            db.connection(), core.module("cases").open_claim(db.connection(), user, rid, _body_json(request))["id"]),
            202, private=True)
    return _call(run)


@csrf_exempt
@require_POST
def notice(request):
    """Checklist 10.9: a copyright notice (REGISTRY_NOTICES_ENABLED; 5 per address per UTC day). Agents also open cases
    from notices that reach the designated agent by email (python3 -m patentref_registry.cases notice)."""
    def run():
        _write_limit(request)
        _offered("REGISTRY_NOTICES_ENABLED", "The notice form")
        subject = "ip:" + abuse.client_ip(request)
        window, wait = ratelimit.minute_window("registry_notice_day", 86400)
        if ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", window, 5) is None:
            raise HttpError(429, "ERR_RATE", "Too many notices from this address today.", headers={"Retry-After": str(wait)})
        c = core.module("cases").open_notice(db.connection(), _body_json(request))
        return _json(core.module("cases").public_case(db.connection(), c["id"]), 202, private=True)
    return _call(run)


@require_GET
def notice_status(request, cid):
    def run():
        _read_limit(request)
        c = core.module("cases").public_case(db.connection(), cid)
        if c is None:
            raise HttpError(404, "ERR_NOT_FOUND", "No case has this id.")
        return _json(c, private=True)
    return _call(run)


@csrf_exempt
@require_POST
def counter_notice(request, cid):
    """Checklist 10.9: the record owner's counter-notice to a copyright notice."""
    def run():
        _write_limit(request)
        user = _user(request)
        cases = core.module("cases")
        c = cases.counter_notice(db.connection(), user, cid, _body_json(request))
        return _json(cases.public_case(db.connection(), c["id"]), 202, private=True)
    return _call(run)


@csrf_exempt
@require_POST
def seal(request, rid):
    """Hook for checklist 10.4: a record is sealed when it is published (visibility "sealed"), never afterwards."""
    return _error_response(HttpError(501, "ERR_NOT_YET", "Sealed copies are published with visibility sealed and are not "
                                     "offered yet."))


@require_GET
def sealed_release(request, rid):
    """Hook for checklist 10.4: the owner's sealed bundle text, checked against the public hash."""
    def run():
        _read_limit(request)
        user = _user(request)
        if not getattr(settings, "REGISTRY_SEALED_ENABLED", False):
            raise HttpError(501, "ERR_NOT_YET", "Sealed copies are not offered yet.")
        return _json(core.module("service").release_sealed(db.connection(), user_id=user, record_id=rid, keys=_keys()),
                     private=True)
    return _call(run)


def keys_state():
    """absent: no registry_crypto package; uninitialized: the package but no signing key in service; present."""
    try:
        mod = core.module("keys").load()
    except Exception:
        return "absent"
    try:
        st = mod.status() if callable(getattr(mod, "status", None)) else {"signing_active": True}
        return "present" if st.get("signing_active") else "uninitialized"
    except Exception:
        return "uninitialized"


@require_GET
def health(request):
    ok = db.ping()
    body = {"ok": ok, "database": "ok" if ok else "unreachable", "keys": keys_state(),
            "receipts_enabled": bool(getattr(settings, "REGISTRY_RECEIPTS_ENABLED", False)),
            "sealed_enabled": bool(getattr(settings, "REGISTRY_SEALED_ENABLED", False)),
            "monitoring_enabled": bool(getattr(settings, "REGISTRY_MONITORING_ENABLED", False)),
            "contribution_logs_enabled": bool(getattr(settings, "REGISTRY_CONTRIBUTION_LOGS_ENABLED", False)),
            "terms_version": _module_attr("terms", "VERSION"),
            "read_cache": readcache.figures()}
    resp = _json(body, 200 if ok else 503, private=True)
    return resp


@require_GET
def jwks(request):
    """The receipt signing keys (checklist 5.14): every key, retired ones included, with nbf, exp and retired_at
    (seconds since 1970 UTC), so a receipt verifies for as long as this is published. No database involved."""
    def run():
        _read_limit(request)
        k = core.module("keys")
        try:
            body = k.jwks(_keys())
        except k.KeysUnavailable:
            log.warning("registry_crypto has no keyring")
            raise HttpError(501, "ERR_NOT_YET", "Signing is not available yet.")
        resp = HttpResponse(json.dumps(body, separators=(",", ":"), sort_keys=True), content_type="application/jwk-set+json")
        resp["Cache-Control"] = "public, max-age=300"
        resp["Access-Control-Allow-Origin"] = "*"
        resp["X-Content-Type-Options"] = "nosniff"
        return resp
    return _call(run)


def _module_attr(name, attr):
    try:
        return getattr(core.module(name), attr)
    except Exception:  # noqa: BLE001  (health never fails on this)
        return None


def _keys():
    k = core.module("keys")
    try:
        return k.load()
    except k.KeysUnavailable:
        # public text names no internal gate; the log and /registry/health say which part is missing
        log.warning("registry_crypto unavailable")
        raise HttpError(501, "ERR_NOT_YET", "Signing is not available yet.")


def _keys_or_none():
    try:
        return core.module("keys").load()
    except Exception:
        return None


def not_found(request, exception=None):
    return _error_response(HttpError(404, "ERR_NOT_FOUND", "No registry route here."))
