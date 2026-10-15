"""Registry routes (lapse_registry.views). Root URLconf of pvapi.settings.registry only; the API's URLconf does not
include them, so the API process never serves a registry path."""
from django.urls import re_path

from lapse_registry import views

ID = r"(?P<rid>[0-7][0-9A-HJKMNP-TV-Z]{25})"

urlpatterns = [
    re_path(r"^registry/disclosures/?$", views.disclosures, name="registry-disclosures"),
    re_path(r"^registry/disclosures/check/?$", views.check, name="registry-check"),
    re_path(rf"^registry/disclosures/{ID}/?$", views.record, name="registry-record"),
    re_path(rf"^registry/disclosures/{ID}/proof/?$", views.proof, name="registry-proof"),
    re_path(rf"^registry/disclosures/{ID}/receipt/?$", views.receipt, name="registry-receipt"),
    re_path(rf"^registry/disclosures/{ID}/seal/?$", views.seal, name="registry-seal"),
    re_path(rf"^registry/disclosures/{ID}/sealed/?$", views.sealed_release, name="registry-sealed"),
    re_path(r"^registry/search/?$", views.search, name="registry-search"),
    re_path(r"^registry/health/?$", views.health, name="registry-health"),
    re_path(rf"^r/{ID}\.json$", views.record, name="record-json"),
    re_path(rf"^r/{ID}/file$", views.record_file, name="record-file"),
]

handler404 = "lapse_registry.views.not_found"
