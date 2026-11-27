"""Release AJ (PatentRef 1.27.0): every record page the fork's own pages link to is in the patentref clone's sitemap-pages.xml
(LAPSE_REPO_DIR): the home page, /agents/, /try/ and /triage/ as rendered here, and the triage example's patents (its page links
each by its permalink). The patentref side reads the same links from the pages it builds and from the served site
(tools/site_links.py) and lists them (tools/gen_site_files.py); this test keeps a later change of a fork page from linking a record
page the sitemap lacks. And every record page the patentref clone lists by hand (ops/sitemap_extra.json, a one-line reason each)
is in that sitemap and answers 200 here, given its row in the test search file. Skipped when no patentref clone with the sitemap
is at hand.

  python -m pytest lapse_accounts/tests/test_site_links.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import os
import re
from pathlib import Path

import pytest
from django.conf import settings

from lapse_accounts import triage_page

LINK = re.compile(r'(?:href="(?:https://patentref\.io)?|https://patentref\.io)/p/([A-Z]{0,2}[0-9]{1,9})(?=/?(?:[?#"\s<]|$))')


def repo_dir():
    return os.environ.get("LAPSE_REPO_DIR") or getattr(settings, "LAPSE_REPO_DIR", "") or ""


def sitemap_ids():
    repo = repo_dir()
    p = Path(repo) / "public" / "site" / "sitemap-pages.xml" if repo else None
    if not p or not p.is_file():
        pytest.skip("no patentref clone with public/site/sitemap-pages.xml (LAPSE_REPO_DIR)")
    return set(re.findall(r"<loc>https://patentref\.io/p/([A-Z]{0,2}[0-9]{1,9})/</loc>", p.read_text(encoding="utf-8")))


def extra_ids():
    """The record pages the patentref clone lists by hand (ops/sitemap_extra.json), in its order."""
    repo = repo_dir()
    p = Path(repo) / "ops" / "sitemap_extra.json" if repo else None
    if not p or not p.is_file():
        pytest.skip("no patentref clone with ops/sitemap_extra.json (LAPSE_REPO_DIR)")
    return [str(e["id"]) for e in json.loads(p.read_text(encoding="utf-8"))["pages"]]


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


@pytest.fixture
def extra_env(monkeypatch):
    """test_lapse_group's search file and catalog with one more row for each page of ops/sitemap_extra.json (fixture rows: the
    record page needs its patent in the search file; the title and dates are placeholders), set up as its lapse_env sets them."""
    from API.search_sqlite import LapseSQLiteSearch
    from lapse_accounts.tests import test_lapse_group as G
    from lapse_accounts.tests.conftest import _TMP

    ids = extra_ids()
    monkeypatch.setattr(G, "PATENTS", G.PATENTS + [(pid, "A record page the sitemap lists by hand", "2025-06-03", "utility", 0,
                                                    "2022-03-15", "", "", "") for pid in ids])
    monkeypatch.setattr(G, "CATALOG", G.CATALOG + [(f"US{pid}", "2022-03-15", "2025-06-03", None, None, None, None, None, 0, 0, 0, 0)
                                                   for pid in ids])
    if not G.REPO_DIR or not (Path(G.REPO_DIR) / "lapse" / "expiry.py").is_file():
        pytest.skip("the record page's status engine lapse.expiry lives in the patentref clone (LAPSE_REPO_DIR)")
    search, catalog = str(_TMP / "extra_search.db"), str(_TMP / "extra_catalog.db")
    G.build_search_db(search)
    G.build_catalog(catalog)
    old, old_cat, old_text = dict(settings.LAPSE_SQLITE), getattr(settings, "LAPSE_CATALOG_PATH", None), getattr(settings, "LAPSE_TEXT_PATH", None)
    old_repo = getattr(settings, "LAPSE_REPO_DIR", None)
    settings.LAPSE_SQLITE["path"] = search
    settings.LAPSE_CATALOG_PATH = catalog
    settings.LAPSE_TEXT_PATH = str(_TMP / "no_long_text_file.db")   # no claims shown: no test reads a file in service
    settings.LAPSE_REPO_DIR = G.REPO_DIR
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    try:
        yield ids
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        settings.LAPSE_CATALOG_PATH = old_cat
        settings.LAPSE_TEXT_PATH = old_text
        settings.LAPSE_REPO_DIR = old_repo
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()


def test_every_page_of_the_extra_list_answers_200_and_is_in_the_sitemap(client, extra_env):
    listed = sitemap_ids()
    assert extra_env, "ops/sitemap_extra.json lists no page"
    for pid in extra_env:
        assert pid in listed, pid
        r = client.get(f"/p/{pid}/")
        page = r.content.decode("utf-8")
        assert r.status_code == 200 and r["Content-Type"].startswith("text/html"), (pid, r.status_code)
        assert page.count(f'<link rel="canonical" href="https://patentref.io/p/{pid}/">') == 1, pid
    assert client.get("/p/12999999/").status_code == 404   # a patent without its row answers 404: the rows make the 200s
