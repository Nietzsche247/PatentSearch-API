"""The rules layer on REST (PatentRef checklist 11.3): GET /api/v1/rules/ and one path per tool, read through the
patentref clone's engine `lapse.rules` (LAPSE_REPO_DIR when set, else a sibling checkout named lapse, patentref,
pr or w4rules, or /srv/lapse, that holds lapse/rules.py). Every answer is checked against the repo's
rules/response.schema.json and rules/schema.json and for the banned strings; the refused inputs answer 400 ERR_Q.

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_accounts/tests/test_rules.py -q -p no:cacheprovider
"""
import json
import os
from pathlib import Path

import pytest
from django.conf import settings

from API import lapse_rules, lapse_wording
from API.models import APIUserKey
from lapse_accounts.tests.conftest import _TMP  # noqa: F401  (the conftest's Django setup)

ROOT = Path(__file__).resolve().parents[2]


def find_repo_dir():
    if os.environ.get("LAPSE_REPO_DIR") and (Path(os.environ["LAPSE_REPO_DIR"]) / "lapse" / "rules.py").is_file():
        return os.environ["LAPSE_REPO_DIR"]
    for cand in (ROOT.parent / "lapse", ROOT.parent / "patentref", ROOT.parent / "pr", ROOT.parent / "w4rules", Path("/srv/lapse")):
        if (cand / "lapse" / "rules.py").is_file():
            return str(cand)
    return None


REPO_DIR = find_repo_dir()
PATHS = [
    ("/api/v1/rules/", {}),
    ("/api/v1/rules/rep_required/", {"office": "EP"}),
    ("/api/v1/rules/rep_required/", {"office": "US", "act": "prosecute"}),
    ("/api/v1/rules/fee_schedule/", {"office": "JP"}),
    ("/api/v1/rules/fee_schedule/", {"office": "EP", "as_of": "2026-10-20"}),
    ("/api/v1/rules/route_windows/", {"office": "AU"}),
    ("/api/v1/rules/pph_routes/", {"from_office": "EP", "to_office": "US"}),
    ("/api/v1/rules/ai_use_statement_required/", {"office": "US"}),
    ("/api/v1/rules/grace_period/", {"office": "US"}),
    ("/api/v1/rules/changelog/", {"since": "0.1.0"}),
]


@pytest.fixture
def rules_env():
    if not REPO_DIR:
        pytest.fail("the rules engine lapse.rules lives in the patentref repo: set LAPSE_REPO_DIR to a patentref checkout "
                    "that has lapse/rules.py", pytrace=False)
    old = getattr(settings, "LAPSE_REPO_DIR", None)
    settings.LAPSE_REPO_DIR = REPO_DIR
    lapse_rules._local.__dict__.clear()
    try:
        yield REPO_DIR
    finally:
        settings.LAPSE_REPO_DIR = old
        lapse_rules._local.__dict__.clear()


@pytest.fixture
def opkey():
    _, key = APIUserKey.objects.create_key(name="rules-op", username="rules-op-" + os.urandom(4).hex(), email="op-rules@example.test")
    return key


def _validators(repo):
    import jsonschema
    from referencing import Registry, Resource

    row = json.loads((Path(repo) / "rules" / "schema.json").read_text(encoding="utf-8"))
    resp = json.loads((Path(repo) / "rules" / "response.schema.json").read_text(encoding="utf-8"))
    reg = Registry().with_resources([(row["$id"], Resource.from_contents(row)), (resp["$id"], Resource.from_contents(resp))])
    return jsonschema.Draft202012Validator(resp, registry=reg), jsonschema.Draft202012Validator(row)


def test_every_rules_path_answers_the_envelope(client, opkey, rules_env):
    resp_v, row_v = _validators(rules_env)
    versions = set()
    for path, params in PATHS:
        r = client.get(path, params, HTTP_X_API_KEY=opkey)
        assert r.status_code == 200, (path, r.status_code, r.headers.get("X-Status-Reason"))
        body = r.json()
        assert not [e.message for e in resp_v.iter_errors(body)], path
        for row in body.get("rows", []):
            assert not list(row_v.iter_errors(row)) and row["citation_url"].startswith("https://")
        assert r["X-Rules-Version"] == body["rules_version"] and r["Cache-Control"] == "public, max-age=300"
        assert lapse_wording.check_banned(r.content.decode("utf-8")) == []
        versions.add(body["rules_version"])
    assert len(versions) == 1
    # the trailing slash is optional, as on /api/v1/lapse/similar
    assert client.get("/api/v1/rules/route_windows", {"office": "KR"}, HTTP_X_API_KEY=opkey).json()["rows"][0]["value"]["months"] == 31


def test_rules_paths_need_a_key(client, rules_env):
    for path, params in PATHS[:3]:
        assert client.get(path, params).status_code == 403


@pytest.mark.parametrize("path,params,reason", [
    ("/api/v1/rules/route_windows/", {"office": "US", "priority_date": "2024-01-01"}, "no application number"),
    ("/api/v1/rules/fee_schedule/", {"office": "US", "application_number": "17123456"}, "no application number"),
    ("/api/v1/rules/grace_period/", {"office": "US", "patent_id": "10000000"}, "no application number"),
    ("/api/v1/rules/route_windows/", {"office": "XX"}, "must be one of"),
    ("/api/v1/rules/route_windows/", {}, "is required"),
    ("/api/v1/rules/fee_schedule/", {"office": "EP", "as_of": "20261020"}, "YYYY-MM-DD"),
    ("/api/v1/rules/pph_routes/", {"from_office": "US", "to_office": "US"}, "must differ"),
    ("/api/v1/rules/rep_required/", {"office": "US", "act": "file"}, "must be one of"),
    ("/api/v1/rules/route_windows/", {"office": "US", "x": "1"}, "unknown parameter"),
])
def test_refused_inputs_answer_400_err_q(client, opkey, rules_env, path, params, reason):
    r = client.get(path, params, HTTP_X_API_KEY=opkey)
    assert r.status_code == 400 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_Q" and reason in r["X-Status-Reason"]


def test_a_repeated_parameter_is_refused(client, opkey, rules_env):
    r = client.get("/api/v1/rules/route_windows/?office=US&office=EP", HTTP_X_API_KEY=opkey)
    assert r.status_code == 400 and "given once" in r["X-Status-Reason"]


def test_problem_details_on_a_refusal(client, opkey, rules_env):
    r = client.get("/api/v1/rules/route_windows/", {"office": "US", "priority_date": "2024-01-01"}, HTTP_X_API_KEY=opkey,
                   HTTP_ACCEPT="application/problem+json")
    assert r.status_code == 400 and r["Content-Type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 400 and body.get("code") == "ERR_Q" and "no application number" in body["detail"]


def test_engine_or_dataset_unavailable_answers_503(client, opkey, rules_env, monkeypatch, tmp_path):
    monkeypatch.setattr(lapse_rules, "rules_module", lambda: None)
    r = client.get("/api/v1/rules/route_windows/", {"office": "US"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 503 and r["X-Status-Reason-Code"] == "ERR_ES"
    monkeypatch.undo()
    settings.LAPSE_REPO_DIR = str(tmp_path)  # a clone without rules/
    r = client.get("/api/v1/rules/route_windows/", {"office": "US"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 503 and "could not be read" in r["X-Status-Reason"]


def test_meta_names_the_rules_version(client, rules_env):
    body = client.get("/api/v1/meta/").json()
    from lapse import rules

    assert body["rules_version"] == rules.load(Path(rules_env) / "rules").version


@pytest.fixture
def free_key():
    """A fresh Free account key, made the way POST /api/v1/meta/keys/ makes one (lapse_accounts/views.py), without
    the endpoint: the whole suite reaches that endpoint's per-address rate, and the conftest's user_key then answers 429."""
    import uuid

    from lapse_accounts.models import PLAN_FREE, AccountKey

    sub = str(uuid.uuid4())
    email = f"rules-{sub[:8]}@example.test"
    obj, secret = APIUserKey.objects.create_key(name="rules-free", username=email, email=email)
    AccountKey.objects.create(api_key=obj, supabase_user_id=sub, email=email, plan=PLAN_FREE)
    return secret


def test_rules_calls_count_toward_the_monthly_allowance(client, free_key, rules_env):
    key = free_key  # LAPSE_FREE_MONTHLY_LIMIT is 5 in these tests
    codes = [client.get("/api/v1/rules/route_windows/", {"office": "US"}, HTTP_X_API_KEY=key).status_code for _ in range(6)]
    assert codes == [200] * 5 + [429]
    from lapse_accounts import metering

    assert metering.usage_summary(prefix=key.split(".")[0])["count"] == 5  # the body of GET /api/v1/meta/usage/
