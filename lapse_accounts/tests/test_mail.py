"""Gate 5.16: one-click unsubscribe (RFC 8058) and bounce and complaint suppression (lapse_accounts/mail.py)."""
import base64
import hashlib
import hmac
import json
import re
import time
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from lapse_accounts import mail

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()


@pytest.fixture
def mailstate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_EMAIL_DB", str(tmp_path / "email.sqlite3"), raising=False)
    secret_file = tmp_path / "resend_webhook_secret.txt"
    monkeypatch.setattr(settings, "LAPSE_RESEND_WEBHOOK_SECRET_FILE", str(secret_file), raising=False)
    return secret_file


def signed(body, secret=SECRET, msg_id="msg_1", ts=None):
    ts = str(int(ts if ts is not None else time.time()))
    key = base64.b64decode(secret.split("_", 1)[1])
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    return {"HTTP_SVIX_ID": msg_id, "HTTP_SVIX_TIMESTAMP": ts, "HTTP_SVIX_SIGNATURE": f"v1,bogus v1,{sig}"}


def post_event(client, event, **kw):
    body = json.dumps(event).encode()
    return client.post("/api/v1/meta/email/events/", data=body, content_type="application/json", **signed(body, **kw))


def bounce(to, kind="Permanent", sub="General"):
    return {"type": "email.bounced", "created_at": "2026-10-20T00:00:00Z",
            "data": {"email_id": "e1", "to": [to], "bounce": {"type": kind, "subType": sub, "message": "550 no such user"}}}


def uris(header):
    """The URIs of a List-Unsubscribe header, in order (RFC 2369: each in angle brackets, separated by commas)."""
    return re.findall(r"<([^>]+)>", header)


def test_headers_follow_rfc_8058(mailstate):
    msg = mail.prepare("Ann <Ann@Example.org>", "monitor-hit", "US 10000000: a fee event", "One line.")
    h = msg["headers"]
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert h["List-Unsubscribe"].startswith("<https://patentref.io/u/") and h["List-Unsubscribe"].endswith(">")
    assert msg["to"] == ["ann@example.org"] and msg["from"] == "PatentRef <noreply@mail.patentref.io>"
    url = uris(h["List-Unsubscribe"])[0]
    assert url in msg["text"] and "Why you got this" in msg["text"]
    assert mail.unsubscribe_url("ann@example.org", "monitor-hit") == url  # one token per address and kind
    with pytest.raises(ValueError):
        mail.can_send("ann@example.org", "newsletter")


def test_one_click_post_unsubscribes_without_login(client, mailstate):
    url = mail.unsubscribe_url("ann@example.org", "monitor-hit")
    path = url.replace("https://patentref.io", "")
    r = client.get(path)  # a GET only shows the button
    assert r.status_code == 200 and b"a***@example.org" in r.content and b'name="List-Unsubscribe" value="One-Click"' in r.content
    assert mail.can_send("ann@example.org", "monitor-hit")
    r = client.post(path, data="List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 200 and r.content == b"unsubscribed\n"
    assert not mail.can_send("ann@example.org", "monitor-hit")
    assert mail.can_send("ann@example.org", "receipt-issued")  # only that kind
    with pytest.raises(mail.Suppressed):
        mail.prepare("ann@example.org", "monitor-hit", "s", "t")
    r = client.post(path, data="List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 200  # again: still fine


def test_security_kinds_still_send_after_unsubscribe(client, mailstate):
    path = mail.unsubscribe_url("bo@example.org", "key-issued").replace("https://patentref.io", "")
    r = client.post(path, {"List-Unsubscribe": "One-Click", "from_page": "1"})
    assert r.status_code == 200 and b"Done." in r.content and b"still arrives" in r.content
    assert mail.can_send("bo@example.org", "key-issued")


def test_bad_unsubscribe_requests(client, mailstate):
    assert client.post("/u/" + "x" * 32, data="List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded").status_code == 404
    assert client.get("/u/" + "x" * 32).status_code == 404
    path = mail.unsubscribe_url("cy@example.org", "monitor-hit").replace("https://patentref.io", "")
    assert client.post(path, data="", content_type="application/x-www-form-urlencoded").status_code == 400
    assert mail.can_send("cy@example.org", "monitor-hit")


def test_webhook_without_secret_stores_nothing(client, mailstate):
    r = post_event(client, bounce("dee@example.org"))
    assert r.status_code == 503 and "not installed" in r.json()["reason"]
    assert not mail.is_suppressed("dee@example.org")


def test_hard_bounce_is_suppressed_on_the_next_send(client, mailstate):
    mailstate.write_text(SECRET + "\n", encoding="utf-8")
    assert mail.can_send("dee@example.org", "monitor-hit")
    r = post_event(client, bounce("Dee <DEE@example.org>"))
    assert r.status_code == 200 and r.json() == {"ok": True, "type": "email.bounced", "suppressed": 1}
    assert not mail.can_send("dee@example.org", "monitor-hit") and not mail.can_send("dee@example.org", "key-issued")
    with pytest.raises(mail.Suppressed):
        mail.prepare("dee@example.org", "key-issued", "s", "t")
    r = post_event(client, bounce("dee@example.org"))  # the same svix-id again
    assert r.json() == {"ok": True, "duplicate": True}


def test_complaint_and_resend_list_and_soft_bounces(client, mailstate):
    mailstate.write_text(SECRET, encoding="utf-8")
    assert post_event(client, {"type": "email.complained", "data": {"to": ["eve@example.org"]}}, msg_id="m2").json()["suppressed"] == 1
    assert mail.is_suppressed("eve@example.org")
    assert post_event(client, {"type": "suppression.added", "data": {"email": "fay@example.org", "reason": "bounce"}}, msg_id="m3").json()["suppressed"] == 1
    for i in range(2):
        assert post_event(client, bounce("gus@example.org", "Temporary", "MailboxFull"), msg_id=f"s{i}").json()["suppressed"] == 0
    assert mail.can_send("gus@example.org", "monitor-hit")
    assert post_event(client, bounce("gus@example.org", "Temporary", "MailboxFull"), msg_id="s3").json()["suppressed"] == 1
    assert not mail.can_send("gus@example.org", "monitor-hit")
    assert post_event(client, {"type": "email.delivered", "data": {"to": ["hal@example.org"]}}, msg_id="m4").json()["suppressed"] == 0


def test_signature_checks(client, mailstate):
    mailstate.write_text(SECRET, encoding="utf-8")
    body = json.dumps(bounce("ivy@example.org")).encode()
    h = signed(body)
    h["HTTP_SVIX_SIGNATURE"] = "v1,AAAA"
    assert client.post("/api/v1/meta/email/events/", data=body, content_type="application/json", **h).status_code == 401
    old = signed(body, ts=time.time() - 3600)
    assert client.post("/api/v1/meta/email/events/", data=body, content_type="application/json", **old).status_code == 401
    other = signed(body, secret="whsec_" + base64.b64encode(b"another key of 32 bytes........").decode())
    assert client.post("/api/v1/meta/email/events/", data=body, content_type="application/json", **other).status_code == 401
    assert client.post("/api/v1/meta/email/events/", data=body, content_type="application/json").status_code == 400
    assert client.get("/api/v1/meta/email/events/").status_code == 405
    assert not mail.is_suppressed("ivy@example.org")
    tampered = body.replace(b"ivy@", b"ivx@")
    assert client.post("/api/v1/meta/email/events/", data=tampered, content_type="application/json", **signed(body)).status_code == 401


def test_svix_reference_vector():
    """The worked example of Svix's documentation on verifying webhooks by hand (its example key, id, timestamp and body
    give the example signature), so the scheme here is Svix's and not only consistent with itself."""
    example = "whsec_" + "MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"  # Svix's published example, not a key of ours
    ok, why = mail.verify(example, "msg_p5jXN8AQM9LWM0D4loKWxJek", "1614265330",
                          b'{"test": 2432232314}', "v1," + "g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE=", now=1614265330)
    assert ok, why


# ---------------------------------------------------------------- release W (independent audit 11, finding F5): mailto beside https

def test_list_unsubscribe_names_the_https_url_and_a_mailto_with_the_same_token(mailstate):
    h = mail.unsubscribe_headers("Kim <kim@example.org>", "outreach")
    https, mailto = uris(h["List-Unsubscribe"])
    token = https.rsplit("/", 1)[1]
    assert https == f"https://patentref.io/u/{token}" and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", token)
    assert mailto == f"mailto:hello@patentref.io?subject=unsubscribe%20{token}"
    assert h["List-Unsubscribe"] == f"<{https}>, <{mailto}>"                  # RFC 8058: the one-click https URI is there
    assert h["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    assert mail.unsubscribe_headers("kim@example.org", "outreach") == h       # one token per address and kind


def test_outreach_mail_takes_the_same_path_with_its_own_reason(mailstate):
    msg = mail.message("kim@example.org", "outreach", "PatentRef is open", "One line.", why="you asked for PatentRef news.")
    assert msg["To"] == "kim@example.org" and msg["From"] == mail.FROM and msg["Message-ID"] and msg["Date"]
    https, mailto = uris(msg["List-Unsubscribe"])
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click" and mailto.startswith("mailto:hello@patentref.io?")
    body = msg.get_content()
    assert "Why you got this: you asked for PatentRef news." in body and https in body and "—" not in body
    mail.suppress("lou@example.org", "hard_bounce", "resend")
    with pytest.raises(mail.Suppressed):
        mail.message("lou@example.org", "outreach", "s", "t")


def test_the_one_click_post_records_the_unsubscribe_for_outreach(client, mailstate):
    https = uris(mail.unsubscribe_headers("max@example.org", "outreach")["List-Unsubscribe"])[0]
    r = client.post(https.replace("https://patentref.io", ""), data="List-Unsubscribe=One-Click", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 200 and r.content == b"unsubscribed\n"
    assert not mail.can_send("max@example.org", "outreach") and mail.can_send("max@example.org", "monitor-hit")
    with pytest.raises(mail.Suppressed):
        mail.message("max@example.org", "outreach", "s", "t")


def test_a_mailto_request_is_recorded_by_the_operator_command(mailstate):
    h = mail.unsubscribe_headers("ned@example.org", "monitor-hit")
    token = uris(h["List-Unsubscribe"])[1].rsplit("%20", 1)[1]
    out = StringIO()
    call_command("mail_unsubscribe", f"unsubscribe {token}", stdout=out)            # the subject as the mail has it
    assert out.getvalue().strip() == "UNSUBSCRIBED: n***@example.org from monitor alerts" and "ned@" not in out.getvalue()
    assert not mail.can_send("ned@example.org", "monitor-hit")
    call_command("mail_unsubscribe", f"https://patentref.io/u/{token}", stdout=StringIO())   # the URL works too, again
    with pytest.raises(CommandError):
        call_command("mail_unsubscribe", "unsubscribe " + "z" * 32, stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("mail_unsubscribe", "nothing", stdout=StringIO())
