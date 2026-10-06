"""Routes under /api/v1/meta/ (included by `lapse_accounts.root_urls`)."""
from django.urls import re_path

from lapse_accounts import views

urlpatterns = [
    re_path(r"^signup/?$", views.signup_page, name="meta-signup"),
    re_path(r"^keys/?$", views.KeysView.as_view(), name="meta-keys"),
    re_path(r"^usage/?$", views.UsageView.as_view(), name="meta-usage"),
]
