"""Release AJ (PatentRef 1.27.0): every record page the fork's own pages link to is in the patentref clone's sitemap-pages.xml
(LAPSE_REPO_DIR): the home page, /agents/, /try/ and /triage/ as rendered here, and the triage example's patents (its page links
each by its permalink). The patentref side reads the same links from the pages it builds and from the served site
(tools/site_links.py) and lists them (tools/gen_site_files.py); this test keeps a later change of a fork page from linking a record
page the sitemap lacks. Skipped when no patentref clone with the sitemap is at hand.

  python -m pytest lapse_accounts/tests/test_site_links.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import os
import re
from pathlib import Path

import pytest
from django.conf import settings

from lapse_accounts import triage_page

LINK = re.compile(r'(?:href="(?:https://patentref\.io)?|https://patentref\.io)/p/([A-Z]{0,2}[0-9]{1,9})(?=/?(?:[?#"\s<]|$))')


def sitemap_ids():
    repo = os.environ.get("LAPSE_REPO_DIR") or getattr(settings, "LAPSE_REPO_DIR", "") or ""
    p = Path(repo) / "public" / "site" / "sitemap-pages.xml" if repo else None
    if not p or not p.is_file():
        pytest.skip("no patentref clone with public/site/sitemap-pages.xml (LAPSE_REPO_DIR)")
    return set(re.findall(r"<loc>https://patentref\.io/p/([A-Z]{0,2}[0-9]{1,9})/</loc>", p.read_text(encoding="utf-8")))


def test_the_link_reader():
    page = ('<a href="/p/10034028#c1">x</a> <a href="https://patentref.io/p/11773614?v=20261117.2">y</a> '
            '<a href="/p/12345/extracted/">z</a> https://patentref.io/p/RE31177 <a href="/docs/">d</a>')
    assert set(LINK.findall(page)) == {"10034028", "11773614", "RE31177"}   # the extracted page is not the record page


def test_the_triage_example_links_only_listed_record_pages():
    ids = sitemap_ids()
    pats = triage_page.example_config()["patents"]
    assert pats and all(p in ids for p in pats), [p for p in pats if p not in ids]


@pytest.mark.parametrize("path", ["/", "/agents/", "/try/", "/triage/"])
def test_the_pages_link_only_listed_record_pages(client, path):
    ids = sitemap_ids()
    r = client.get(path)
    assert r.status_code == 200, (path, r.status_code)
    found = set(LINK.findall(r.content.decode("utf-8")))
    assert found <= ids, (path, sorted(found - ids))
