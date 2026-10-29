"""Wave 4 Q1, the waitlist (lapse_accounts/waitlist.py): one public form with three choices, double opt-in through the Resend
relay, one-click unsubscribe (RFC 8058) on every mail, rows apart from accounts and keys, per-address and per-domain caps
like the sign-up caps, and no mail but the confirmation, one opening notice per topic and at most one other notice.

  python -m pytest lapse_accounts/tests/test_waitlist.py -q -p no:cacheprovider -p no:django
"""
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from email import message_from_string
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command

from lapse_accounts import mail, waitlist
from lapse_accounts.models import AccountKey

FORM = "application/x-www-form-urlencoded"
POLICY_IN_FORCE = "# PatentRef Privacy Policy (free beta)\n\nEffective: 2026-01-01. Version: beta-4. Controller: test.\n"
POLICY_LATER = "# PatentRef Privacy Policy (free beta)\n\nEffective: 2999-01-01. Version: beta-4. Controller: test.\n"


@pytest.fixture
def wl(tmp_path, monkeypatch):
    """A clean waitlist file and mail state file, the policy in force, and every message captured instead of sent."""
    legal = tmp_path / "legal"
    legal.mkdir()
    (legal / "privacy.md").write_text(POLICY_IN_FORCE, encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(legal), raising=False)
    monkeypatch.setattr(settings, "LAPSE_WAITLIST_DB", str(tmp_path / "waitlist.sqlite3"), raising=False)
    monkeypatch.setattr(settings, "LAPSE_EMAIL_DB", str(tmp_path / "email.sqlite3"), raising=False)
    monkeypatch.setattr(settings, "LAPSE_MAIL_ENV_FILE", str(tmp_path / "mail.env"), raising=False)
    sent = []
    monkeypatch.setattr(waitlist, "transport", lambda msg: sent.append(msg))

    class W:
        pass
    w = W()
    w.sent, w.legal, w.tmp = sent, legal, tmp_path
    return w


def ip():
    n = uuid.uuid4().int
    return f"198.18.{n % 250}.{(n >> 8) % 250 + 1}"


def join(client, email, topics=("lapse",), addr=None, **extra):
    data = "&".join([f"email={email}"] + [f"topics={t}" for t in topics] + [f"{k}={v}" for k, v in extra.items()])
    return client.post("/waitlist/", data=data, content_type=FORM, REMOTE_ADDR=addr or ip())


def token_of(msg):
    return re.search(r"/waitlist/confirm/([A-Za-z0-9_-]+)", msg.get_content()).group(1)


def unsub_url(msg):
    """The https URI of the List-Unsubscribe header (the first; the second is the mailto, release W)."""
    return re.findall(r"<([^>]+)>", msg["List-Unsubscribe"])[0]


def unsub_path(msg):
    return unsub_url(msg).replace("https://patentref.io", "")


def rows(w):
    with waitlist.db() as con:
        return con.execute("SELECT address, topics, pending_topics, confirmed_at FROM waitlist ORDER BY address").fetchall()


def addr_mail():
    return f"ann-{uuid.uuid4().hex[:8]}@example.org"


def check_rfc8058(msg):
    lu = msg["List-Unsubscribe"]
    assert lu.startswith("<https://patentref.io/u/") and lu.endswith(">"), lu
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    https, mailto = re.findall(r"<([^>]+)>", lu)                              # release W: the mailto beside the https URL
    assert mailto == "mailto:hello@patentref.io?subject=unsubscribe%20" + https.rsplit("/", 1)[1], lu
    assert https in msg.get_content()
    assert "6640 North Oracle Road" in msg.get_content() and "Why you got this" in msg.get_content()
    assert "\u2014" not in msg.get_content() and "\u2014" not in msg["Subject"]


# ---------------------------------------------------------------- the page and the double opt-in

def test_the_page_has_one_form_with_the_three_choices_and_sets_no_cookie(client, wl):
    r = client.get("/waitlist/")
    assert r.status_code == 200 and not r.cookies
    h = r.content.decode()
    assert h.count("<form") == 1 and 'action="/waitlist/"' in h and 'name="email"' in h
    for key, name in waitlist.TOPICS.items():
        assert f'value="{key}"' in h and name in h
    assert ["Lapse alerts and lists", "Rules updates", "Registry opening"] == list(waitlist.TOPICS.values())
    assert 'class="pr-header"' in h and 'class="pr-footer"' in h and "/docs/legal/privacy" in h and "Resend" in h
    assert "\u2014" not in h


def test_double_opt_in(client, wl):
    a = addr_mail()
    r = join(client, a, ("lapse", "registry"))
    assert r.status_code == 200 and b"Check your inbox" in r.content
    assert len(wl.sent) == 1
    msg = wl.sent[0]
    assert msg["To"] == a and msg["From"] == "PatentRef <noreply@mail.patentref.io>" and "Confirm" in msg["Subject"]
    assert "Lapse alerts and lists, Registry opening" in msg.get_content()
    check_rfc8058(msg)
    assert rows(wl) == [(a, "", "lapse,registry", None)]                      # not on the list until confirmed
    assert waitlist.summary()["confirmed"] == 0
    path = f"/waitlist/confirm/{token_of(msg)}"
    g = client.get(path)                                                     # a GET only shows the button
    assert g.status_code == 200 and b"<form" in g.content and waitlist.summary()["confirmed"] == 0
    assert g["X-Robots-Tag"] == "noindex"
    p = client.post(path)
    assert p.status_code == 200 and b"Confirmed." in p.content
    assert waitlist.summary()["confirmed"] == 1 and waitlist.summary()["per_topic"] == {"lapse": 1, "rules": 0, "registry": 1}
    (address, topics, pending, confirmed_at), = rows(wl)
    assert (address, topics, pending) == (a, "lapse,registry", None) and confirmed_at
    assert client.post(path).status_code == 404                              # a token works once
    assert len(wl.sent) == 1


def test_a_closed_waitlist_stores_nothing_before_the_policy_is_in_force(client, wl):
    (wl.legal / "privacy.md").write_text(POLICY_LATER, encoding="utf-8")
    g = client.get("/waitlist/")
    assert g.status_code == 200 and b"opens on 2999-01-01" in g.content and b"<form" not in g.content
    r = join(client, addr_mail())
    assert r.status_code == 403 and wl.sent == [] and rows(wl) == []


def test_input_rules(client, wl):
    assert join(client, "not-an-address").status_code == 400
    assert join(client, addr_mail(), topics=()).status_code == 400
    assert join(client, addr_mail(), topics=("newsletter",)).status_code == 400
    r = join(client, f"x{uuid.uuid4().hex[:6]}@mailinator.com")
    assert r.status_code == 400 and b"disposable" in r.content
    assert wl.sent == [] and rows(wl) == []


def test_the_honeypot_gets_the_normal_answer_and_nothing_happens(client, wl):
    r = join(client, addr_mail(), company="Acme")
    assert r.status_code == 200 and b"Check your inbox" in r.content and wl.sent == [] and rows(wl) == []


def test_a_second_request_is_held_and_answered_the_same(client, wl):
    a = addr_mail()
    assert join(client, a).status_code == 200 and join(client, a).status_code == 200
    assert len(wl.sent) == 1                                                 # at most one confirmation in 10 minutes
    later = datetime.now(UTC) + timedelta(minutes=11)
    assert waitlist.request_confirmation(a, ["rules"], now=later) == "sent"
    assert waitlist.request_confirmation(a, ["rules"], now=later + timedelta(minutes=11)) == "sent"
    assert waitlist.request_confirmation(a, ["rules"], now=later + timedelta(minutes=22)) == "held"   # 3 a UTC day
    assert len(wl.sent) == 3


def test_no_relay_settings_means_503_and_nothing_stored(client, wl, monkeypatch):
    monkeypatch.setattr(waitlist, "transport", waitlist.smtp_send)          # the real sender, with no mail.env
    r = join(client, addr_mail())
    assert r.status_code == 503 and rows(wl) == []


def test_a_relay_failure_stores_nothing(client, wl, monkeypatch):
    def refuse(msg):
        raise OSError("relay down")
    monkeypatch.setattr(waitlist, "transport", refuse)
    assert join(client, addr_mail()).status_code == 503 and rows(wl) == []


def test_the_relay_settings_are_read_from_the_mail_env_file(wl):
    (wl.tmp / "pw").write_text("not-a-real-key\n", encoding="utf-8")
    (wl.tmp / "mail.env").write_text(f"# the registry's\nREGISTRY_SMTP_HOST=smtp.example.test\nREGISTRY_SMTP_PORT=587\n"
                                     f"export REGISTRY_SMTP_USER=resend\nREGISTRY_SMTP_PASSWORD_FILE=\"{wl.tmp / 'pw'}\"\n"
                                     "REGISTRY_MAIL_FROM=x@mail.patentref.io\n", encoding="utf-8")
    cfg = waitlist.mail_env()
    assert cfg == {"REGISTRY_SMTP_HOST": "smtp.example.test", "REGISTRY_SMTP_PORT": "587", "REGISTRY_SMTP_USER": "resend",
                   "REGISTRY_SMTP_PASSWORD_FILE": str(wl.tmp / "pw")}
    out = StringIO()
    with pytest.raises(SystemExit) as e:
        call_command("waitlist", "check", stdout=out)
    assert e.value.code == 0 and out.getvalue().startswith("WAITLIST MAIL: READY") and "not-a-real-key" not in out.getvalue()
    (wl.tmp / "mail.env").write_text("REGISTRY_SMTP_HOST=smtp.example.test\n", encoding="utf-8")
    with pytest.raises(waitlist.NoSender):
        waitlist.mail_env()


# ---------------------------------------------------------------- rows apart from accounts and keys

def test_rows_are_kept_apart_from_accounts_and_keys(client, wl):
    a = addr_mail()
    before = AccountKey.objects.count()
    join(client, a)
    client.post(f"/waitlist/confirm/{token_of(wl.sent[0])}")
    assert AccountKey.objects.count() == before
    django_db = settings.DATABASES["default"]["NAME"]
    assert str(wl.tmp / "waitlist.sqlite3") != django_db
    with open(django_db, "rb") as f:
        assert a.encode() not in f.read()                                   # the address is nowhere in the account database
    con = sqlite3.connect(django_db)
    try:
        assert not [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'") if "waitlist" in t]
    finally:
        con.close()


# ---------------------------------------------------------------- one-click unsubscribe on every mail

def test_one_click_unsubscribe_leaves_the_list(client, wl):
    a = addr_mail()
    join(client, a, ("rules",))
    client.post(f"/waitlist/confirm/{token_of(wl.sent[0])}")
    path = unsub_path(wl.sent[0])
    assert client.get(path).status_code == 200 and rows(wl)                  # a GET never unsubscribes
    r = client.post(path, data="List-Unsubscribe=One-Click", content_type=FORM)
    assert r.status_code == 200 and r.content == b"unsubscribed\n"
    assert rows(wl) == [] and waitlist.summary()["confirmed"] == 0
    assert waitlist.notify("rules", "opening", "Rules updates are open", "They are open now.", send=True)["sent"] == 0
    assert len(wl.sent) == 1


def test_every_waitlist_mail_carries_the_rfc_8058_headers(client, wl):
    a = addr_mail()
    join(client, a, ("lapse", "rules"))
    client.post(f"/waitlist/confirm/{token_of(wl.sent[0])}")
    waitlist.notify("lapse", "opening", "Lapse alerts are open", "Lapse alerts are open. Sign up at https://patentref.io/.", send=True)
    waitlist.notify("rules", "other", "A short delay", "Rules updates open a week later than planned.", send=True)
    assert len(wl.sent) == 3
    for msg in wl.sent:
        check_rfc8058(msg)
        assert msg["To"] == a
    assert len({m["List-Unsubscribe"] for m in wl.sent}) == 1                # one link per address and kind
    parsed = message_from_string(wl.sent[1].as_string())
    assert parsed["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


# ---------------------------------------------------------------- no mail but the three kinds

def test_mail_is_limited_to_the_confirmation_one_opening_notice_per_topic_and_one_other_notice(client, wl):
    a, b = addr_mail(), addr_mail()
    for x, topics in ((a, ("lapse", "registry")), (b, ("registry",))):
        join(client, x, topics)
        client.post(f"/waitlist/confirm/{token_of(wl.sent[-1])}")
    assert len(wl.sent) == 2
    dry = waitlist.notify("registry", "opening", "The registry is open", "The registry is open.")
    assert dry == {"recipients": 2, "sent": 0, "skipped_suppressed": 0, "failed": 0} and len(wl.sent) == 2
    assert waitlist.summary()["opened"] == {}                                # a dry run records nothing
    assert waitlist.notify("registry", "opening", "The registry is open", "The registry is open.", send=True)["sent"] == 2
    assert waitlist.notify("registry", "opening", "The registry is open", "Again.", send=True)["sent"] == 0   # once per topic
    assert waitlist.notify("lapse", "opening", "Lapse alerts are open", "Open.", send=True)["sent"] == 1
    assert waitlist.notify("registry", "other", "One more thing", "A note.", send=True)["sent"] == 2
    assert waitlist.notify("registry", "other", "Another thing", "A second note.", send=True)["sent"] == 0  # at most one other
    assert waitlist.notify("lapse", "other", "Another thing", "A second note.", send=True)["sent"] == 0     # whatever the topic
    to = [m["To"] for m in wl.sent]
    assert to.count(a) == 4 and to.count(b) == 3     # a: confirmation, two openings, one other; b: confirmation, one opening, one other


def test_suppressed_addresses_get_nothing(client, wl):
    a = addr_mail()
    mail.suppress(a, "hard_bounce", "resend")
    r = join(client, a)
    assert r.status_code == 200 and b"Check your inbox" in r.content and wl.sent == [] and rows(wl) == []
    c = addr_mail()
    join(client, c, ("rules",))
    client.post(f"/waitlist/confirm/{token_of(wl.sent[0])}")
    mail.suppress(c, "complaint", "resend")
    out = waitlist.notify("rules", "opening", "Rules updates are open", "Open.", send=True)
    assert out["sent"] == 0 and out["skipped_suppressed"] == 1 and len(wl.sent) == 1


def test_notice_text_keeps_the_wording_rules(wl):
    with pytest.raises(ValueError):
        waitlist.notify("rules", "other", "Subject", "A note \u2014 with an em-dash.")
    with pytest.raises(ValueError):
        waitlist.notify("rules", "other", "", "Text.")
    with pytest.raises(ValueError):
        waitlist.notify("newsletter", "other", "Subject", "Text.")


def test_the_operator_command_dry_run_sends_nothing(client, wl, tmp_path):
    a = addr_mail()
    join(client, a, ("lapse",))
    client.post(f"/waitlist/confirm/{token_of(wl.sent[0])}")
    body = tmp_path / "body.txt"
    body.write_text("Lapse alerts are open.\n", encoding="utf-8")
    out = StringIO()
    call_command("waitlist", "open", "lapse", "--subject", "Lapse alerts are open", "--body-file", str(body), stdout=out)
    assert out.getvalue().startswith("DRY RUN: 1 recipients") and "List-Unsubscribe-Post" in out.getvalue()
    assert len(wl.sent) == 1 and a not in out.getvalue()
    out = StringIO()
    call_command("waitlist", "summary", stdout=out)
    assert a not in out.getvalue() and '"confirmed": 1' in out.getvalue()


# ---------------------------------------------------------------- retention

def test_purge_applies_the_retention_rules(client, wl):
    keep = addr_mail()
    join(client, keep, ("lapse", "rules"))
    client.post(f"/waitlist/confirm/{token_of(wl.sent[-1])}")
    only = addr_mail()
    join(client, only, ("lapse",))
    client.post(f"/waitlist/confirm/{token_of(wl.sent[-1])}")
    stale = addr_mail()   # never confirmed, 8 days old (after the posts above, which purge too)
    waitlist.request_confirmation(stale, ["lapse"], now=datetime.now(UTC) - timedelta(days=8))
    waitlist.notify("lapse", "opening", "Lapse alerts are open", "Open.", send=True, now=datetime.now(UTC) - timedelta(days=31))
    out = waitlist.purge()
    assert out["unconfirmed"] == 1 and out["choices"] == 2 and out["rows"] == 1
    left = rows(wl)
    assert [r[0] for r in left] == [keep] and left[0][1] == "rules"
    con = sqlite3.connect(str(wl.tmp / "email.sqlite3"))
    try:   # the mail state keeps no unsubscribe link of an address that is off the list
        assert {a for (a,) in con.execute("SELECT address FROM token WHERE kind = 'waitlist'")} == {keep}
    finally:
        con.close()


def test_the_unconfirmed_link_expires_with_the_row(client, wl):
    a = addr_mail()
    waitlist.request_confirmation(a, ["rules"], now=datetime.now(UTC) - timedelta(days=8))
    tok = token_of(wl.sent[0])
    assert client.get(f"/waitlist/confirm/{tok}").status_code == 404
    assert client.post(f"/waitlist/confirm/{tok}").status_code == 404


# ---------------------------------------------------------------- rate limits like the sign-up caps

def test_per_address_cap(client, wl, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_WAITLIST_IP_HOURLY", 2, raising=False)
    here = ip()
    assert join(client, addr_mail(), addr=here).status_code == 200
    assert join(client, addr_mail(), addr=here).status_code == 200
    r = join(client, addr_mail(), addr=here)
    assert r.status_code == 429 and int(r["Retry-After"]) > 0 and len(wl.sent) == 2
    assert join(client, addr_mail()).status_code == 200                     # another address is not affected


def test_per_domain_cap(client, wl, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_WAITLIST_DOMAIN_DAILY", 2, raising=False)
    dom = f"d{uuid.uuid4().hex[:8]}.example"
    assert join(client, f"a@{dom}").status_code == 200 and join(client, f"b@{dom}").status_code == 200
    r = join(client, f"c@{dom}")
    assert r.status_code == 429 and int(r["Retry-After"]) > 0 and len(wl.sent) == 2
    assert join(client, f"a@e{uuid.uuid4().hex[:8]}.example").status_code == 200   # another domain is not affected


def test_purge_deletes_the_cap_counters_once_their_window_is_over(client, wl):
    from django.db import connection

    from lapse_accounts import ratelimit

    here = ip()
    assert join(client, addr_mail(), addr=here).status_code == 200
    with connection.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {ratelimit.WINDOW_TABLE} WHERE subject = %s", ["ip:" + here])
        assert cur.fetchone()[0] == 1
    assert waitlist.purge(now=datetime.now(UTC))["counters"] == 0           # still in its hour: kept
    out = waitlist.purge(now=datetime.now(UTC) + timedelta(days=2))         # every waitlist window is over
    assert out["counters"] >= 2
    with connection.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {ratelimit.WINDOW_TABLE} WHERE win LIKE 'wl-%'")
        assert cur.fetchone()[0] == 0
        cur.execute(f"SELECT COUNT(*) FROM {ratelimit.WINDOW_TABLE} WHERE win NOT LIKE 'wl-%'")
        others = cur.fetchone()[0]
    waitlist.purge(now=datetime.now(UTC) + timedelta(days=2))
    with connection.cursor() as cur:                                         # the API's own throttle rows are untouched
        cur.execute(f"SELECT COUNT(*) FROM {ratelimit.WINDOW_TABLE} WHERE win NOT LIKE 'wl-%'")
        assert cur.fetchone()[0] == others
