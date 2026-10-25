"""/swagger-ui/ (PatentRef lane Q2, the gate 7.6 finding): upstream served the page, so it stays, now with Swagger UI pinned to
one release, Subresource Integrity on both files, a Content-Security-Policy, the spec read from /openapi.json and no cookie.

  python -m pytest lapse_accounts/tests/test_swagger_ui.py -q -p no:cacheprovider -o addopts=""
  PATENTREF_NET_TESTS=1 ...   also fetches the two pinned files from unpkg and checks their SHA-384 against the page
"""
import base64
import hashlib
import os
import re
import urllib.request

import pytest
from django.urls import resolve, reverse

from lapse_accounts import swagger


def test_the_page_is_pinned_with_sri_and_reads_the_patentref_spec(client):
    r = client.get("/swagger-ui/")
    assert r.status_code == 200 and r["Content-Type"] == "text/html; charset=utf-8"
    body = r.content.decode("utf-8")
    srcs = re.findall(r'<script src="([^"]+)" integrity="([^"]+)" crossorigin="anonymous"', body)
    links = re.findall(r'<link rel="stylesheet" href="([^"]+)" integrity="([^"]+)" crossorigin="anonymous"', body)
    assert srcs == [(swagger.JS_URL, swagger.JS_SRI)] and links == [(swagger.CSS_URL, swagger.CSS_SRI)]
    for url, sri in srcs + links:
        assert re.fullmatch(r"https://unpkg\.com/swagger-ui-dist@[0-9]+\.[0-9]+\.[0-9]+/[a-z-]+\.(js|css)", url), url
        assert re.fullmatch(r"sha384-[A-Za-z0-9+/]{64}", sri), sri
    # every external reference of the page is one of the two pinned files; upstream's unpinned ones are gone
    assert set(re.findall(r'(?:src|href)="(https?:[^"]*|//[^"]*)"', body)) == {swagger.JS_URL, swagger.CSS_URL}
    assert "swagger-ui-dist@3" not in body and "hierarchical-tags" not in body and "/static/" not in body
    assert 'url: "/openapi.json"' in body
    assert '<html lang="en">' in body and "<main " in body and "<title>Swagger UI | PatentRef API</title>" in body
    assert "csrftoken" not in r.cookies and "X-CSRFToken" not in body and "—" not in body


def test_the_csp_allows_only_the_pinned_files_and_the_one_inline_script(client):
    r = client.get("/swagger-ui/")
    csp = dict((d.split(" ", 1) + [""])[:2] for d in r["Content-Security-Policy"].split("; "))
    assert csp["default-src"] == "'none'" and csp["frame-ancestors"] == "'none'" and csp["base-uri"] == "'none'"
    inline = re.findall(r"<script>(.*?)</script>", r.content.decode("utf-8"), re.S)
    assert len(inline) == 1
    digest = "sha256-" + base64.b64encode(hashlib.sha256(inline[0].encode("utf-8")).digest()).decode("ascii")
    assert csp["script-src"].split() == [swagger.JS_URL, f"'{digest}'"]
    assert csp["style-src"].split() == [swagger.CSS_URL]          # no 'unsafe-inline' anywhere
    assert "unsafe" not in r["Content-Security-Policy"]
    assert csp["connect-src"] == "'self'" and csp["img-src"] == "'self' data:"


def test_methods_and_route(client):
    assert client.head("/swagger-ui/").status_code == 200
    assert client.post("/swagger-ui/").status_code == 405
    r = client.get("/swagger-ui")
    assert r.status_code == 301 and r["Location"].endswith("/swagger-ui/")
    assert reverse("swagger-ui") == "/swagger-ui/" and resolve("/swagger-ui/").func is swagger.page


@pytest.mark.skipif(os.environ.get("PATENTREF_NET_TESTS") != "1", reason="fetches the pinned files from unpkg.com")
def test_the_sri_values_match_the_files_unpkg_serves():
    for url, sri in ((swagger.JS_URL, swagger.JS_SRI), (swagger.CSS_URL, swagger.CSS_SRI)):
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
        assert "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii") == sri, url
