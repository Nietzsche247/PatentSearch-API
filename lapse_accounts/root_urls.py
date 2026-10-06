"""Root URLconf for the Lapse settings: the account routes first, then upstream's `pvapi.urls`
unchanged. Set by `pvapi/settings/lapse_local.py` (ROOT_URLCONF); upstream settings keep theirs."""
from django.urls import include, path

from pvapi.urls import urlpatterns as upstream_urlpatterns

urlpatterns = [path("api/v1/meta/", include("lapse_accounts.urls"))] + list(upstream_urlpatterns)
