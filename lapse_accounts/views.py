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
from lapse_accounts import metering, supabase_jwt
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
    version = getattr(settings, "LAPSE_DATA_VERSION", "")
    path = settings.LAPSE_SQLITE.get("path", "") if getattr(settings, "LAPSE_SQLITE", None) else ""
    as_of = ""
    if path and os.path.exists(path):
        as_of = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).strftime("%Y-%m-%d")
    if not version:
        version = os.path.splitext(os.path.basename(path))[0] if path else "beta"
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
        },
    )
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    return resp
