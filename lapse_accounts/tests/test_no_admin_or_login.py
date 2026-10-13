"""Independent audit 5, F5 and B14: the inherited Django admin and social-auth's Google login are off the public site. The
paths answer 404, none answers 500, and no Google backend is configured (the coordinator's decision of 2026-10-13)."""
from django.conf import settings
from django.urls import Resolver404, resolve

import pytest

GONE = ["/admin", "/admin/", "/admin/login/", "/admin/login/?next=/admin/", "/admin/auth/user/", "/admin/lapse_accounts/accountkey/",
        "/login/google-oauth2/", "/login/google-oauth2/?next=/", "/complete/google-oauth2/",
        "/complete/google-oauth2/?state=x&code=y", "/disconnect/google-oauth2/", "/disconnect/google-oauth2/1/", "/logout/",
        "/login/", "/complete/"]


@pytest.mark.parametrize("path", GONE)
def test_the_admin_and_the_social_login_paths_answer_404(client, path):
    r = client.get(path)
    assert r.status_code == 404, (path, r.status_code)
    r = client.post(path.split("?")[0], data={"username": "x", "password": "y"})
    assert r.status_code in (403, 404), (path, r.status_code)  # 403: a POST without a CSRF token; never 500 or 302


def test_no_route_resolves_to_the_admin_or_social_auth():
    for path in ("/admin/", "/login/google-oauth2/", "/complete/google-oauth2/", "/disconnect/google-oauth2/"):
        with pytest.raises(Resolver404):
            resolve(path)


def test_no_google_backend_is_configured():
    assert not any("google" in b.lower() for b in settings.AUTHENTICATION_BACKENDS)
    assert settings.AUTHENTICATION_BACKENDS == ("django.contrib.auth.backends.ModelBackend",)


def test_patentrefs_own_routes_still_answer(client):
    assert client.get("/api/v1/meta/health/").status_code == 200
    assert client.get("/api/v1/patent/").status_code in (401, 403)  # keyed, as before
