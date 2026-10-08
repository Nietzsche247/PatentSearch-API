"""Root URLconf for the Lapse settings: the account routes first, then upstream's `pvapi.urls`
unchanged. Set by `pvapi/settings/lapse_local.py` (ROOT_URLCONF); upstream settings keep theirs."""
from django.urls import include, path, re_path

from lapse_accounts import public
from pvapi.urls import urlpatterns as upstream_urlpatterns

urlpatterns = [
    path("api/v1/meta/", include("lapse_accounts.urls")),
    # agent plumbing at the root (gate 2.11): served by Django so the shared Caddy needs no change
    re_path(r"^(?P<name>llms\.txt|llms-full\.txt|openapi\.json|LICENSES\.md)$", public.public_file, name="public-file"),
    re_path(r"^docs/errors/?$", public.error_docs, name="error-docs"),
    re_path(r"^docs/errors/(?P<code>[A-Za-z_]+)/?$", public.error_docs, name="error-doc"),
] + list(upstream_urlpatterns)
