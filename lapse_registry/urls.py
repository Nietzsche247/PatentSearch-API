"""Registry routes (lapse_registry.views). Root URLconf of pvapi.settings.registry only; the API's URLconf does not
include them, so the API process never serves a registry path."""
from django.urls import re_path

from lapse_registry import views

ID = r"(?P<rid>[0-7][0-9A-HJKMNP-TV-Z]{25})"
CASE = r"(?P<cid>[0-7][0-9A-HJKMNP-TV-Z]{25})"

# Every API route lives under a prefix Caddy already sends to this process (ops/caddy/patentref_registry.caddy:
# /registry/disclosures*, /registry/search*, /registry/sitemap.xml, /registry/health, the JWKS, /r/<id>.json and
# /r/<id>/file), so registry 2 adds routes without a change to the site file.
urlpatterns = [
    re_path(r"^registry/disclosures/?$", views.disclosures, name="registry-disclosures"),
    re_path(r"^registry/disclosures/check/?$", views.check, name="registry-check"),
    re_path(r"^registry/disclosures/terms/?$", views.terms_status, name="registry-terms"),
    re_path(r"^registry/disclosures/account/export/?$", views.account_export, name="registry-account-export"),
    re_path(r"^registry/disclosures/account/erase/?$", views.account_erase, name="registry-account-erase"),
    re_path(r"^registry/disclosures/notices/?$", views.notice, name="registry-notice"),
    re_path(rf"^registry/disclosures/notices/{CASE}/?$", views.notice_status, name="registry-notice-status"),
    re_path(rf"^registry/disclosures/notices/{CASE}/counter/?$", views.counter_notice, name="registry-counter-notice"),
    re_path(rf"^registry/disclosures/{ID}/monitor/?$", views.monitor, name="registry-monitor"),
    re_path(rf"^registry/disclosures/{ID}/claim/?$", views.claim, name="registry-claim"),
    re_path(rf"^registry/disclosures/{ID}/contribution-log/?$", views.contribution_log, name="registry-contribution-log"),
    re_path(rf"^registry/disclosures/{ID}/contribution-log\.zip$", views.contribution_zip, name="registry-contribution-zip"),
    re_path(rf"^registry/disclosures/{ID}/?$", views.record, name="registry-record"),
    re_path(rf"^registry/disclosures/{ID}/proof/?$", views.proof, name="registry-proof"),
    re_path(rf"^registry/disclosures/{ID}/receipt/?$", views.receipt, name="registry-receipt"),
    re_path(rf"^registry/disclosures/{ID}/seal/?$", views.seal, name="registry-seal"),
    re_path(rf"^registry/disclosures/{ID}/sealed/?$", views.sealed_release, name="registry-sealed"),
    re_path(r"^registry/search/?$", views.search, name="registry-search"),
    re_path(r"^registry/sitemap\.xml$", views.sitemap, name="registry-sitemap"),
    re_path(r"^registry/health/?$", views.health, name="registry-health"),
    re_path(r"^registry/\.well-known/jwks\.json$", views.jwks, name="registry-jwks"),
    re_path(rf"^r/{ID}\.json$", views.record, name="record-json"),
    re_path(rf"^r/{ID}/file$", views.record_file, name="record-file"),
]

handler404 = "lapse_registry.views.not_found"
