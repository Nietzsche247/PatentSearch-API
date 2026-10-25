"""Wave 4 Q1: a legal document posted ahead of its effective date is announced on the status page and by email to account
holders (manage.py legal_notice), as the Privacy Policy and the terms promise (14 days before a material change).

  python -m pytest lapse_accounts/tests/test_policy_notice.py -q -p no:cacheprovider -p no:django
"""
import json
import uuid
from datetime import date
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from lapse_accounts import legal, mail, waitlist

POSTED = ("# PatentRef Privacy Policy (free beta)\n\nEffective: 2999-01-01. Version: beta-4. Posted: 2026-10-28; until the effective "
          "date, version beta-3 applies. Controller: test.\n\nWhat is new in beta-4: a waitlist; weekly visit counts.\n\nThe rest.\n")
IN_FORCE = POSTED.replace("2999-01-01", "2026-01-01")


@pytest.fixture
def docs(tmp_path, monkeypatch):
    d = tmp_path / "legal"
    d.mkdir()
    (d / "privacy.md").write_text(POSTED, encoding="utf-8")
    (d / "terms.md").write_text("# Terms\n\nEffective: 2026-10-20. Version: beta-2. Operator: test.\n", encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(d), raising=False)
    monkeypatch.setattr(settings, "LAPSE_EMAIL_DB", str(tmp_path / "email.sqlite3"), raising=False)
    sent = []
    monkeypatch.setattr(waitlist, "transport", lambda m: sent.append(m))
    return d, sent


def test_upcoming_lists_only_documents_posted_ahead(docs):
    d, _ = docs
    up = legal.upcoming(today=date(2026, 10, 28))
    assert [(u["name"], u["version"], u["effective"], u["summary"]) for u in up] == [("privacy", "beta-4", "2999-01-01", "a waitlist; weekly visit counts.")]
    (d / "privacy.md").write_text(IN_FORCE, encoding="utf-8")
    assert legal.upcoming(today=date(2026, 10, 28)) == []
    assert legal.in_force("privacy", 4, today=date(2026, 10, 28)) == (True, date(2026, 1, 1))
    assert legal.in_force("privacy", 5, today=date(2026, 10, 28))[0] is False


def test_the_status_page_announces_the_coming_change(client, docs):
    d, _ = docs
    h = client.get("/api/v1/meta/status/").content.decode()
    assert "Coming change: Privacy Policy" in h and "takes effect on 2999-01-01" in h and "What is new: a waitlist; weekly visit counts." in h
    (d / "privacy.md").write_text(IN_FORCE, encoding="utf-8")
    assert "Coming change" not in client.get("/api/v1/meta/status/").content.decode()


def test_the_email_notice_goes_once_to_each_account_and_never_to_a_suppressed_one(client, docs, token_factory):
    _, sent = docs
    mine = []
    for _ in range(2):
        sub = str(uuid.uuid4())
        email = f"notice-{sub[:8]}@example.test"
        r = client.post("/api/v1/meta/keys/", data=json.dumps({"terms_version": settings.LAPSE_TERMS_VERSION}), content_type="application/json",
                        HTTP_AUTHORIZATION="Bearer " + token_factory(sub=sub, email=email), REMOTE_ADDR=f"10.9.{uuid.uuid4().int % 250}.7")
        assert r.status_code == 201
        mine.append(email)
    mail.suppress(mine[1], "hard_bounce", "resend")
    out = StringIO()
    call_command("legal_notice", "privacy", stdout=out)
    assert out.getvalue().startswith("DRY RUN:") and "Subject: PatentRef Privacy Policy: version beta-4 takes effect on 2999-01-01" in out.getvalue()
    assert sent == []
    call_command("legal_notice", "privacy", "--send", stdout=StringIO())
    to = [m["To"] for m in sent]
    assert mine[0] in to and mine[1] not in to and len(to) == len(set(to))
    msg = [m for m in sent if m["To"] == mine[0]][0]
    assert msg["List-Unsubscribe"] is None                                  # a notice the terms require: no unsubscribe link
    body = msg.get_content()
    assert "https://patentref.io/docs/legal/privacy" in body and "What is new: a waitlist" in body and "\u2014" not in body
    n = len(sent)
    call_command("legal_notice", "privacy", "--send", stdout=StringIO())
    assert len(sent) == n                                                   # once per address and version


def test_nothing_to_announce_once_in_force(docs):
    d, _ = docs
    (d / "privacy.md").write_text(IN_FORCE, encoding="utf-8")
    with pytest.raises(CommandError):
        call_command("legal_notice", "privacy", stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("legal_notice", "terms", stdout=StringIO())
