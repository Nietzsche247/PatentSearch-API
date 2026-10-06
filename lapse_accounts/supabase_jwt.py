"""Verification of Supabase Auth access tokens (checklist gate 5.10, scaffold section 2).

Django never talks to Supabase to check a user: it verifies the JWT signature against the project's
JWKS (`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, ES256 on this project), fetched with the
standard library and cached in process and in the Django cache. A fetch failure keeps serving from
the cached keys, so a Supabase outage does not stop key minting for tokens that are still valid
(their lifetime is one hour by default). Settings read: SUPABASE_URL, SUPABASE_JWKS_URL,
SUPABASE_JWT_ISSUER, SUPABASE_JWKS_STATIC (tests only: a JWKS dict used instead of fetching).

Claims checked: signature, `exp`, `aud` == "authenticated", `iss` == the project's auth issuer, `sub`
present, `role` == "authenticated", not `is_anonymous`, and `user_metadata.email_verified` not false.
On this project `mailer_autoconfirm` is off, so Supabase only issues an access token to a user whose
email is confirmed; the metadata check is belt and braces.
"""
import json
import logging
import threading
import time
import urllib.request

import jwt
from django.conf import settings
from django.core.cache import cache

log = logging.getLogger("lapse_accounts")

ALGORITHMS = ["ES256", "RS256"]
AUDIENCE = "authenticated"
REFRESH_AFTER = 6 * 3600  # re-fetch the JWKS this often while it keeps working
RETRY_AFTER = 60  # minimum gap between forced re-fetches (unknown kid, fetch error)
CACHE_KEY = "lapse_supabase_jwks"

_lock = threading.Lock()
_state = {"jwks": None, "fetched_at": 0.0, "attempted_at": 0.0}


class TokenError(Exception):
    """A token that must not be accepted; `reason` is safe to show the client."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def jwks_url():
    url = getattr(settings, "SUPABASE_JWKS_URL", "")
    if url:
        return url
    base = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    return base + "/auth/v1/.well-known/jwks.json" if base else ""


def issuer():
    iss = getattr(settings, "SUPABASE_JWT_ISSUER", "")
    if iss:
        return iss
    base = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    return base + "/auth/v1" if base else ""


def _fetch():
    url = jwks_url()
    if not url:
        raise TokenError("Supabase is not configured on this server")
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 (fixed https URL from settings)
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        raise ValueError("JWKS response has no keys list")
    return data


def get_jwks(force=False):
    """The JWKS dict, from settings (tests), memory, the Django cache or the project, in that order."""
    static = getattr(settings, "SUPABASE_JWKS_STATIC", None)
    if static:
        return static
    now = time.time()
    with _lock:
        jwks = _state["jwks"]
        if jwks is None:
            cached = cache.get(CACHE_KEY)
            if cached:
                _state["jwks"], _state["fetched_at"] = cached["jwks"], cached["fetched_at"]
                jwks = cached["jwks"]
        stale = jwks is None or now - _state["fetched_at"] > REFRESH_AFTER
        if (stale or force) and now - _state["attempted_at"] >= RETRY_AFTER:
            _state["attempted_at"] = now
            try:
                fresh = _fetch()
                _state["jwks"], _state["fetched_at"] = fresh, now
                cache.set(CACHE_KEY, {"jwks": fresh, "fetched_at": now}, None)
                jwks = fresh
            except TokenError:
                raise
            except Exception as exc:  # keep serving from cache during an outage
                log.warning("JWKS fetch failed, using cached keys: %s", exc)
        if jwks is None:
            raise TokenError("Could not load the Supabase signing keys")
        return jwks


def reset_cache():
    with _lock:
        _state.update({"jwks": None, "fetched_at": 0.0, "attempted_at": 0.0})
    cache.delete(CACHE_KEY)


def _key_for(token):
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise TokenError(f"Malformed token: {exc}")
    kid = header.get("kid")
    alg = header.get("alg")
    if alg not in ALGORITHMS:
        raise TokenError(f"Unsupported token algorithm {alg!r}; expected one of {ALGORITHMS}")
    for attempt in (0, 1):
        jwks = get_jwks(force=attempt == 1)
        for k in jwks.get("keys", []):
            if (kid is None or k.get("kid") == kid) and k.get("kty") in ("EC", "RSA"):
                return jwt.PyJWK(k).key
    raise TokenError("Token signed by an unknown key")


def verify(token):
    """Return the claims of a valid Supabase access token or raise TokenError."""
    if not token or token.count(".") != 2:
        raise TokenError("Missing or malformed bearer token")
    key = _key_for(token)
    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=issuer() or None,
            options={"require": ["exp", "sub", "aud"]},
            leeway=30,
        )
    except jwt.ExpiredSignatureError:
        raise TokenError("Token expired; sign in again")
    except jwt.PyJWTError as exc:
        raise TokenError(f"Invalid token: {exc}")
    if claims.get("role") != AUDIENCE:
        raise TokenError("Token role is not 'authenticated'")
    if claims.get("is_anonymous"):
        raise TokenError("Anonymous sessions cannot mint keys")
    meta = claims.get("user_metadata") or {}
    if meta.get("email_verified") is False:
        raise TokenError("Email address not confirmed yet")
    if not claims.get("email"):
        raise TokenError("Token carries no email address")
    return claims


def bearer_token(request):
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return None
