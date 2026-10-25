"""PatentRef lane Q2: the registry is not open to outside records yet. With REGISTRY_OPEN off (the default), an account not
named in REGISTRY_PUBLISHERS is refused 403 ERR_NOT_OPEN on publish and on the preflight, nothing is stored, and the terms
route says may_publish false; a named account (its Supabase user id, or its confirmed email address in any case) publishes
as before. Needs the local Postgres of conftest.py, like the other registry tests.

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_registry/tests/test_registry_not_open.py -q -p no:cacheprovider
"""
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from django.conf import settings

from lapse_registry import views

ROOT = Path(__file__).resolve().parents[2]


def post_json(client, path, body, token=None):
    extra = {"HTTP_AUTHORIZATION": "Bearer " + token} if token else {}
    return client.post(path, data=json.dumps(body), content_type="application/json", **extra)


@pytest.fixture
def closed(monkeypatch):
    monkeypatch.setattr(settings, "REGISTRY_OPEN", False)
    monkeypatch.setattr(settings, "REGISTRY_PUBLISHERS", ())
    return monkeypatch


def test_the_default_is_closed():
    """The settings module with neither variable set: closed, nobody named (a fresh process; conftest.py opens it here)."""
    env = {k: v for k, v in os.environ.items() if k not in ("REGISTRY_OPEN", "REGISTRY_PUBLISHERS")}
    code = ("import os, django; os.environ['DJANGO_SETTINGS_MODULE'] = 'pvapi.settings.registry'; django.setup(); "
            "from django.conf import settings as s; print(s.REGISTRY_OPEN, s.REGISTRY_PUBLISHERS)")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=str(ROOT))
    assert out.stdout.strip().splitlines()[-1] == "False ()", out.stderr[-2000:]


def test_an_outside_account_cannot_publish(client, token, submission, closed):
    t = token()
    r = post_json(client, "/registry/disclosures", submission(), t)
    assert r.status_code == 403 and r["X-Status-Reason-Code"] == "ERR_NOT_OPEN"
    assert r.json()["reason"] == "The registry is not open to outside records yet."
    r = post_json(client, "/registry/disclosures/check", submission(), t)
    assert r.status_code == 403 and r.json()["code"] == "ERR_NOT_OPEN"
    mine = client.get("/registry/disclosures", HTTP_AUTHORIZATION="Bearer " + t)
    assert mine.status_code == 200 and mine.json()["records"] == []          # nothing was stored
    terms = client.get("/registry/disclosures/terms", HTTP_AUTHORIZATION="Bearer " + t)
    assert terms.status_code == 200 and terms.json()["may_publish"] is False
    # no token is still 401, before the not-open answer
    assert post_json(client, "/registry/disclosures", submission()).status_code == 401


def test_a_named_account_publishes(client, token, submission, closed):
    sub = str(uuid.uuid4())
    closed.setattr(settings, "REGISTRY_PUBLISHERS", (sub,))
    t = token(sub=sub)
    assert client.get("/registry/disclosures/terms", HTTP_AUTHORIZATION="Bearer " + t).json()["may_publish"] is True
    assert post_json(client, "/registry/disclosures/check", submission(), t).status_code == 200
    r = post_json(client, "/registry/disclosures", submission(), t)
    assert r.status_code == 201, r.content
    # by its confirmed email address too, compared without case (make_token gives <first 8 of sub>@example.test)
    sub2 = str(uuid.uuid4())
    closed.setattr(settings, "REGISTRY_PUBLISHERS", ("someone@else.test", f"{sub2[:8].upper()}@EXAMPLE.TEST"))
    r = post_json(client, "/registry/disclosures", submission(), token(sub=sub2))
    assert r.status_code == 201, r.content
    # and another account stays outside
    r = post_json(client, "/registry/disclosures", submission(), token())
    assert r.status_code == 403 and r.json()["code"] == "ERR_NOT_OPEN"


def test_open_lets_every_account_publish(client, token, submission, closed):
    closed.setattr(settings, "REGISTRY_OPEN", True)
    assert post_json(client, "/registry/disclosures", submission(), token()).status_code == 201


def test_may_publish_reads_only_the_verified_claims():
    with pytest.MonkeyPatch.context() as m:
        m.setattr(settings, "REGISTRY_OPEN", False)
        m.setattr(settings, "REGISTRY_PUBLISHERS", ("Owner@Example.test", "0f0e"))
        assert views.may_publish({"sub": "x", "email": "owner@example.TEST"})
        assert views.may_publish({"sub": "0F0E", "email": ""})
        assert not views.may_publish({"sub": "", "email": ""}) and not views.may_publish({}) and not views.may_publish(None)
        assert not views.may_publish({"sub": "x", "email": "owner@example.test.evil"})
        m.setattr(settings, "REGISTRY_PUBLISHERS", ())
        assert not views.may_publish({"sub": "0f0e", "email": "owner@example.test"})
