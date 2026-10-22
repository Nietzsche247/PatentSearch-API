"""PatentRef 5.11: the key sign-up asks for the terms. The page shows the links and a required box; POST /api/v1/meta/keys/
mints or rotates a key only when the terms of the current version are accepted, and stores the version and the time with
the key's record. Keys minted before the gate keep working."""
import json
import os
import re
import uuid
from pathlib import Path

from django.conf import settings

from API.models import APIUserKey
from lapse_accounts import views
from lapse_accounts.models import PLAN_FREE, AccountKey

JSON = "application/json"
V = settings.LAPSE_TERMS_VERSION


def post(client, token, body):
    return client.post("/api/v1/meta/keys/", data=json.dumps(body), content_type=JSON, HTTP_AUTHORIZATION="Bearer " + token)


def fresh(token_factory, **kw):
    sub = str(uuid.uuid4())
    return sub, token_factory(sub=sub, email=f"t-{sub[:8]}@example.test", **kw)


def test_a_direct_post_without_the_box_mints_nothing(client, token_factory):
    sub, token = fresh(token_factory)
    for body in ({}, {"action": "create"}, {"terms_version": ""}, {"terms_version": "beta-1"}, {"action": "rotate"}):
        r = post(client, token, body)
        assert r.status_code == 403, body
        j = r.json()
        assert r["X-Status-Reason-Code"] == "ERR_TERMS" and j["error"] is True and j["terms_version"] == V
        assert j["terms"]["terms"].endswith("/docs/legal/terms") and j["terms"]["privacy"].endswith("/docs/legal/privacy")
        assert j["terms"]["acceptable_use"].endswith("/docs/legal/acceptable-use")
        assert "api_key" not in j
    assert AccountKey.objects.filter(supabase_user_id=sub).count() == 0


def test_the_box_mints_and_stores_version_and_time(client, token_factory):
    sub, token = fresh(token_factory)
    r = post(client, token, {"terms_version": V})
    assert r.status_code == 201 and r.json()["api_key"] and r.json()["terms_version"] == V
    acct = AccountKey.objects.get(supabase_user_id=sub)
    assert acct.terms_version == V and acct.terms_accepted_at is not None
    # the key works on a data path
    assert client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=r.json()["api_key"]).status_code == 200
    # a rotation of an account that accepted this version carries the acceptance over
    r2 = post(client, token, {"action": "rotate"})
    assert r2.status_code == 201
    new = AccountKey.objects.filter(supabase_user_id=sub).order_by("-created_at").first()
    assert new.terms_version == V and new.terms_accepted_at == acct.terms_accepted_at
    listing = client.get("/api/v1/meta/keys/", HTTP_AUTHORIZATION="Bearer " + token).json()
    assert {k["terms_version"] for k in listing["keys"]} == {V}


def test_acceptance_at_sign_up_counts(client, token_factory):
    """The page's (and the registry's) sign-up puts terms_version and terms_accepted_at in the Supabase user metadata."""
    sub = str(uuid.uuid4())
    email = f"m-{sub[:8]}@example.test"
    token = token_factory(sub=sub, email=email, extra={"user_metadata": {"email": email, "email_verified": True,
                                                                         "terms_version": V, "terms_accepted_at": "2026-10-22T18:00:00Z"}})
    r = post(client, token, {})
    assert r.status_code == 201
    acct = AccountKey.objects.get(supabase_user_id=sub)
    assert acct.terms_version == V and acct.terms_accepted_at.strftime("%Y-%m-%dT%H:%M:%SZ") == "2026-10-22T18:00:00Z"
    other = str(uuid.uuid4())
    old = token_factory(sub=other, email=f"o-{other[:8]}@example.test",
                        extra={"user_metadata": {"email": "x@example.test", "email_verified": True, "terms_version": "beta-1"}})
    assert post(client, old, {}).status_code == 403


def test_keys_minted_before_the_gate_keep_working(client, token_factory):
    """A key whose record has no terms version (minted before 5.11) still answers on the data paths and the usage route;
    its rotation asks for the terms."""
    sub, token = fresh(token_factory)
    obj, secret = APIUserKey.objects.create_key(name=f"{PLAN_FREE}:{sub[:8]}:old", username="old@example.test", email="old@example.test")
    AccountKey.objects.create(api_key=obj, supabase_user_id=sub, email="old@example.test", plan=PLAN_FREE)
    assert AccountKey.objects.get(supabase_user_id=sub).terms_version is None
    assert client.get("/api/v1/meta/usage/", HTTP_X_API_KEY=secret).status_code == 200
    assert client.get("/api/v1/patent/", {"q": json.dumps({"patent_id": "10000000"})}, HTTP_X_API_KEY=secret).status_code == 200
    assert post(client, token, {"action": "create"}).status_code == 200   # create with a key in place mints nothing
    assert post(client, token, {"action": "rotate"}).status_code == 403
    assert not APIUserKey.objects.get(pk=obj.pk).revoked                 # the refused rotation left the old key alone
    assert post(client, token, {"action": "rotate", "terms_version": V}).status_code == 201


def test_page_shows_the_links_and_a_required_box(client):
    h = client.get("/api/v1/meta/signup/").content.decode()
    for href in ("/docs/legal/terms", "/docs/legal/privacy", "/docs/legal/acceptable-use"):
        assert h.count(f'href="{href}"') >= 3, href   # the sign-up box, the key box and the footer
    assert re.search(r'<input id="su-terms" type="checkbox" required><label for="su-terms">I have read and accept', h)
    assert '<input id="key-terms" type="checkbox" required><label for="key-terms">' in h
    from django.utils.html import escapejs

    assert f'var TERMS_VERSION = "{escapejs(V)}";' in h and "terms_version: TERMS_VERSION" in h


def test_the_version_is_the_one_the_terms_page_carries():
    """LAPSE_TERMS_VERSION equals the "Version:" line of docs/legal/terms.md in a patentref checkout beside this one."""
    root = Path(__file__).resolve().parent.parent.parent
    for c in [os.environ.get("LAPSE_REPO_DIR", "")] + [str(root.parent / n) for n in ("trust", "patentref", "lapse")] + ["/srv/lapse"]:
        f = Path(c) / "docs" / "legal" / "terms.md" if c else None
        if f and f.exists():
            m = re.search(r"Version: (beta-\d+)\.", f.read_text(encoding="utf-8"))
            assert m and m.group(1) == V == views.terms_version()
            return


def test_the_refusal_is_documented_and_links_the_public_pages(client, token_factory):
    _, token = fresh(token_factory)
    j = post(client, token, {}).json()
    assert j["terms"]["terms"] == "https://patentref.io/docs/legal/terms"
    page = client.get("/docs/errors/ERR_TERMS")
    assert page.status_code == 200 and "terms_version" in page.content.decode()
    r = client.post("/api/v1/meta/keys/", data="{}", content_type=JSON, HTTP_AUTHORIZATION="Bearer " + token,
                    HTTP_ACCEPT="application/problem+json")
    p = r.json()
    assert r.status_code == 403 and p["code"] == "ERR_TERMS" and p["type"] == "https://patentref.io/docs/errors/ERR_TERMS"
