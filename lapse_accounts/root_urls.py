"""Root URLconf for the Lapse settings: the account routes first, then upstream's `pvapi.urls`
unchanged. Set by `pvapi/settings/lapse_local.py` (ROOT_URLCONF); upstream settings keep theirs."""
from django.urls import URLResolver, include, path, re_path

from API import lapse_rules, lapse_views
from lapse_accounts import legal, mail, public
from pvapi.urls import urlpatterns as upstream_urlpatterns


def _ours(pattern):
    """Upstream's routes without the Django admin and social-auth's Google login (independent audit 5, F5 and B14; the
    coordinator's decision of 2026-10-13): nobody uses the admin, PatentRef's accounts run on Supabase, and the login could
    make the API server post to Google. ops/caddy/patentref.caddy answers 404 for the same paths before they reach the app."""
    return not (isinstance(pattern, URLResolver) and ({getattr(pattern, "namespace", None), getattr(pattern, "app_name", None)}
                                                       & {"admin", "social"}))


urlpatterns = [
    path("api/v1/meta/", include("lapse_accounts.urls")),
    # PatentRef's own data paths (gates 2.9 and 2.12): keyed and throttled like every /api/v1/ data path
    re_path(r"^api/v1/lapse/status/(?P<pk>[^/]+)/?$", lapse_views.StatusView.as_view(), name="lapse-status"),
    re_path(r"^api/v1/status/(?P<pk>[^/]+)/?$", lapse_views.StatusView.as_view(), name="status"),
    re_path(r"^api/v1/lapse/similar/?$", lapse_views.SimilarView.as_view(), name="lapse-similar"),
] + [
    # the rules layer (PatentRef 11.3): one route per tool, keyed and throttled like the paths above
    re_path(rf"^api/v1/rules/{p + '/?' if p else ''}$", lapse_rules.RulesView.as_view(tool=tool), name=f"rules-{tool}")
    for p, tool in lapse_rules.ROUTES
] + [
    # the human page behind the versioned permalink; no key
    re_path(r"^p/(?P<pk>[A-Za-z0-9]+)/?$", lapse_views.RecordPage.as_view(), name="record-page"),
    # agent plumbing at the root (gate 2.11): served by Django so the shared Caddy needs no change
    re_path(r"^(?P<name>llms\.txt|llms-full\.txt|openapi\.json|changes\.txt|LICENSES\.md)$", public.public_file, name="public-file"),
    re_path(r"^docs/errors/?$", public.error_docs, name="error-docs"),
    re_path(r"^docs/errors/(?P<code>[A-Za-z_]+)/?$", public.error_docs, name="error-doc"),
    # the legal pages (gates 5.11 and 5.12), read from the patentref repo clone like the files above
    re_path(r"^docs/legal/?$", legal.index, name="legal-index"),
    re_path(r"^docs/legal/(?P<name>terms|privacy|acceptable-use|dmca|processors)\.md$", legal.source, name="legal-source"),
    re_path(r"^docs/legal/(?P<name>terms|privacy|acceptable-use|dmca|processors)/?$", legal.page, name="legal-page"),
    # one-click unsubscribe (gate 5.16, RFC 8058): the URL of every List-Unsubscribe header PatentRef sends
    re_path(r"^u/(?P<token>[A-Za-z0-9_-]+)/?$", mail.unsubscribe, name="unsubscribe"),
] + [p for p in upstream_urlpatterns if _ours(p)]
