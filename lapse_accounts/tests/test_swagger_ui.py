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
    assert '<html lang="en">' in body and "<title>Swagger UI | PatentRef API</title>" in body
    # Swagger UI draws its own main landmark (#operations): the container is a div, so the page has one main (release W)
    assert '<div id="swagger-ui"></div>' in body and "<main" not in body
    assert "csrftoken" not in r.cookies and "X-CSRFToken" not in body and "\u2014" not in body


def test_the_csp_allows_only_the_pinned_files_and_the_one_inline_script_and_style(client):
    r = client.get("/swagger-ui/")
    csp = dict((d.split(" ", 1) + [""])[:2] for d in r["Content-Security-Policy"].split("; "))
    assert csp["default-src"] == "'none'" and csp["frame-ancestors"] == "'none'" and csp["base-uri"] == "'none'"
    body = r.content.decode("utf-8")
    inline = re.findall(r"<script>(.*?)</script>", body, re.S)
    assert len(inline) == 1
    digest = "sha256-" + base64.b64encode(hashlib.sha256(inline[0].encode("utf-8")).digest()).decode("ascii")
    assert csp["script-src"].split() == [swagger.JS_URL, f"'{digest}'"]
    styles = re.findall(r"<style>(.*?)</style>", body, re.S)
    assert len(styles) == 1 and 'style="' not in body
    sdigest = "sha256-" + base64.b64encode(hashlib.sha256(styles[0].encode("utf-8")).digest()).decode("ascii")
    assert csp["style-src"].split() == [swagger.CSS_URL, f"'{sdigest}'"]    # the one inline style by its hash
    assert "unsafe" not in r["Content-Security-Policy"]                      # no 'unsafe-inline' anywhere
    assert csp["connect-src"] == "'self'" and csp["img-src"] == "'self' data:"


def _contrast(fg, bg):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_the_gate_7_6_fixes_are_on_the_page():
    """Independent audit 11: axe-core 4.10.3 found select-name (critical), nested-interactive, color-contrast and target-size
    (serious) on this page and 50 px of sideways scroll at 320 px. The fixes, as the page carries them (release W); the
    live axe gate (C:\\anitw\\axe_gate) checks the rendered page at 1280, 390 and 320 px in both color schemes."""
    s = swagger.INIT_SCRIPT
    assert "deepLinking: false" in s and "deepLinking: true" not in s       # no link inside an operation's button
    assert 'getElementById("servers")' in s and 'setAttribute("aria-label", "Server")' in s and "MutationObserver" in s
    assert 'name(el, "Request body")' in s and 'name(el, "Close")' in s and 'setAttribute("tabindex", "0")' in s
    assert '<meta name="color-scheme" content="light">' in swagger.PAGE
    st = swagger.STYLE
    # every color the style sets on text, against the background it sits on: at least 4.5 to 1 (WCAG 2.2 AA, 1.4.3)
    white = "#ffffff"
    badges = re.findall(r"opblock-(?:get|post|put|delete|patch|head|options|query|deprecated) \.opblock-summary-method \{ background: (#[0-9a-f]{6}); \}", st)
    assert len(badges) == 9
    pairs = [(white, b) for b in badges] + [
        (re.search(r"\.info a:visited \{ color: (#[0-9a-f]{6})", st).group(1), white),
        (white, re.search(r"\.info \.title small \{ background: (#[0-9a-f]{6})", st).group(1)),
        (white, re.search(r"small\.version-stamp \{ background-color: (#[0-9a-f]{6})", st).group(1)),
        (re.search(r"\.btn\.authorize \{ color: (#[0-9a-f]{6})", st).group(1), white),
        (re.search(r"expand-deep-button \{ color: (#[0-9a-f]{6})", st).group(1), "#f0f0f0"),
        (white, re.search(r"constraint--string \{ background-color: (#[0-9a-f]{6})", st).group(1)),
        (re.search(r"\.parameter__in, \.swagger-ui \.parameter__deprecated \{ color: (#[0-9a-f]{6})", st).group(1), "#eff7ff"),
        (re.search(r"\.btn\.cancel \{ color: (#[0-9a-f]{6})", st).group(1), white),
        (white, re.search(r"\.btn\.execute \{ background-color: (#[0-9a-f]{6})", st).group(1)),
    ]
    for fg, bg in pairs:
        assert _contrast(fg, bg) >= 4.5, (fg, bg, round(_contrast(fg, bg), 2))
    # 24 by 24 px targets, and the narrow-screen rules that end the sideways scroll
    assert "copy-to-clipboard { width: 24px; min-width: 24px" in st and "expand-deep-button { color: #505050; min-height: 24px; min-width: 24px" in st
    assert "@media (max-width: 480px)" in st and ".opblock-tag small:empty { display: none; }" in st
    assert "\u2014" not in st and "\u2014" not in s


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
