"""Release AG: the keys of accounts on the operator's own email domains (settings.LAPSE_OWN_ACCOUNT_DOMAINS: omnipoolbuilders.com,
omnipools.com, dmtcode.com and patentref.io) are the service's own, so the status page's API figures and the structure layer's
usage counter leave them out with the operator's key files (lapse_accounts/status.py own_account_prefixes, own_key_prefixes).
A key counts by its own email or its account's email, on the whole domain after the @ and in any case; a subdomain or a longer
name does not count.

  python -m pytest lapse_accounts/tests/test_own_account_keys.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import uuid

import pytest
from django.conf import settings

from API.models import APIUserKey
from lapse_accounts import status
from lapse_accounts.models import AccountKey


@pytest.fixture(autouse=True)
def _own_rows():
    """The keys a test makes are removed after it (they would count as own keys in later suites), and the prefixes kept by
    status are forgotten before and after."""
    before = set(APIUserKey.objects.values_list("pk", flat=True))
    status._own_cache.update({"at": 0.0, "prefixes": frozenset()})
    yield
    APIUserKey.objects.exclude(pk__in=before).delete()
    status._own_cache.update({"at": 0.0, "prefixes": frozenset()})


def key(email=None, account_email=None):
    tag = uuid.uuid4().hex[:8]
    obj, _secret = APIUserKey.objects.create_key(name=f"t-{tag}", username=f"u-{tag}", email=email)
    if account_email is not None:
        AccountKey.objects.create(api_key=obj, supabase_user_id=f"sub-{tag}", email=account_email)
    return obj.prefix


def test_the_four_domains_are_the_default():
    assert tuple(settings.LAPSE_OWN_ACCOUNT_DOMAINS) == ("omnipoolbuilders.com", "omnipools.com", "dmtcode.com", "patentref.io")


def test_own_domains_count_by_the_key_or_its_account_and_nothing_else():
    mine = [key(email="aaron@omnipools.com"), key(account_email="Test@DMTCODE.com"), key(email="x@example.test", account_email="a@patentref.io"),
            key(account_email="ops@omnipoolbuilders.com")]
    others = [key(email="a@sub.omnipools.com"), key(email="a@notomnipools.com"), key(account_email="a@patentref.io.example.test"),
              key(email="patentref.io@example.test"), key(email=None, account_email="someone@example.test")]
    got = status.own_account_prefixes(settings.LAPSE_OWN_ACCOUNT_DOMAINS)
    assert set(mine) <= got and not set(others) & got
    assert status.own_account_prefixes(()) == set()


def test_own_key_prefixes_takes_them_with_the_key_files(monkeypatch):
    p = key(account_email="me@omnipools.com")
    monkeypatch.setattr(settings, "LAPSE_OWN_KEY_FILES", [], raising=False)
    monkeypatch.setattr(status, "_own_cache", {"at": 0.0, "prefixes": frozenset()})
    assert p in status.own_key_prefixes()
    monkeypatch.setattr(settings, "LAPSE_OWN_ACCOUNT_DOMAINS", (), raising=False)
    monkeypatch.setattr(status, "_own_cache", {"at": 0.0, "prefixes": frozenset()})
    assert p not in status.own_key_prefixes()


def test_a_database_that_cannot_be_read_gives_no_prefix(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no database")
    monkeypatch.setattr(APIUserKey.objects, "filter", boom)
    assert status.own_account_prefixes(("omnipools.com",)) == set()
