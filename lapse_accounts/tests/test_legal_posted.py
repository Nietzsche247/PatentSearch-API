"""Release wave4-Q: a legal document version the repo writes with {{posted}} and {{effective}} is dated by the deploy. The
posted date is the day the deploy recorded (manage.py legal_posted --stamp), the effective date 14 days after it, and
until the deploy has recorded it the text shows today, the effective date stays 14 days ahead and nothing switches on.

  python -m pytest lapse_accounts/tests/test_legal_posted.py -q -p no:cacheprovider -p no:django
"""
import json
import os
import uuid
from datetime import date, timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from lapse_accounts import legal, waitlist

DATED = ("# PatentRef Privacy Policy (free beta)\n\nEffective: {{effective}}. Version: beta-4. Posted: {{posted}}; until the "
         "effective date, version beta-3 applies. Controller: test.\n\nWhat is new in beta-4: a waitlist.\n\nThe rest.\n")
REGISTER = "# Processors\n\nEffective: {{effective}}. Version: beta-4. Part of the Privacy Policy.\n"
D = date(2026, 10, 28)


@pytest.fixture
def docs(tmp_path, monkeypatch):
    d = tmp_path / "legal"
    d.mkdir()
    (d / "privacy.md").write_text(DATED, encoding="utf-8")
    (d / "processors.md").write_text(REGISTER, encoding="utf-8")
    (d / "terms.md").write_text("# Terms\n\nEffective: 2026-10-20. Version: beta-2. Operator: test.\n", encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(d), raising=False)
    monkeypatch.setattr(settings, "LAPSE_LEGAL_POSTED_FILE", str(tmp_path / "data" / "legal_posted.json"), raising=False)
    monkeypatch.setattr(settings, "LAPSE_EMAIL_DB", str(tmp_path / "email.sqlite3"), raising=False)
    sent = []
    monkeypatch.setattr(waitlist, "transport", lambda m: sent.append(m))
    return tmp_path, sent


def test_until_the_deploy_records_it_the_text_shows_today_and_nothing_switches_on(docs):
    for day in (D, D + timedelta(days=20), date(2027, 3, 1)):
        md = legal.read_doc("privacy", day)
        assert f"Effective: {(day + timedelta(days=14)).isoformat()}. Version: beta-4. Posted: {day.isoformat()};" in md
        assert "{{" not in md
        assert legal.in_force("privacy", 4, today=day) == (False, day + timedelta(days=14))
        up = legal.upcoming(today=day)
        assert [(u["name"], u["stamped"], u["posted"]) for u in up] == [("privacy", False, day.isoformat()), ("processors", False, day.isoformat())]
    assert not Path(settings.LAPSE_LEGAL_POSTED_FILE).exists()      # reading never records anything
    assert legal.read_doc("terms").startswith("# Terms\n\nEffective: 2026-10-20.")   # written dates are served as written


def test_the_deploy_records_the_day_once_and_the_switch_on_follows_14_days_later(docs):
    got = legal.stamp(today=D)
    assert [(p["name"], p["version"], p["posted"], p["effective"], p["stamped"]) for p in got] == [
        ("privacy", "beta-4", D, date(2026, 11, 11), True), ("processors", "beta-4", D, date(2026, 11, 11), True)]
    rec = json.loads(Path(settings.LAPSE_LEGAL_POSTED_FILE).read_text())
    assert rec == {"privacy": {"beta-4": "2026-10-28"}, "processors": {"beta-4": "2026-10-28"}}
    assert oct(os.stat(settings.LAPSE_LEGAL_POSTED_FILE).st_mode & 0o777) == "0o644"
    legal.stamp(today=D + timedelta(days=5))                          # a later deploy never moves a recorded date
    assert json.loads(Path(settings.LAPSE_LEGAL_POSTED_FILE).read_text()) == rec
    for day in (D, D + timedelta(days=13)):
        assert "Effective: 2026-11-11. Version: beta-4. Posted: 2026-10-28;" in legal.read_doc("privacy", day)
        assert legal.in_force("privacy", 4, today=day) == (False, date(2026, 11, 11))
        assert waitlist.is_open(today=day) == (False, date(2026, 11, 11))
    assert legal.in_force("privacy", 4, today=date(2026, 11, 11)) == (True, date(2026, 11, 11))
    assert legal.upcoming(today=date(2026, 11, 11)) == []
    assert legal.read_doc("processors", D).startswith("# Processors\n\nEffective: 2026-11-11. Version: beta-4.")


def test_a_new_version_gets_its_own_day_and_unstamp_forgets_one(docs):
    tmp, _ = docs
    legal.stamp(today=D)
    (Path(settings.LAPSE_LEGAL_DIR) / "privacy.md").write_text(DATED.replace("beta-4", "beta-5"), encoding="utf-8")
    later = date(2027, 1, 4)
    legal.stamp(today=later)
    assert legal.read_stamps()["privacy"] == {"beta-4": "2026-10-28", "beta-5": "2027-01-04"}
    assert legal.unstamp("privacy", "beta-5") is True and legal.unstamp("privacy", "beta-5") is False
    assert legal.read_stamps()["privacy"] == {"beta-4": "2026-10-28"}
    assert legal.posting("privacy", today=later)["stamped"] is False


def test_the_pages_and_the_markdown_twin_show_the_recorded_dates(client, docs):
    legal.stamp(today=D)
    for path in ("/docs/legal/privacy", "/docs/legal/privacy.md", "/docs/legal/processors.md", "/docs/legal/"):
        body = client.get(path).content.decode()
        assert "{{" not in body and "2026-11-11" in body, path
    assert "Posted: 2026-10-28" in client.get("/docs/legal/privacy.md").content.decode()
    h = client.get("/api/v1/meta/status/").content.decode()
    assert "Coming change: Privacy Policy" in h and "takes effect on 2026-11-11" in h


def test_the_command_prints_waits_and_records(docs):
    out = StringIO()
    call_command("legal_posted", stdout=out)
    lines = out.getvalue().splitlines()
    assert lines[-1].startswith("LEGAL POSTED: WAITING (2 document(s)")
    assert any(x.startswith("privacy beta-4: posted ") and "not recorded yet" in x for x in lines)
    assert not Path(settings.LAPSE_LEGAL_POSTED_FILE).exists()
    out = StringIO()
    call_command("legal_posted", "--stamp", stdout=out)
    assert out.getvalue().splitlines()[-1].startswith("LEGAL POSTED: PASS (2 document(s)")
    out = StringIO()
    call_command("legal_posted", "--json", stdout=out)
    j = json.loads(out.getvalue())
    assert j["ok"] is True and {r["doc"] for r in j["documents"]} == {"privacy", "processors"}
    assert all(r["recorded"] and (date.fromisoformat(r["effective"]) - date.fromisoformat(r["posted"])).days == 14 for r in j["documents"])
    out = StringIO()
    call_command("legal_posted", "--unstamp", "processors", "beta-4", stdout=out)
    assert "recorded date removed" in out.getvalue() and out.getvalue().splitlines()[-1].startswith("LEGAL POSTED: WAITING")
    with pytest.raises(CommandError):
        call_command("legal_posted", "--unstamp", "nope", "beta-1", stdout=StringIO())


def test_the_email_notice_waits_for_the_recorded_date(client, docs, token_factory):
    _, sent = docs
    sub = str(uuid.uuid4())
    r = client.post("/api/v1/meta/keys/", data=json.dumps({"terms_version": settings.LAPSE_TERMS_VERSION}), content_type="application/json",
                    HTTP_AUTHORIZATION="Bearer " + token_factory(sub=sub, email=f"posted-{sub[:8]}@example.test"),
                    REMOTE_ADDR=f"10.8.{uuid.uuid4().int % 250}.9")
    assert r.status_code == 201
    with pytest.raises(CommandError, match="legal_posted --stamp"):
        call_command("legal_notice", "privacy", stdout=StringIO())
    legal.stamp()
    out = StringIO()
    call_command("legal_notice", "privacy", stdout=out)
    eff = (date.fromisoformat(legal.read_stamps()["privacy"]["beta-4"]) + timedelta(days=14)).isoformat()
    assert f"Subject: PatentRef Privacy Policy: version beta-4 takes effect on {eff}" in out.getvalue() and sent == []


def _real_legal_dir():
    for c in (os.environ.get("LAPSE_REPO_DIR"), str(Path(__file__).resolve().parents[3] / "patentref"), "/srv/lapse"):
        if c and (Path(c) / "docs" / "legal" / "privacy.md").exists():
            return Path(c) / "docs" / "legal"
    return None


def test_the_repo_dates_privacy_beta_4_and_its_register_by_the_deploy(docs, monkeypatch):
    real = _real_legal_dir()
    if real is None:
        pytest.skip("no patentref checkout (LAPSE_REPO_DIR)")
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(real), raising=False)
    if legal.posting("privacy") is None:
        pytest.skip("this patentref checkout writes the privacy dates by hand (before release wave4-Q)")
    for name in ("privacy", "processors"):
        raw = legal.raw_doc(name)
        assert raw.split("\n\n")[1].startswith("Effective: {{effective}}. Version: beta-4."), name
    assert "Posted: {{posted}}; until the effective date, version beta-3 applies." in legal.raw_doc("privacy")
    got = {p["name"]: p for p in legal.stamp(today=D)}
    assert set(got) == {"privacy", "processors"} and got["privacy"]["effective"] == got["processors"]["effective"] == date(2026, 11, 11)
    assert waitlist.is_open(today=date(2026, 11, 10)) == (False, date(2026, 11, 11))
    assert waitlist.is_open(today=date(2026, 11, 11)) == (True, date(2026, 11, 11))
    for name in legal.ORDER:
        assert "{{" not in (legal.read_doc(name) or ""), name
