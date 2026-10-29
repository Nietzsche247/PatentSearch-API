"""Root URLconf for the Lapse settings: the account routes first, then upstream's `pvapi.urls`
unchanged. Set by `pvapi/settings/lapse_local.py` (ROOT_URLCONF); upstream settings keep theirs."""
from django.urls import URLResolver, include, path, re_path

from API import lapse_brief, lapse_extract, lapse_rules, lapse_structure, lapse_structure_counts, lapse_triage, lapse_views
from lapse_accounts import demo, legal, mail, public, swagger, triage_page, waitlist
from pvapi.urls import urlpatterns as upstream_urlpatterns


def _ours(pattern):
    """Upstream's routes without the Django admin and social-auth's Google login (independent audit 5, F5 and B14; the
    coordinator's decision of 2026-10-13): nobody uses the admin, PatentRef's accounts run on Supabase, and the login could
    make the API server post to Google. ops/caddy/patentref.caddy answers 404 for the same paths before they reach the app."""
    return not (isinstance(pattern, URLResolver) and ({getattr(pattern, "namespace", None), getattr(pattern, "app_name", None)}
                                                       & {"admin", "social"}))


def _not_root(pattern):
    """Upstream's redirect of / to /docs/ is left out: / is PatentRef's home page since release S (1.17.0)."""
    return str(pattern.pattern) != ""


urlpatterns = [
    path("api/v1/meta/", include("lapse_accounts.urls")),
    # PatentRef's own data paths (gates 2.9 and 2.12): keyed and throttled like every /api/v1/ data path
    re_path(r"^api/v1/lapse/status/(?P<pk>[^/]+)/?$", lapse_views.StatusView.as_view(), name="lapse-status"),
    re_path(r"^api/v1/status/(?P<pk>[^/]+)/?$", lapse_views.StatusView.as_view(), name="status"),
    re_path(r"^api/v1/lapse/similar/?$", lapse_views.SimilarView.as_view(), name="lapse-similar"),
    # the structure layer, tier S0 (gate S0.2): one patent's claim tree, computed on read by claimgraph; keyed and throttled the same
    re_path(r"^api/v1/structure/claims/(?P<pk>[^/]+)/?$", lapse_structure.ClaimTreeView.as_view(), name="structure-claims"),
    # a brief of one patent cut to a token budget (gate S0.5), from the same claim tree; keyed and throttled the same
    re_path(r"^api/v1/structure/brief/(?P<pk>[^/]+)/?$", lapse_brief.BriefView.as_view(), name="structure-brief"),
    # gate S0.6: one patent's claim counts (API/claim_counts.py from the same claim tree), computed on read; keyed and throttled the same
    re_path(r"^api/v1/structure/counts/(?P<pk>[^/]+)/?$", lapse_structure_counts.ClaimCountsView.as_view(), name="structure-counts"),
    # the triage table (gate S1.8L): up to 500 patents in one answer, computed on read; keyed and throttled the same, plus one
    # table at a time per key
    re_path(r"^api/v1/structure/triage/?$", lapse_triage.TriageView.as_view(), name="structure-triage"),
    # the structure layer, tier S1 (gate S1.1): the on-demand extraction trial, built dark (API/lapse_extract.py); keyed and
    # throttled the same; the jobs route first, so a job id is never read as a patent number
    re_path(r"^api/v1/structure/extract/jobs/(?P<job_id>[^/]+)/?$", lapse_extract.ExtractJobView.as_view(), name="structure-extract-job"),
    re_path(r"^api/v1/structure/extract/(?P<pk>[^/]+)/?$", lapse_extract.ExtractView.as_view(), name="structure-extract"),
] + [
    # the rules layer (PatentRef 11.3): one route per tool, keyed and throttled like the paths above
    re_path(rf"^api/v1/rules/{p + '/?' if p else ''}$", lapse_rules.RulesView.as_view(tool=tool), name=f"rules-{tool}")
    for p, tool in lapse_rules.ROUTES
] + [
    # the human page behind the versioned permalink; no key
    re_path(r"^p/(?P<pk>[A-Za-z0-9]+)/?$", lapse_views.RecordPage.as_view(), name="record-page"),
    # the extraction trial's result as a page (gate S1.1): no key, the per-IP meta rate; 404 while no result is made
    re_path(r"^p/(?P<pk>[A-Za-z0-9]+)/extracted/?$", lapse_extract.ExtractPage.as_view(), name="extract-page"),
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
    # the waitlist (wave 4 Q1): the form, and the confirmation of the double opt-in (a GET shows a button, a POST confirms)
    re_path(r"^waitlist/?$", waitlist.page, name="waitlist"),
    re_path(r"^waitlist/confirm/(?P<token>[A-Za-z0-9_-]+)/?$", waitlist.confirm_page, name="waitlist-confirm"),
    # upstream's interactive page, pinned, with SRI and a CSP, reading /openapi.json (lane Q2; lapse_accounts/swagger.py);
    # upstream's own swagger-ui route is left out below so only this one answers
    path("swagger-ui/", swagger.page, name="swagger-ui"),
    # the home page and the keyless demo (release S, 1.17.0; lapse_accounts/demo.py): the demo runs only its fixed requests,
    # on a shared allowance limited per address, never on a key
    re_path(r"^$", demo.home, name="home"),
    re_path(r"^try/?$", demo.try_index, name="try"),
    re_path(r"^try/(?P<name>[A-Za-z0-9_-]+)/?$", demo.try_run, name="try-run"),
    # the triage table's page (gate S1.8L; lapse_accounts/triage_page.py): the browser sends the visitor's key to the API; the
    # example is built on read from a fixed list, without a key
    re_path(r"^triage/?$", triage_page.page, name="triage"),
    re_path(r"^triage/example/?$", triage_page.example, name="triage-example"),
] + [p for p in upstream_urlpatterns if _ours(p) and _not_root(p) and getattr(p, "name", None) != "swagger-ui"]
