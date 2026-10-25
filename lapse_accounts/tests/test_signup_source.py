"""Wave 4 Q1, the sign-up source count (lapse_accounts/sources.py): a campaign code from the sign-up link adds one to a weekly
row (ISO week, code, count) when POST /api/v1/meta/keys/ creates a key; codes outside the rule are dropped; nothing is
stored on the account or the key; it counts only once Privacy Policy beta-4 is in force.

  python -m pytest lapse_accounts/tests/test_signup_source.py -q -p no:cacheprovider
"""
import json
import shutil
import sqlite3
import subprocess
import uuid
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.template.loader import render_to_string

from API.models import APIUserKey
from lapse_accounts import sources
from lapse_accounts.models import AccountKey, SignupSource

POLICY_IN_FORCE = "# PatentRef Privacy Policy (free beta)\n\nEffective: 2026-01-01. Version: beta-4. Controller: test.\n"
POLICY_LATER = "# PatentRef Privacy Policy (free beta)\n\nEffective: 2999-01-01. Version: beta-4. Controller: test.\n"
POLICY_OLD = "# PatentRef Privacy Policy (free beta)\n\nEffective: 2026-01-01. Version: beta-3. Controller: test.\n"


@pytest.fixture
def policy(tmp_path, monkeypatch):
    d = tmp_path / "legal"
    d.mkdir()
    monkeypatch.setattr(settings, "LAPSE_LEGAL_DIR", str(d), raising=False)

    def write(text):
        (d / "privacy.md").write_text(text, encoding="utf-8")
    write(POLICY_IN_FORCE)
    return write


def mint(client, token_factory, body, sub=None):
    sub = sub or str(uuid.uuid4())
    token = token_factory(sub=sub, email=f"src-{sub[:8]}@example.test")
    data = {"terms_version": settings.LAPSE_TERMS_VERSION, **body}
    r = client.post("/api/v1/meta/keys/", data=json.dumps(data), content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token,
                    REMOTE_ADDR=addr())  # a fresh address each time: the account endpoints allow 30 a minute per address
    return r, sub, token


def addr():
    n = uuid.uuid4().int
    return f"10.{n % 250}.{(n >> 8) % 250}.{(n >> 16) % 250 + 1}"


def week_count(code):
    row = SignupSource.objects.filter(week=sources.iso_week(), code=code).first()
    return row.count if row else 0


@pytest.mark.parametrize("code", ["hn", "launch-2026", "A1-b2", "x" * 32, "-", "Reddit"])
def test_codes_inside_the_rule_are_kept(code):
    assert sources.clean(code) == code


@pytest.mark.parametrize("code", ["", "x" * 33, "a_b", "a b", "a.b", "hn?x=1", "<script>", "café", "a/b", None, 5, ["hn"], {"x": 1}])
def test_codes_outside_the_rule_are_dropped(code):
    assert sources.clean(code) is None


def test_iso_week():
    assert sources.iso_week(datetime(2026, 11, 17, 12, tzinfo=timezone.utc)) == "2026-W47"
    assert sources.iso_week(datetime(2027, 1, 1, tzinfo=timezone.utc)) == "2026-W53"   # ISO week-year, not the calendar year


def test_one_per_created_key(client, token_factory, policy):
    code = "q1-" + uuid.uuid4().hex[:8]
    for n in (1, 2, 3):
        r, _, _ = mint(client, token_factory, {"source": code})
        assert r.status_code == 201, r.content
        assert week_count(code) == n


def test_no_count_without_a_created_key(client, token_factory, policy):
    code = "q1-" + uuid.uuid4().hex[:8]
    r, sub, token = mint(client, token_factory, {"source": code})
    assert r.status_code == 201 and week_count(code) == 1
    again, _, _ = mint(client, token_factory, {"source": code}, sub=sub)        # the key exists: 200, nothing created
    assert again.status_code == 200 and week_count(code) == 1
    rot, _, _ = mint(client, token_factory, {"source": code, "action": "rotate"}, sub=sub)   # a rotation is not a sign-up
    assert rot.status_code == 201 and week_count(code) == 1
    refused = client.post("/api/v1/meta/keys/", data=json.dumps({"source": code}), content_type="application/json",
                          HTTP_AUTHORIZATION="Bearer " + token_factory(email="new-" + uuid.uuid4().hex[:6] + "@example.test"),
                          REMOTE_ADDR=addr())
    assert refused.status_code == 403 and week_count(code) == 1                   # the terms gate refused: no key, no count


@pytest.mark.parametrize("bad", ["x" * 33, "a_b", "<b>", "hn hn", 7])
def test_a_code_outside_the_rule_counts_nothing_and_the_key_is_minted(client, token_factory, policy, bad):
    before = SignupSource.objects.count()
    r, _, _ = mint(client, token_factory, {"source": bad})
    assert r.status_code == 201
    assert SignupSource.objects.count() == before


def test_the_account_and_the_key_carry_no_code(client, token_factory, policy):
    code = "q1-" + uuid.uuid4().hex[:8]
    r, sub, _ = mint(client, token_factory, {"source": code})
    assert r.status_code == 201
    assert code not in json.dumps(r.json())
    assert not [f for f in AccountKey._meta.get_fields() if "source" in f.name or "utm" in f.name or "campaign" in f.name]
    acct = AccountKey.objects.get(supabase_user_id=sub)
    values = [str(getattr(acct, f.attname)) for f in AccountKey._meta.concrete_fields]
    key = APIUserKey.objects.get(pk=acct.api_key_id)
    values += [str(getattr(key, f.attname)) for f in APIUserKey._meta.concrete_fields]
    assert not [v for v in values if code in v]
    # the only place the code exists in the whole account database is its weekly row
    con = sqlite3.connect(settings.DATABASES["default"]["NAME"])
    try:
        hits = []
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            cols = [c[1] for c in con.execute(f'PRAGMA table_info("{table}")')]
            for col in cols:
                n = con.execute(f'SELECT COUNT(*) FROM "{table}" WHERE CAST("{col}" AS TEXT) LIKE ?', (f"%{code}%",)).fetchone()[0]
                if n:
                    hits.append((table, col, n))
    finally:
        con.close()
    assert hits == [("lapse_accounts_signupsource", "code", 1)]


def test_nothing_counts_before_the_policy_is_in_force(client, token_factory, policy):
    code = "q1-" + uuid.uuid4().hex[:8]
    policy(POLICY_LATER)
    assert mint(client, token_factory, {"source": code})[0].status_code == 201 and week_count(code) == 0
    policy(POLICY_OLD)
    assert mint(client, token_factory, {"source": code})[0].status_code == 201 and week_count(code) == 0
    policy(POLICY_IN_FORCE)
    assert mint(client, token_factory, {"source": code})[0].status_code == 201 and week_count(code) == 1


def test_a_failing_count_never_fails_the_sign_up(client, token_factory, policy, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the counter table is not there")
    monkeypatch.setattr(sources, "count", boom)
    r, _, _ = mint(client, token_factory, {"source": "hn"})
    assert r.status_code == 201 and r.json()["api_key"]


def test_operator_read_out(client, token_factory, policy):
    code = "q1-" + uuid.uuid4().hex[:8]
    for _ in range(2):
        mint(client, token_factory, {"source": code})
    out = StringIO()
    call_command("signup_sources", "--weeks", "2", stdout=out)
    text = out.getvalue()
    assert text.splitlines()[0].split() == ["week", "code", "count"]
    line = [ln for ln in text.splitlines() if code in ln][0]
    assert line.split() == [sources.iso_week(), code, "2"]
    out = StringIO()
    call_command("signup_sources", "--json", stdout=out)
    data = json.loads(out.getvalue())
    assert data["counting"] is True and {"week": sources.iso_week(), "code": code, "count": 2} in data["rows"]


def test_the_page_reads_the_code_and_carries_it_through_the_confirmation():
    html = render_to_string("lapse_accounts/signup.html", {"page_url": "https://patentref.io/api/v1/meta/signup/", "terms_version": "beta-2",
                                                           "supabase_url": "https://x.supabase.co", "supabase_anon_key": "anon"})
    assert 'get("utm_source")' in html and "/^[A-Za-z0-9-]{1,32}$/" in html
    assert "emailRedirectTo: RETURN_URL" in html and 'PAGE_URL + (SOURCE ? "?utm_source=" + encodeURIComponent(SOURCE) : "")' in html
    assert 'if (action === "create" && SOURCE) { body.source = SOURCE; }' in html
    assert "localStorage" not in html.split("var SOURCE")[1].split("var RETURN_URL")[0]   # nothing kept in the browser


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_the_page_script_keeps_only_codes_inside_the_rule(tmp_path):
    """The page's own SOURCE and RETURN_URL lines, run in node against several addresses."""
    html = render_to_string("lapse_accounts/signup.html", {"page_url": "https://patentref.io/api/v1/meta/signup/", "terms_version": "beta-2"})
    block = "var SOURCE" + html.split("var SOURCE", 1)[1].split("var RETURN_URL", 1)[0]
    ret = "var RETURN_URL" + html.split("var RETURN_URL", 1)[1].split("\n", 1)[0]
    cases = {"?utm_source=hn": ("hn", "?utm_source=hn"), "?utm_source=launch-2026&x=1": ("launch-2026", "?utm_source=launch-2026"),
             "?utm_source=" + "x" * 33: ("", ""), "?utm_source=a_b": ("", ""), "?utm_source=%3Cb%3E": ("", ""), "": ("", ""),
             "?utm_source=a%20b": ("", ""), "?utm_source=ok&utm_source=second": ("ok", "?utm_source=ok")}
    script = tmp_path / "t.js"
    script.write_text("var out = {}; var PAGE_URL = 'https://patentref.io/api/v1/meta/signup/';\n" + "".join(
        f"(function(){{ var window = {{ location: {{ search: {json.dumps(q)} }} }}; {block} {ret} out[{json.dumps(q)}] = [SOURCE, RETURN_URL]; }})();\n"
        for q in cases) + "console.log(JSON.stringify(out));\n", encoding="utf-8")
    got = json.loads(subprocess.run(["node", str(script)], capture_output=True, text=True, check=True).stdout)
    for q, (code, suffix) in cases.items():
        assert got[q] == [code, "https://patentref.io/api/v1/meta/signup/" + suffix], q


def test_the_migration_adds_one_table_and_touches_nothing_else():
    text = (Path(__file__).resolve().parent.parent / "migrations" / "0004_signupsource.py").read_text(encoding="utf-8")
    assert text.count("migrations.CreateModel(") == 1
    for op in ("AddField", "AlterField", "RemoveField", "RunSQL", "RunPython", "DeleteModel", "RenameField"):
        assert op not in text, op
