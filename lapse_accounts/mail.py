"""Outbound mail rules (PatentRef checklist 5.16, patentref skill email-template): one-click unsubscribe (RFC 8058) and
bounce and complaint suppression.

How mail leaves the stack (checked 2026-10-20): only Supabase Auth sends mail (sign-up confirmation, password recovery,
magic link, email change), through Resend's SMTP relay as noreply@mail.patentref.io. All of it is account mail. No code
in this fork or in the patentref repo sends mail, and patentref-us1 runs no mail server. Resend keeps a team-wide
suppression list: an address that hard-bounced or complained is not mailed again (the send shows as "suppressed"), and
that list covers the Supabase sends.

This module is the path any mail PatentRef itself sends must take:
  prepare(to, kind, subject, text)  refuses a suppressed address (every kind) and an address that unsubscribed from that
                                    kind (security kinds excepted), and returns the message with the List-Unsubscribe and
                                    List-Unsubscribe-Post headers of RFC 8058 for a per-recipient https URL
  GET  /u/<token>                   a page with one button (a GET never unsubscribes: link scanners follow GETs)
  POST /u/<token>                   the one-click unsubscribe: body List-Unsubscribe=One-Click, no login, no cookie
  POST /api/v1/meta/email/events/   Resend's webhook, signed the Svix way. email.bounced with bounce.type Permanent and
                                    email.complained suppress the address at once, Temporary bounces after 3 in 7 days,
                                    suppression.added mirrors Resend's own list. The signing secret is read from
                                    LAPSE_RESEND_WEBHOOK_SECRET_FILE on every request, so installing it needs no restart;
                                    without it the route answers 503 and stores nothing.
State: a small SQLite file (LAPSE_EMAIL_DB, default <LAPSE_DATA_DIR>/email.sqlite3) created on first use, like the
count cache. Addresses are stored lower-cased.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from lapse_accounts import public

# The kinds of skill email-template. Security kinds carry the header too, but unsubscribing never stops them.
KINDS = {
    "verify-email": "sign-up confirmation",
    "key-issued": "key notices",
    "receipt-issued": "receipts",
    "record-complete": "record completion notices",
    "monitor-hit": "monitor alerts",
    "payment-failed": "payment notices",
    "claim-step": "owner claim progress",
    "support-reply": "support replies",
}
SECURITY_KINDS = {"verify-email", "key-issued"}
SOFT_BOUNCES = 3                  # Temporary bounces within SOFT_WINDOW that suppress an address
SOFT_WINDOW = 7 * 86400
TOLERANCE = 300                   # seconds between the webhook's svix-timestamp and now
FROM = "PatentRef <noreply@mail.patentref.io>"

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppression(address TEXT PRIMARY KEY, reason TEXT NOT NULL, source TEXT NOT NULL, at TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS soft_bounce(address TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS unsubscribe(address TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(address, kind));
CREATE TABLE IF NOT EXISTS token(token TEXT PRIMARY KEY, address TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(address, kind));
CREATE TABLE IF NOT EXISTS event(id TEXT PRIMARY KEY, type TEXT, at TEXT NOT NULL);
"""


class Suppressed(Exception):
    """The address must not be mailed (suppressed, or unsubscribed from this kind)."""


def _now():
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path():
    return Path(getattr(settings, "LAPSE_EMAIL_DB", "") or Path(getattr(settings, "LAPSE_STATUS_PATHS", {}).get("api_dir", "/data/api")) / "email.sqlite3")


def connect():
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    con.executescript(SCHEMA)
    return con


@contextmanager
def db():
    """One transaction on the mail state file; the connection is closed after it."""
    con = connect()
    try:
        with con:
            yield con
    finally:
        con.close()


_ADDR = re.compile(r"<([^>]+)>")


def norm(address):
    """The bare address of 'Name <a@b>' or 'a@b', lower-cased; '' when it is not an address."""
    a = (address or "").strip()
    m = _ADDR.search(a)
    if m:
        a = m.group(1).strip()
    a = a.lower()
    return a if re.fullmatch(r"[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+", a) else ""


def suppress(address, reason, source, detail=""):
    a = norm(address)
    if not a:
        return False
    with db() as con:
        con.execute("INSERT INTO suppression(address, reason, source, at, detail) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(address) DO NOTHING", (a, reason, source, _now(), (detail or "")[:300]))
    return True


def is_suppressed(address):
    a = norm(address)
    with db() as con:
        return con.execute("SELECT 1 FROM suppression WHERE address=?", (a,)).fetchone() is not None


def is_unsubscribed(address, kind):
    with db() as con:
        return con.execute("SELECT 1 FROM unsubscribe WHERE address=? AND kind=?", (norm(address), kind)).fetchone() is not None


def can_send(address, kind):
    if kind not in KINDS:
        raise ValueError(f"unknown mail kind {kind!r}: add it to skill email-template and KINDS first")
    if not norm(address) or is_suppressed(address):
        return False
    return kind in SECURITY_KINDS or not is_unsubscribed(address, kind)


def token_for(address, kind):
    a = norm(address)
    with db() as con:
        row = con.execute("SELECT token FROM token WHERE address=? AND kind=?", (a, kind)).fetchone()
        if row:
            return row[0]
        t = secrets.token_urlsafe(24)
        con.execute("INSERT INTO token(token, address, kind, created_at) VALUES (?,?,?,?)", (t, a, kind, _now()))
        return t


def unsubscribe_url(address, kind):
    base = getattr(settings, "LAPSE_PUBLIC_BASE", "https://patentref.io").rstrip("/")
    return f"{base}/u/{token_for(address, kind)}"


def unsubscribe_headers(address, kind):
    """The two RFC 8058 headers. The provider's DKIM signature must cover both (Resend signs them)."""
    return {"List-Unsubscribe": f"<{unsubscribe_url(address, kind)}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def prepare(to, kind, subject, text):
    """The message as Resend's send API takes it (from, to, subject, text, headers), or Suppressed."""
    if not can_send(to, kind):
        raise Suppressed(f"{norm(to) or to!r} is suppressed or unsubscribed from {kind}")
    url = unsubscribe_url(to, kind)
    footer = f"\n\nWhy you got this: your PatentRef account ({KINDS[kind]}).\nUnsubscribe from {KINDS[kind]}: {url}\n"
    return {"from": FROM, "to": [norm(to)], "subject": subject, "text": text.rstrip() + footer, "headers": unsubscribe_headers(to, kind)}


# ---------------------------------------------------------------- the unsubscribe page and the one-click POST

def _lookup(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        return None
    with db() as con:
        return con.execute("SELECT address, kind FROM token WHERE token=?", (token,)).fetchone()


def masked(address):
    local, _, domain = address.partition("@")
    return (local[:1] + "***@" + domain) if local else address


def _page(request, ctx, status=200):
    from lapse_accounts.views import _data_version

    version, as_of = _data_version()
    ctx.update({"data_version": version, "as_of": as_of, "footer_lines": public.footer_lines()})
    resp = HttpResponse(render_to_string("lapse_accounts/unsubscribe.html", ctx, request=request), status=status,
                        content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    resp["X-Robots-Tag"] = "noindex"
    return resp


@csrf_exempt
@require_http_methods(["GET", "POST"])
def unsubscribe(request, token):
    row = _lookup(token)
    if row is None:
        if request.method == "POST" and request.POST.get("from_page") != "1":
            return HttpResponse("unknown unsubscribe link\n", status=404, content_type="text/plain; charset=utf-8")
        return _page(request, {"state": "unknown"}, status=404)
    address, kind = row
    what = {"address": masked(address), "kind": KINDS.get(kind, kind), "security": kind in SECURITY_KINDS, "token": token}
    if request.method == "GET":
        return _page(request, dict(what, state="ask"))
    if request.POST.get("List-Unsubscribe") != "One-Click":
        return HttpResponse("the body must be List-Unsubscribe=One-Click (RFC 8058)\n", status=400, content_type="text/plain; charset=utf-8")
    with db() as con:
        con.execute("INSERT INTO unsubscribe(address, kind, at) VALUES (?,?,?) ON CONFLICT(address, kind) DO NOTHING", (address, kind, _now()))
    if request.POST.get("from_page") == "1":
        return _page(request, dict(what, state="done"))
    return HttpResponse("unsubscribed\n", content_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------- Resend's webhook (bounces and complaints)

def webhook_secret():
    p = Path(getattr(settings, "LAPSE_RESEND_WEBHOOK_SECRET_FILE", "/data/lapse/secrets/resend_webhook_secret.txt"))
    try:
        s = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return s or None


def verify(secret, msg_id, timestamp, body, signature_header, now=None):
    """Svix's scheme as Resend uses it: base64(HMAC-SHA256(key, f"{id}.{timestamp}.{body}")), key = base64 of the secret
    after "whsec_"; the header holds one or more "v1,<sig>" separated by spaces. Returns (ok, why)."""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False, "bad svix-timestamp"
    if abs((now or time.time()) - ts) > TOLERANCE:
        return False, "svix-timestamp outside 5 minutes"
    try:
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except (ValueError, IndexError):
        return False, "the installed secret is not a whsec_ value"
    signed = msg_id.encode() + b"." + str(ts).encode() + b"." + body
    want = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in (signature_header or "").split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, want):
            return True, ""
    return False, "signature mismatch"


def record_event(event):
    """Store what one verified Resend event means for suppression; returns the number of addresses suppressed."""
    etype = event.get("type") or ""
    data = event.get("data") or {}
    to = data.get("to") or []
    if isinstance(to, str):
        to = [to]
    n = 0
    if etype == "email.bounced":
        b = data.get("bounce") or {}
        detail = f"{b.get('type', '')}/{b.get('subType', '')}: {b.get('message', '')}"
        if (b.get("type") or "").lower() == "permanent":
            n = sum(suppress(a, "hard_bounce", "resend", detail) for a in to)
        else:
            now = time.time()
            with db() as con:
                for a in filter(None, map(norm, to)):
                    con.execute("INSERT INTO soft_bounce(address, at) VALUES (?,?)", (a, now))
                    k = con.execute("SELECT COUNT(*) FROM soft_bounce WHERE address=? AND at>=?", (a, now - SOFT_WINDOW)).fetchone()[0]
                    if k >= SOFT_BOUNCES:
                        con.execute("INSERT INTO suppression(address, reason, source, at, detail) VALUES (?,?,?,?,?) ON CONFLICT(address) DO NOTHING",
                                    (a, "soft_bounces", "resend", _now(), f"{k} temporary bounces in 7 days; last {detail}"[:300]))
                        n += 1
    elif etype == "email.complained":
        n = sum(suppress(a, "complaint", "resend") for a in to)
    elif etype == "suppression.added":
        addr = data.get("email") or data.get("address") or (to[0] if to else "")
        n = int(suppress(addr, "resend_list", "resend", str(data.get("reason") or "")))
    return n


@csrf_exempt
@require_http_methods(["POST"])
def resend_events(request):
    secret = webhook_secret()
    if not secret:
        return JsonResponse({"error": True, "reason": "the Resend webhook signing secret is not installed on this server"}, status=503)
    msg_id = request.META.get("HTTP_SVIX_ID", "")
    ts = request.META.get("HTTP_SVIX_TIMESTAMP", "")
    sig = request.META.get("HTTP_SVIX_SIGNATURE", "")
    if not (msg_id and ts and sig):
        return JsonResponse({"error": True, "reason": "missing svix-id, svix-timestamp or svix-signature"}, status=400)
    body = request.body
    ok, why = verify(secret, msg_id, ts, body, sig)
    if not ok:
        return JsonResponse({"error": True, "reason": why}, status=401)
    try:
        event = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"error": True, "reason": "the body is not JSON"}, status=400)
    with db() as con:
        seen = con.execute("SELECT 1 FROM event WHERE id=?", (msg_id,)).fetchone()
        if not seen:
            con.execute("INSERT INTO event(id, type, at) VALUES (?,?,?)", (msg_id, str(event.get("type"))[:60], _now()))
    if seen:
        return JsonResponse({"ok": True, "duplicate": True})
    return JsonResponse({"ok": True, "type": event.get("type"), "suppressed": record_event(event)})
