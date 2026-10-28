"""The waitlist (PatentRef wave 4 Q1): one public form, double opt-in, at most three kinds of mail, rows apart from accounts.

  GET  /waitlist/                   the page with the form: an email address and three choices (TOPICS)
  POST /waitlist/                   the form: rate limits, then one confirmation mail; nothing is on the list until confirmed
  GET  /waitlist/confirm/<token>    a page with one button (a GET never confirms: mail scanners follow links)
  POST /waitlist/confirm/<token>    confirms: the choices are kept from then on
  POST /u/<token>                   the one-click unsubscribe of every waitlist mail (RFC 8058, lapse_accounts/mail.py),
                                    which also deletes the address's waitlist row

Mail. The only mail this module sends: the confirmation (again when the address asks again, at most once in 10 minutes
and 3 times a UTC day), one opening notice per chosen topic when the operator opens it, and at most one other notice per
address (`python manage.py waitlist open|notice`, dry runs unless --send). Every message carries List-Unsubscribe (the
https one-click URL and a mailto) and List-Unsubscribe-Post (RFC 8058) for kind "waitlist", and none goes to an address on
the suppression list (bounces and complaints, lapse_accounts/mail.py). It leaves through the Resend relay the registry
outbox already uses: the settings in
LAPSE_MAIL_ENV_FILE (default /data/registry/mail.env: REGISTRY_SMTP_HOST, _PORT, _USER, _PASSWORD_FILE), read at
send time, so installing or changing them needs no restart; without them the form answers 503 and stores nothing.

Storage. Its own SQLite file (LAPSE_WAITLIST_DB, default <LAPSE_DATA_DIR>/waitlist.sqlite3), apart from the account
database (accounts, keys, counters) and from the mail state file, and not in the nightly backup set. A row holds the
address, the confirmed choices, the choices waiting for confirmation and a hash of the confirmation token, when it was
created, sent and confirmed, and when its one other notice went; table `sent` records which opening notices went. Purge
(`waitlist purge`, run every day by ops/systemd/patentref-counts.timer and at every form post): an address never confirmed
goes 7 days after it was entered; a choice goes 30 days after its topic opened; a row with no choice left goes with its
`sent` rows; the post caps' counters go once their window (an hour, a day) is over. Leaving (the unsubscribe) deletes
the row at once.

Limits, like the sign-up caps (views.signup_domain_check): disposable domains refused; per client address
LAPSE_WAITLIST_IP_HOURLY posts an hour (5) and per email domain LAPSE_WAITLIST_DOMAIN_DAILY a UTC day (200), on the same
atomic window counters (lapse_accounts.ratelimit), 429 with Retry-After above them. A filled hidden field (the honeypot)
gets the normal answer and nothing is stored or sent.

The form opens, and mail goes, only once Privacy Policy beta-4, which describes all this, is in force on this server
(`legal.in_force`); before its effective date the page says when it opens.
"""
import hashlib
import re
import secrets
import smtplib
import sqlite3
import ssl
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from lapse_accounts import abuse, mail, public, ratelimit

TOPICS = {"lapse": "Lapse alerts and lists", "rules": "Rules updates", "registry": "Registry opening"}
KIND = "waitlist"                 # the mail kind of every waitlist message (mail.KINDS), for the unsubscribe token
POLICY = ("privacy", 4)           # the document and the beta version that describe the waitlist
UNCONFIRMED_DAYS = 7              # an address never confirmed is deleted after this
AFTER_OPENING_DAYS = 30           # a choice is deleted this long after its topic opened
RESEND_GAP = 600                  # seconds between two confirmation mails to one address
CONFIRMS_PER_DAY = 3              # confirmation mails to one address in a UTC day
IMPLICIT_TLS = (465, 2465)        # as the registry outbox: implicit TLS on these ports, STARTTLS on the others
FOOTER_POSTAL = "PatentRef, Aaron Baker, 6640 North Oracle Road Ste 130A, Tucson, AZ 85704"
SCOPE_IP, SCOPE_DOMAIN = "wl-ip", "wl-domain"

SCHEMA = """
CREATE TABLE IF NOT EXISTS waitlist(
  address TEXT PRIMARY KEY,
  topics TEXT NOT NULL DEFAULT '',
  pending_topics TEXT,
  token_hash TEXT UNIQUE,
  created_at TEXT NOT NULL,
  confirm_sent_at TEXT,
  confirm_day TEXT,
  confirm_sends INTEGER NOT NULL DEFAULT 0,
  confirmed_at TEXT,
  other_notice_at TEXT
);
CREATE TABLE IF NOT EXISTS opened(topic TEXT PRIMARY KEY, opened_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sent(address TEXT NOT NULL, topic TEXT NOT NULL, kind TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY(address, topic, kind));
"""


class NoSender(Exception):
    """No Resend relay settings on this server (LAPSE_MAIL_ENV_FILE)."""


def _now():
    return datetime.now(UTC)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) if s else None


def db_path():
    p = getattr(settings, "LAPSE_WAITLIST_DB", "")
    if p:
        return Path(p)
    return Path(getattr(settings, "LAPSE_STATUS_PATHS", {}).get("api_dir", "/data/api")) / "waitlist.sqlite3"


@contextmanager
def db():
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), timeout=10)
    try:
        con.executescript(SCHEMA)
        with con:
            yield con
    finally:
        con.close()


def is_open(today=None):
    """(open, the date it opens) from the Privacy Policy served on this server."""
    from lapse_accounts import legal

    return legal.in_force(*POLICY, today=today)


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def topic_list(csv):
    return [t for t in (csv or "").split(",") if t in TOPICS]


def topic_names(csv):
    return ", ".join(TOPICS[t] for t in topic_list(csv))


# ---------------------------------------------------------------- mail through the Resend relay

def mail_env():
    """The relay settings (host, port, user, password file) from LAPSE_MAIL_ENV_FILE, read now; NoSender without them."""
    path = Path(getattr(settings, "LAPSE_MAIL_ENV_FILE", "/data/registry/mail.env"))
    vals = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.removeprefix("export ").split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        raise NoSender(f"no mail settings at {path}") from None
    need = ("REGISTRY_SMTP_HOST", "REGISTRY_SMTP_PORT", "REGISTRY_SMTP_USER", "REGISTRY_SMTP_PASSWORD_FILE")
    missing = [k for k in need if not vals.get(k)]
    if missing:
        raise NoSender(f"{path} lacks {', '.join(missing)}")
    return {k: vals[k] for k in need}


def smtp_send(msg):
    """Hand one message to the relay (the registry outbox's rule for the port). Raises on any failure."""
    cfg = mail_env()
    with open(cfg["REGISTRY_SMTP_PASSWORD_FILE"], encoding="utf-8") as f:
        password = f.read().strip()
    port = int(cfg["REGISTRY_SMTP_PORT"])
    ctx = ssl.create_default_context()
    if port in IMPLICIT_TLS:
        s = smtplib.SMTP_SSL(cfg["REGISTRY_SMTP_HOST"], port, timeout=20, context=ctx)
    else:
        s = smtplib.SMTP(cfg["REGISTRY_SMTP_HOST"], port, timeout=20)
        s.starttls(context=ctx)
    with s:
        s.login(cfg["REGISTRY_SMTP_USER"], password)
        s.send_message(msg)


transport = smtp_send  # tests replace it


def base_url():
    return getattr(settings, "LAPSE_PUBLIC_BASE", "https://patentref.io").rstrip("/")


def message(to, subject, text, why, preview=False):
    """One waitlist message: plain text, the footer with the reason, the way out and the postal address, and the two
    RFC 8058 headers for this recipient (mail.list_unsubscribe: the https one-click URL and a mailto). preview: a
    placeholder unsubscribe link, nothing written (the dry run)."""
    token = "RECIPIENT-TOKEN" if preview else mail.token_for(to, KIND)
    leave = f"{base_url()}/u/{token}"
    out = EmailMessage()
    out["From"] = getattr(settings, "LAPSE_WAITLIST_FROM", mail.FROM)
    out["To"] = to
    out["Subject"] = subject
    out["Date"] = format_datetime(_now())
    out["Message-ID"] = make_msgid(domain="mail.patentref.io")
    for k, v in mail.list_unsubscribe(token, base_url()).items():
        out[k] = v
    footer = (f"\n\n--\nWhy you got this: {why}\nLeave the waitlist (one click, nothing more is sent): "
              f"{leave}\nWhat we keep and why: {base_url()}/docs/legal/privacy\n{FOOTER_POSTAL}\n")
    out.set_content(text.rstrip() + footer)
    return out


def check_text(text):
    """Notice text keeps the wording rules: no em-dash or en-dash, not empty. Returns the reason it fails, or None."""
    if not text.strip():
        return "the text is empty"
    if "\u2014" in text or "\u2013" in text:
        return "the text has an em-dash or an en-dash"
    return None


# ---------------------------------------------------------------- the list

def purge(now=None):
    """Apply the retention rules; returns the counts removed."""
    now = now or _now()
    out = {"unconfirmed": 0, "pending": 0, "choices": 0, "rows": 0, "mail_state": 0, "counters": 0}
    with db() as con:
        cut = _iso(now - timedelta(days=UNCONFIRMED_DAYS))
        out["unconfirmed"] = con.execute("DELETE FROM waitlist WHERE confirmed_at IS NULL AND created_at < ? "
                                         "AND COALESCE(confirm_sent_at, created_at) < ?", (cut, cut)).rowcount
        out["pending"] = con.execute("UPDATE waitlist SET pending_topics = NULL, token_hash = NULL WHERE confirmed_at IS NOT NULL "
                                     "AND token_hash IS NOT NULL AND COALESCE(confirm_sent_at, created_at) < ?", (cut,)).rowcount
        done = [t for (t,) in con.execute("SELECT topic FROM opened WHERE opened_at < ?",
                                          (_iso(now - timedelta(days=AFTER_OPENING_DAYS)),))]
        if done:
            for address, topics in con.execute("SELECT address, topics FROM waitlist WHERE confirmed_at IS NOT NULL").fetchall():
                keep = [t for t in topic_list(topics) if t not in done]
                if len(keep) != len(topic_list(topics)):
                    out["choices"] += len(topic_list(topics)) - len(keep)
                    con.execute("UPDATE waitlist SET topics = ? WHERE address = ?", (",".join(keep), address))
        gone = [a for (a,) in con.execute("SELECT address FROM waitlist WHERE confirmed_at IS NOT NULL AND topics = '' "
                                          "AND token_hash IS NULL")]
        for a in gone:
            con.execute("DELETE FROM sent WHERE address = ?", (a,))
            con.execute("DELETE FROM waitlist WHERE address = ?", (a,))
        out["rows"] = len(gone)
        con.execute("DELETE FROM sent WHERE address NOT IN (SELECT address FROM waitlist)")
        kept = {a for (a,) in con.execute("SELECT address FROM waitlist")}
    # the post caps' counters (client address for an hour, email domain for a day) go once their window is over
    from django.db import connection

    with connection.cursor() as cur:
        for scope, seconds in ((SCOPE_IP, 3600), (SCOPE_DOMAIN, 86400)):
            current, _ = ratelimit.minute_window(scope, seconds, now.timestamp())
            cur.execute(f"DELETE FROM {ratelimit.WINDOW_TABLE} WHERE substr(win, 1, %s) = %s AND win < %s",
                        [len(scope) + 1, scope + ":", current])
            out["counters"] += cur.rowcount
    # the mail state file holds the unsubscribe link (and, after leaving, the unsubscribe record) of every address a
    # waitlist mail went to; once the address is off the list neither is needed, so neither is kept
    with mail.db() as con:
        for table in ("token", "unsubscribe"):
            stale = [a for (a,) in con.execute(f"SELECT address FROM {table} WHERE kind = ?", (KIND,)) if a not in kept]
            con.executemany(f"DELETE FROM {table} WHERE address = ? AND kind = ?", [(a, KIND) for a in stale])
            out["mail_state"] += len(stale)
    return out


def forget(address):
    """Delete the address's row and its notice records (the unsubscribe). Returns True when there was a row."""
    a = mail.norm(address)
    with db() as con:
        con.execute("DELETE FROM sent WHERE address = ?", (a,))
        return con.execute("DELETE FROM waitlist WHERE address = ?", (a,)).rowcount > 0


def summary():
    """Counts only, never an address: confirmed and waiting rows, and confirmed rows per topic, with the openings."""
    with db() as con:
        confirmed = con.execute("SELECT COUNT(*) FROM waitlist WHERE confirmed_at IS NOT NULL").fetchone()[0]
        waiting = con.execute("SELECT COUNT(*) FROM waitlist WHERE confirmed_at IS NULL").fetchone()[0]
        per = {t: 0 for t in TOPICS}
        for (topics,) in con.execute("SELECT topics FROM waitlist WHERE confirmed_at IS NOT NULL"):
            for t in topic_list(topics):
                per[t] += 1
        opened = dict(con.execute("SELECT topic, opened_at FROM opened").fetchall())
        other = con.execute("SELECT COUNT(*) FROM waitlist WHERE other_notice_at IS NOT NULL").fetchone()[0]
    return {"confirmed": confirmed, "waiting_for_confirmation": waiting, "per_topic": per, "opened": opened,
            "had_other_notice": other}


def request_confirmation(address, topics, now=None):
    """Store the choices as waiting and send one confirmation mail. Returns "sent", "held" (an address that had one in the
    last 10 minutes or 3 today, or a suppressed address: nothing is sent and the page answers the same) or raises NoSender
    or the relay's error (then a new row is removed again)."""
    now = now or _now()
    a = mail.norm(address)
    if mail.is_suppressed(a):
        return "held"
    day = now.strftime("%Y-%m-%d")
    token = secrets.token_urlsafe(24)
    csv = ",".join(t for t in TOPICS if t in topics)
    with db() as con:
        row = con.execute("SELECT confirm_sent_at, confirm_day, confirm_sends FROM waitlist WHERE address = ?", (a,)).fetchone()
        if row:
            last = _parse(row[0])
            sends = row[2] if row[1] == day else 0   # confirmation mails already sent this UTC day
            if (last and (now - last).total_seconds() < RESEND_GAP) or sends >= CONFIRMS_PER_DAY:
                return "held"
            con.execute("UPDATE waitlist SET pending_topics = ?, token_hash = ? WHERE address = ?", (csv, _hash(token), a))
        else:
            con.execute("INSERT INTO waitlist(address, pending_topics, token_hash, created_at) VALUES (?,?,?,?)",
                        (a, csv, _hash(token), _iso(now)))
    url = f"{base_url()}/waitlist/confirm/{token}"
    text = (f"Someone, probably you, asked to join the PatentRef waitlist with this address, for: {topic_names(csv)}.\n\n"
            f"To confirm, open this link and press Confirm:\n{url}\n\n"
            f"If it was not you, ignore this mail. Without a confirmation the address is deleted after {UNCONFIRMED_DAYS} days "
            "and nothing more is sent.\n\n"
            "Once confirmed, we send one notice when each thing you chose opens, and at most one other notice. Nothing else.")
    try:
        transport(message(a, "Confirm your place on the PatentRef waitlist", text,
                          f"this address was entered on {base_url()}/waitlist/"))
    except Exception:
        with db() as con:
            if not row:
                con.execute("DELETE FROM waitlist WHERE address = ? AND confirmed_at IS NULL", (a,))
            else:
                con.execute("UPDATE waitlist SET pending_topics = NULL, token_hash = NULL WHERE address = ?", (a,))
        raise
    with db() as con:
        con.execute("UPDATE waitlist SET confirm_sent_at = ?, confirm_day = ?, confirm_sends = ? WHERE address = ?",
                    (_iso(now), day, (row[2] if row and row[1] == day else 0) + 1, a))
    return "sent"


def lookup(token):
    """(address, pending topics) for a live confirmation token, else None."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token or ""):
        return None
    with db() as con:
        row = con.execute("SELECT address, pending_topics, COALESCE(confirm_sent_at, created_at) FROM waitlist "
                          "WHERE token_hash = ?", (_hash(token),)).fetchone()
    if not row or not row[1]:
        return None
    if (_now() - _parse(row[2])).days >= UNCONFIRMED_DAYS:
        return None
    return row[0], row[1]


def confirm(token, now=None):
    """Make the waiting choices the address's choices. Returns (address, topics csv) or None for an unknown token."""
    found = lookup(token)
    if not found:
        return None
    a, csv = found
    with db() as con:
        con.execute("UPDATE waitlist SET topics = ?, pending_topics = NULL, token_hash = NULL, confirmed_at = ? WHERE address = ?",
                    (csv, _iso(now or _now()), a))
    with mail.db() as con:   # joining again after leaving: the address asked for waitlist mail once more
        con.execute("DELETE FROM unsubscribe WHERE address = ? AND kind = ?", (a, KIND))
    return a, csv


def _recipients(con, topic, kind):
    rows = con.execute("SELECT address, topics, other_notice_at FROM waitlist WHERE confirmed_at IS NOT NULL").fetchall()
    out = []
    for address, topics, other in rows:
        if topic not in topic_list(topics):
            continue
        if kind == "opening" and con.execute("SELECT 1 FROM sent WHERE address = ? AND topic = ? AND kind = 'opening'",
                                             (address, topic)).fetchone():
            continue
        if kind == "other" and other:
            continue
        out.append(address)
    return out


def notify(topic, kind, subject, text, send=False, now=None):
    """The opening notice of a topic (kind "opening": records the opening, at most once per address and topic) or the one
    other notice (kind "other": at most once per address, whatever the topic). A dry run (send False) changes nothing and
    returns the counts. Returns {"recipients", "sent", "skipped_suppressed", "failed"}."""
    if topic not in TOPICS:
        raise ValueError(f"unknown topic {topic!r}; one of {', '.join(TOPICS)}")
    if kind not in ("opening", "other"):
        raise ValueError("kind is opening or other")
    why = check_text(text) or (check_text(subject) if subject.strip() else "the subject is empty")
    if why:
        raise ValueError(why)
    now = now or _now()
    with db() as con:
        if send and kind == "opening":
            con.execute("INSERT INTO opened(topic, opened_at) VALUES (?, ?) ON CONFLICT(topic) DO NOTHING", (topic, _iso(now)))
        todo = _recipients(con, topic, kind)
    out = {"recipients": len(todo), "sent": 0, "skipped_suppressed": 0, "failed": 0}
    if not send:
        return out
    for a in todo:
        if not mail.can_send(a, KIND):
            out["skipped_suppressed"] += 1
            continue
        with db() as con:
            topics = con.execute("SELECT topics FROM waitlist WHERE address = ?", (a,)).fetchone()
        why = f"you joined the PatentRef waitlist at {base_url()}/waitlist/ for: {topic_names(topics[0] if topics else topic)}."
        try:
            transport(message(a, subject.strip(), text, why))
        except Exception:
            out["failed"] += 1
            continue
        with db() as con:
            if kind == "opening":
                con.execute("INSERT INTO sent(address, topic, kind, at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                            (a, topic, "opening", _iso(now)))
            else:
                con.execute("UPDATE waitlist SET other_notice_at = ? WHERE address = ?", (_iso(now), a))
                con.execute("INSERT INTO sent(address, topic, kind, at) VALUES (?,?,?,?) ON CONFLICT DO NOTHING",
                            (a, topic, "other", _iso(now)))
        out["sent"] += 1
    return out


# ---------------------------------------------------------------- limits

def limit_check(request, address):
    """None when the post may go on, else the answer: 400 for a disposable domain, 429 above the address or domain cap."""
    from lapse_accounts.views import email_domain, is_disposable

    domain = email_domain(address)
    if is_disposable(domain):
        return _page(request, {"state": "error", "error": f"Addresses at disposable mail domains ({domain}) are not accepted."}, 400)
    for scope, subject, seconds, limit in (
            (SCOPE_IP, "ip:" + abuse.client_ip(request), 3600, int(getattr(settings, "LAPSE_WAITLIST_IP_HOURLY", 5))),
            (SCOPE_DOMAIN, "domain:" + domain, 86400, int(getattr(settings, "LAPSE_WAITLIST_DOMAIN_DAILY", 200)))):
        window, wait = ratelimit.minute_window(scope, seconds)
        n = ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", window, limit)
        if n is None:
            resp = _page(request, {"state": "error", "error": "Too many sign-ups from here for now. Try again later."}, 429)
            resp["Retry-After"] = str(wait)
            return resp
        if n == 1:
            ratelimit.drop_old_windows(subject, window)
    return None


# ---------------------------------------------------------------- pages

def _page(request, ctx, status=200, template="lapse_accounts/waitlist.html"):
    from lapse_accounts.views import _data_version

    version, as_of = _data_version()
    opened, opens_on = is_open()
    full = {"data_version": version, "as_of": as_of, "footer_lines": public.footer_lines(), "topics": TOPICS.items(),
            "is_open": opened, "opens_on": opens_on.isoformat() if opens_on else None, "chosen": [],
            "unconfirmed_days": UNCONFIRMED_DAYS, "after_opening_days": AFTER_OPENING_DAYS}
    full.update(ctx)
    resp = HttpResponse(render_to_string(template, full, request=request), status=status, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    if template != "lapse_accounts/waitlist.html":
        resp["X-Robots-Tag"] = "noindex"
    return resp


@csrf_exempt
@require_http_methods(["GET", "POST"])
def page(request):
    if request.method == "GET":
        return _page(request, {"state": "form"})
    opened, _ = is_open()
    if not opened:
        return _page(request, {"state": "closed"}, 403)
    if request.POST.get("company"):   # the honeypot: a person never sees this field
        return _page(request, {"state": "check"})
    address = mail.norm(request.POST.get("email", ""))
    chosen = [t for t in request.POST.getlist("topics") if t in TOPICS]
    if not address or len(address) > 254:
        return _page(request, {"state": "error", "error": "Enter an email address.", "chosen": chosen}, 400)
    if not chosen:
        return _page(request, {"state": "error", "error": "Tick at least one box.", "email": address, "chosen": chosen}, 400)
    refused = limit_check(request, address)
    if refused is not None:
        return refused
    try:
        purge()
        request_confirmation(address, chosen)
    except NoSender:
        return _page(request, {"state": "error", "error": "The waitlist cannot send mail right now. Nothing was stored. Try again later."}, 503)
    except Exception:  # noqa: BLE001 - the relay refused or did not answer
        return _page(request, {"state": "error", "error": "The confirmation mail could not be sent. Nothing was stored. Try again later."}, 503)
    return _page(request, {"state": "check", "email": mail.masked(address)})


@csrf_exempt
@require_http_methods(["GET", "POST"])
def confirm_page(request, token):
    tpl = "lapse_accounts/waitlist_confirm.html"
    found = lookup(token)
    if found is None:
        return _page(request, {"state": "unknown"}, 404, tpl)
    if request.method == "GET":
        return _page(request, {"state": "ask", "address": mail.masked(found[0]), "names": topic_names(found[1]), "token": token}, 200, tpl)
    done = confirm(token)
    if done is None:
        return _page(request, {"state": "unknown"}, 404, tpl)
    return _page(request, {"state": "done", "address": mail.masked(done[0]), "names": topic_names(done[1])}, 200, tpl)
