"""Account endpoints under /api/v1/meta/ (checklist gate 5.10).

  GET  /api/v1/meta/signup/   the sign-up and account page (HTML; Supabase Auth runs in the browser)
  GET  /api/v1/meta/keys/     Bearer <Supabase access token>: the caller's keys (prefixes, never secrets)
  POST /api/v1/meta/keys/     Bearer: mint the caller's Free key, or {"action": "rotate"} to replace it;
                              the key value is in the response once and never again
  GET  /api/v1/meta/usage/    X-Api-Key or Bearer: this month's count, limits and the reset time

Errors keep the upstream shape: body {"error": true} plus X-Status-Reason and X-Status-Reason-Code
(ERR_AUTH for a missing or bad token, ERR_KEY for a missing or revoked API key, ERR_Q for a bad body).
"""
import os
from datetime import datetime, timezone

from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from API.models import APIUserKey
from lapse_accounts import metering, public, supabase_jwt
from lapse_accounts.models import PLAN_FREE, AccountKey


def _error(code, reason, http_status):
    return Response(status=http_status, data={"error": True}, headers={"X-Status-Reason": reason, "X-Status-Reason-Code": code})


def _claims_or_error(request):
    token = supabase_jwt.bearer_token(request)
    if not token:
        return None, _error("ERR_AUTH", "Authorization: Bearer <Supabase access token> required", status.HTTP_401_UNAUTHORIZED)
    try:
        return supabase_jwt.verify(token), None
    except supabase_jwt.TokenError as exc:
        return None, _error("ERR_AUTH", exc.reason, status.HTTP_401_UNAUTHORIZED)


def _key_or_error(request):
    raw = request.META.get("HTTP_X_API_KEY")
    if not raw:
        return None, _error("ERR_KEY", "X-Api-Key header required (or a bearer token)", status.HTTP_403_FORBIDDEN)
    try:
        key = APIUserKey.objects.get_from_key(raw)
    except APIUserKey.DoesNotExist:
        return None, _error("ERR_KEY", "Unknown API key", status.HTTP_403_FORBIDDEN)
    if key.revoked:
        return None, _error("ERR_KEY", "API key revoked", status.HTTP_403_FORBIDDEN)
    return key, None


# ---- sign-up abuse limits (gate 5.6): keyed on the email domain the Supabase token carries
SCOPE_SIGNUP = "signup"
DISPOSABLE_DOMAINS = frozenset("""
10minutemail.com 10minutemail.net 20minutemail.com 33mail.com anonbox.net binkmail.com bobmail.info burnermail.io
byom.de courriel.fr.nf deadaddress.com discard.email dispostable.com dropmail.me emailondeck.com emailtemporanea.com
fakeinbox.com fakemail.net filzmail.com getairmail.com getnada.com guerrillamail.biz guerrillamail.com guerrillamail.de
guerrillamail.info guerrillamail.net guerrillamail.org guerrillamailblock.com grr.la harakirimail.com inboxkitten.com
jetable.org koszmail.pl kurzepost.de lroid.com mail-temp.com mail.tm mailcatch.com maildrop.cc mailexpire.com
mailinator.com mailinator.net mailinator2.com mailnesia.com mailnull.com mailsac.com mailtemp.info meltmail.com
mintemail.com mohmal.com moakt.com mytemp.email nada.email nowmymail.com objectmail.com owlpic.com pokemail.net
proxymail.eu rcpt.at sharklasers.com spam4.me spamgourmet.com spambox.us spamfree24.org spamherelots.com
temp-mail.io temp-mail.org tempail.com tempemail.co tempemail.com tempinbox.com tempmail.com tempmail.de tempmail.net
tempmailo.com tempmailaddress.com tempr.email temporaryemail.net throwawaymail.com throwam.com tmail.ws
tmpmail.net tmpmail.org trash-mail.com trashmail.com trashmail.de trashmail.me trashmail.net yopmail.com yopmail.fr
yopmail.net zetmail.com
""".split())
_extra_disposable = None


def disposable_domains():
    """The built-in list plus `LAPSE_DISPOSABLE_DOMAINS_FILE` (one domain per line, # comments), read once."""
    global _extra_disposable
    if _extra_disposable is None:
        extra = set()
        path = getattr(settings, "LAPSE_DISPOSABLE_DOMAINS_FILE", "")
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip().lower()
                    if line and not line.startswith("#"):
                        extra.add(line)
        _extra_disposable = frozenset(extra)
    return DISPOSABLE_DOMAINS | _extra_disposable


def email_domain(email):
    return (email or "").rsplit("@", 1)[-1].strip().lower()


def is_disposable(domain):
    parts = domain.split(".")
    known = disposable_domains()
    return any(".".join(parts[i:]) in known for i in range(len(parts) - 1))


def signup_domain_check(email, now=None):
    """None when the new account may be created, else the error response: 403 ERR_AUTH for a disposable
    domain, 429 (DRF's body, Retry-After to the end of the UTC day) when the domain has used its day's
    allowance (`LAPSE_SIGNUP_DOMAIN_DAILY`). The day's count is the same atomic window counter as the
    throttles, scope `signup`, window 86400 s aligned to the epoch (UTC days)."""
    from lapse_accounts import abuse, ratelimit

    domain = email_domain(email)
    if not domain:
        return _error("ERR_AUTH", "The token carries no email address", status.HTTP_403_FORBIDDEN)
    if is_disposable(domain):
        return _error("ERR_AUTH", f"Sign-ups from disposable email domains are not accepted ({domain})", status.HTTP_403_FORBIDDEN)
    limit = int(getattr(settings, "LAPSE_SIGNUP_DOMAIN_DAILY", 20))
    window, wait = ratelimit.minute_window(SCOPE_SIGNUP, 86400, now)
    count = ratelimit.bump(ratelimit.WINDOW_TABLE, "domain:" + domain, "win", window, limit)
    if count is None:
        resp = abuse.throttled_response(wait)
        resp["X-Status-Reason"] = f"Sign-up limit reached for the domain {domain}: {limit} new accounts a day"
        resp["X-Status-Reason-Code"] = "ERR_SIGNUP_LIMIT"
        return resp
    if count == 1:
        ratelimit.drop_old_windows("domain:" + domain, window)
    return None


def _active_account(user_id):
    return (
        AccountKey.objects.filter(supabase_user_id=user_id, api_key__revoked=False)
        .select_related("api_key")
        .order_by("-created_at")
        .first()
    )


def _key_info(acct, created=False, secret=None):
    monthly, per_minute = metering.plan_limits(acct.plan)
    return {
        "error": False,
        "created": created,
        "api_key": secret,
        "prefix": acct.prefix,
        "plan": acct.plan,
        "monthly_limit": monthly,
        "per_minute_limit": per_minute,
        "created_at": acct.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rotated_from": acct.rotated_from or None,
        "note": ("Store this key now; it is shown once." if secret else "The key value was shown once at creation. POST {\"action\": \"rotate\"} for a new one."),
    }


class MetaAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = []

    def get_throttles(self):
        from lapse_accounts.throttling import MetaThrottle

        return [MetaThrottle()]


class KeysView(MetaAPIView):
    def get(self, request):
        claims, err = _claims_or_error(request)
        if err:
            return err
        rows = AccountKey.objects.filter(supabase_user_id=claims["sub"]).select_related("api_key").order_by("-created_at")
        return Response({
            "error": False,
            "email": claims["email"],
            "keys": [
                {
                    "prefix": a.prefix,
                    "plan": a.plan,
                    "revoked": a.api_key.revoked,
                    "created_at": a.created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "rotated_from": a.rotated_from or None,
                }
                for a in rows
            ],
        })

    def post(self, request):
        claims, err = _claims_or_error(request)
        if err:
            return err
        body = request.data if isinstance(request.data, dict) else {}
        action = (body.get("action") or "create").lower()
        if action not in ("create", "rotate"):
            return _error("ERR_Q", "action must be 'create' or 'rotate'", status.HTTP_400_BAD_REQUEST)
        user_id, email = claims["sub"], claims["email"]
        with transaction.atomic():
            current = _active_account(user_id)
            if current and action == "create":
                return Response(_key_info(current))
            if current is None and not AccountKey.objects.filter(supabase_user_id=user_id).exists():
                # a new account's first key (gate 5.6): known disposable domains refused, any domain capped per day
                err = signup_domain_check(email)
                if err is not None:
                    return err
            rotated_from = ""
            if current:
                current.api_key.revoke()
                metering.forget_prefix(current.prefix)
                rotated_from = current.prefix
            name = f"{PLAN_FREE}:{user_id[:8]}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            obj, secret = APIUserKey.objects.create_key(name=name, username=email, email=email)
            acct = AccountKey.objects.create(api_key=obj, supabase_user_id=user_id, email=email, plan=PLAN_FREE, rotated_from=rotated_from)
        return Response(_key_info(acct, created=True, secret=secret), status=status.HTTP_201_CREATED)


class UsageView(MetaAPIView):
    def get(self, request):
        if supabase_jwt.bearer_token(request):
            claims, err = _claims_or_error(request)
            if err:
                return err
            acct = _active_account(claims["sub"])
            if acct is None:
                data = metering.usage_summary()
                data["key_prefix"] = None
                data["note"] = "No active key yet; POST /api/v1/meta/keys/ to mint one."
                return Response(data)
            return Response(metering.usage_summary(account=acct))
        key, err = _key_or_error(request)
        if err:
            return err
        return Response(metering.usage_summary(prefix=key.prefix))


def _data_version():
    """The served file's data_version (same source as the X-Data-Version header) and its date."""
    path = settings.LAPSE_SQLITE.get("path", "") if getattr(settings, "LAPSE_SQLITE", None) else ""
    as_of, version = "", ""
    if path and os.path.exists(path):
        as_of = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).strftime("%Y-%m-%d")
        try:
            from API.search_sqlite import LapseSQLiteSearch

            version = LapseSQLiteSearch.from_django_settings().data_version()
        except Exception:
            version = ""
    if not version:
        version = getattr(settings, "LAPSE_DATA_VERSION", "") or (os.path.splitext(os.path.basename(path))[0] if path else "beta")
    return version, as_of


@require_GET
def signup_page(request):
    version, as_of = _data_version()
    monthly, per_minute = metering.plan_limits(PLAN_FREE)
    html = render_to_string(
        "lapse_accounts/signup.html",
        {
            "supabase_url": getattr(settings, "SUPABASE_URL", ""),
            "supabase_anon_key": getattr(settings, "SUPABASE_ANON_KEY", ""),
            "data_version": version,
            "as_of": as_of,
            "monthly_limit": monthly,
            "per_minute_limit": per_minute,
            "page_url": request.build_absolute_uri(request.path),
            "footer_lines": public.footer_lines(),
        },
    )
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    return resp
