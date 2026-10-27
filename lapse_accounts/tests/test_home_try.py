"""Release S (PatentRef 1.17.0): the home page at / (before, / redirected to /docs/) and the keyless demo at /try/, which runs
only its fixed requests, on an allowance shared by every visitor and limited per client address, never on a key.

  python -m pytest lapse_accounts/tests/test_home_try.py -q -p no:django
"""
import html
import json
import re
import time

import pytest
from django.conf import settings
from django.test import RequestFactory
from django.urls import resolve

from API import lapse_wording
from lapse_accounts import demo, public, ratelimit
from lapse_accounts.models import MonthlyUsage, WindowCount

DASHES = (chr(0x2014), chr(0x2013))
BROWSER = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
# requests the three-row search file of conftest.py can answer, in place of the real examples (which need the full data)
TEST_EXAMPLES = (
    demo.Example("one", "One patent by its number", "The record of US 10000000.", "/api/v1/patent/",
                 {"q": {"patent_id": "10000000"}, "f": ["patent_id", "patent_title", "patent_date"]}),
    demo.Example("dates", "Grants on one day", "Patents granted on 2018-06-19.", "/api/v1/patent/",
                 {"q": {"_and": [{"_gte": {"patent_date": "2018-06-19"}}, {"_lte": {"patent_date": "2018-06-19"}}]},
                  "f": ["patent_id", "patent_title"], "s": [{"patent_id": "asc"}], "o": {"size": 2}}),
)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    WindowCount.objects.all().delete()
    public._footer_cache.update({"at": 0.0, "lines": None, "mtime": None})
    yield


@pytest.fixture
def examples(monkeypatch):
    monkeypatch.setattr(demo, "EXAMPLES", TEST_EXAMPLES)
    monkeypatch.setattr(demo, "BY_NAME", {e.name: e for e in TEST_EXAMPLES})
    return TEST_EXAMPLES


def text(r):
    return r.content.decode("utf-8")


def skeleton(h):
    assert h.count('class="pr-header"') == 1 and h.count('class="pr-footer"') == 1
    assert '<html lang="en"' in h and re.search(r"<title>[^<]+ \| PatentRef</title>", h) and "<h1>" in h
    assert '<main class="pr-main" id="main">' in h and "Data version " in h
    for href in ("/docs/legal/terms", "/docs/legal/privacy", "/docs/legal/acceptable-use", "/docs/legal/dmca"):
        assert f'href="{href}"' in h, href
    for line in public.footer_lines():
        assert html.escape(line) in h, line
    assert not any(d in h for d in DASHES)
    assert not lapse_wording.check_banned(h)


# ---------------------------------------------------------------- the home page (spec 3)

def test_home_page(client):
    r = client.get("/", HTTP_ACCEPT=BROWSER)
    assert r.status_code == 200 and r["Content-Type"] == "text/html; charset=utf-8" and "Location" not in r
    h = text(r)
    skeleton(h)
    lead = re.search(r'<p class="lead">(.*?)</p>', h, re.S).group(1)
    assert len(re.findall(r"[.] ", lead + " ")) == 2, lead   # two sentences
    for words in ("PatentsView-compatible API", "US patents granted from 1976 on", "refreshed every week",
                  "independent service and is not affiliated with the USPTO"):
        assert words in lead, words
    # the base URL in R and in Python, three lines each
    blocks = re.findall(r"<pre><code>(.*?)</code></pre>", h, re.S)
    assert len(blocks) == 2 and all(len(b.split("\n")) == 3 for b in blocks), blocks
    r_code, py_code = (html.unescape(b) for b in blocks)
    assert r_code.startswith("library(patentsview)\n") and 'PATENTSVIEW_API_KEY = "YOUR_KEY"' in r_code
    assert 'assignInNamespace("get_base", function(endpoint) sprintf("https://patentref.io/api/v1/%s/", endpoint), ns = "patentsview")' in r_code
    assert py_code.startswith("import requests\n") and 'BASE = "https://patentref.io/api/v1/"' in py_code and '{"X-Api-Key": "YOUR_KEY"}' in py_code
    # the free key button and the links
    assert '<a class="button" href="/api/v1/meta/signup/">Get a free key</a>' in h
    for href in ("/docs/", "/try/", "/api/v1/meta/status/", "/docs/snapshot.html", "/waitlist/"):
        assert f'href="{href}"' in h, href
    assert '<a class="pr-name" href="/">PatentRef</a>' in h
    # no script at all, nothing to fill in
    assert "<script" not in h and "<form" not in h and "<input" not in h
    assert r["Cache-Control"] == "public, max-age=300" and not r.cookies


def test_the_302_to_the_docs_is_gone(client):
    for path in ("/", ""):
        r = client.get(path or "/")
        assert r.status_code == 200 and "Location" not in r
    assert resolve("/").func is demo.home
    from lapse_accounts import root_urls

    root = [p for p in root_urls.urlpatterns if str(p.pattern) in ("", "^$")]
    assert [getattr(p.callback, "__name__", "") for p in root if hasattr(p, "callback")] == ["home"]   # upstream's RedirectView left out
    assert client.head("/").status_code == 200
    assert client.post("/").status_code == 405


def test_the_name_in_every_header_links_home(client):
    from pathlib import Path

    tpl = Path(demo.__file__).parent / "templates" / "lapse_accounts" / "base.html"
    assert '<a class="pr-name" href="{% block home %}/{% endblock %}">PatentRef</a>' in tpl.read_text(encoding="utf-8")
    assert '<a class="pr-name" href="/">PatentRef</a>' in text(client.get("/waitlist/"))
    assert '<a class="pr-name" href="/api/v1/meta/status/">PatentRef</a>' in text(client.get("/api/v1/meta/status/"))   # its own


# ---------------------------------------------------------------- the demo (spec 10)

def test_try_lists_only_the_fixed_requests(client):
    r = client.get("/try/", HTTP_ACCEPT=BROWSER)
    assert r.status_code == 200 and r["Cache-Control"] == "public, max-age=300"
    h = text(r)
    skeleton(h)
    assert h.count("Run this request") == len(demo.EXAMPLES) == 4
    for e in demo.EXAMPLES:
        assert html.escape(e.curl()) in h, e.name
        assert f'href="/try/{e.name}/" rel="nofollow"' in h
        assert '-H "X-Api-Key: YOUR_KEY"' in e.curl() and "https://patentref.io" + e.path in e.curl()
    # no free-form query: no form, no field, no script
    for tag in ("<form", "<input", "<textarea", "<select", "<script", "contenteditable"):
        assert tag not in h, tag
    assert client.get("/try").status_code == 200
    assert client.get("/try/?utm_source=hn").status_code == 200   # the index runs nothing, so a campaign code is harmless


def test_the_real_examples_are_requests_the_api_takes():
    """Each example names an API view and only fields that view knows; the full data answers them (the deploy's live check
    runs each one on the box and wants 200 with total_hits at least 1)."""
    from API.PVAPIViews import PVAPIView

    assert [e.name for e in demo.EXAMPLES] == ["patent", "assignee", "claims", "status"]
    assert len({e.title for e in demo.EXAMPLES}) == 4
    for e in demo.EXAMPLES:
        assert re.fullmatch(r"[a-z]+", e.name)
        cls = resolve(e.path).func.cls
        assert issubclass(cls, PVAPIView), e.path
        view = cls()
        assert list(e.params) == [k for k in ("q", "f", "s", "o") if k in e.params]
        for f in e.params.get("f", []):
            assert f in view.field_list or f.split(".", 1)[0] in view.nested_paths, (e.name, f)
        for s in e.params.get("s", []):
            assert list(s)[0] in view.field_list, (e.name, s)
        assert json.loads(json.dumps(e.params)) == e.params
        assert "X-Api-Key" not in e.query_string() and "key" not in e.params


def test_a_run_shows_the_answer_a_keyed_client_gets(client, examples, user_key):
    _, key, _ = user_key
    for e in examples:
        r = client.get(f"/try/{e.name}/", HTTP_ACCEPT=BROWSER, REMOTE_ADDR="203.0.113.20")
        assert r.status_code == 200, text(r)[:500]
        h = text(r)
        skeleton(h)
        assert r["Cache-Control"] == "no-store" and r["X-Robots-Tag"] == "noindex"
        assert html.escape(e.curl()) in h and "Status 200" in h
        shown = html.unescape(re.findall(r"<pre><code>(.*?)</code></pre>", h, re.S)[-1])
        keyed = client.get(e.path, {k: json.dumps(v) for k, v in e.params.items()}, HTTP_X_API_KEY=key, REMOTE_ADDR="203.0.113.21")
        assert keyed.status_code == 200
        assert json.loads(shown) == keyed.json(), e.name
        assert keyed.json()["total_hits"] >= 1
        assert f"X-Data-Version {keyed['X-Data-Version']}" in h and r["X-Data-Version"] == keyed["X-Data-Version"]
    # the body itself, byte for byte, through demo.run
    rf = RequestFactory()
    e = examples[0]
    got = demo.run(rf.get(f"/try/{e.name}/"), e)
    keyed = client.get(e.path, {k: json.dumps(v, separators=(",", ":")) for k, v in e.params.items()}, HTTP_X_API_KEY=key, REMOTE_ADDR="203.0.113.21")
    assert got["status"] == 200 and got["body"] == keyed.content


def test_the_demo_never_takes_a_key_and_never_counts_against_an_account(client, examples, user_key, monkeypatch):
    _, key, prefix = user_key
    months = list(MonthlyUsage.objects.values_list("subject", "month", "count"))
    for extra in ({"HTTP_X_API_KEY": key}, {"HTTP_AUTHORIZATION": "Bearer x.y.z"}, {"HTTP_X_API_KEY": "nonsense"}):
        r = client.get("/try/one/", HTTP_ACCEPT=BROWSER, REMOTE_ADDR="203.0.113.30", **extra)
        assert r.status_code == 400 and "The demo takes no key" in text(r), extra
        assert r["Cache-Control"] == "no-store"
    assert not WindowCount.objects.filter(win__startswith="demo-").exists()   # nothing ran, nothing taken
    # a run: the view gets a request with no key, no Authorization and no cookie, whatever the visitor's browser sent
    seen = []
    real = demo._view_for

    def spy(path):
        view, kwargs = real(path)

        def wrapped(request, **kw):
            seen.append(dict(request.META))
            return view(request, **kw)
        return wrapped, kwargs

    monkeypatch.setattr(demo, "_view_for", spy)
    client.cookies["sessionid"] = "abc"
    r = client.get("/try/one/", HTTP_ACCEPT=BROWSER, REMOTE_ADDR="203.0.113.30", HTTP_REFERER="https://news.ycombinator.com/")
    assert r.status_code == 200
    assert len(seen) == 1
    meta = seen[0]
    for name in ("HTTP_X_API_KEY", "HTTP_AUTHORIZATION", "HTTP_COOKIE", "HTTP_REFERER"):
        assert name not in meta, name
    assert meta["HTTP_ACCEPT"] == "application/json" and meta["REQUEST_METHOD"] == "GET" and meta["PATH_INFO"] == "/api/v1/patent/"
    assert list(MonthlyUsage.objects.values_list("subject", "month", "count")) == months   # no month reserved or counted
    client.cookies.clear()


def test_the_demo_takes_no_parameters_and_only_its_names(client, examples):
    for q in ("q=%7B%7D", "f=%5B%22patent_title%22%5D", "o=%7B%22size%22%3A1000%7D", "x=1"):
        r = client.get(f"/try/one/?{q}", HTTP_ACCEPT=BROWSER)
        assert r.status_code == 400 and "The demo takes no parameters" in text(r), q
    r = client.get("/try/nope/", HTTP_ACCEPT=BROWSER)
    assert r.status_code == 404 and "No such example" in text(r) and r["X-Robots-Tag"] == "noindex"
    assert client.post("/try/one/").status_code == 405
    assert client.head("/try/one/").status_code == 405   # a run is a GET of a page, never a probe
    assert not WindowCount.objects.filter(win__startswith="demo-").exists()


def test_the_allowance_per_address(client, examples, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_DEMO_PER_HOUR", 3, raising=False)
    monkeypatch.setattr(settings, "LAPSE_DEMO_PER_DAY", 100, raising=False)
    for _ in range(3):
        assert client.get("/try/one/", REMOTE_ADDR="203.0.113.40").status_code == 200
    r = client.get("/try/dates/", REMOTE_ADDR="203.0.113.40")
    assert r.status_code == 429 and 1 <= int(r["Retry-After"]) <= 3601
    h = text(r)
    assert "This address has had its 3 demo runs this hour" in h and 'href="/api/v1/meta/signup/"' in h
    assert r["X-Robots-Tag"] == "noindex" and r["Cache-Control"] == "no-store"
    assert client.get("/try/one/", REMOTE_ADDR="203.0.113.41").status_code == 200   # another address is not affected
    assert client.get("/try/", REMOTE_ADDR="203.0.113.40").status_code == 200       # the list never counts
    row = WindowCount.objects.get(subject="ip:203.0.113.40", win__startswith="demo-ip:")
    assert row.count == 3 and row.win.startswith("demo-ip:3600:")


def test_the_allowance_for_everyone(client, examples, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_DEMO_PER_HOUR", 10, raising=False)
    monkeypatch.setattr(settings, "LAPSE_DEMO_PER_DAY", 2, raising=False)
    assert client.get("/try/one/", REMOTE_ADDR="203.0.113.50").status_code == 200
    assert client.get("/try/one/", REMOTE_ADDR="203.0.113.51").status_code == 200
    r = client.get("/try/one/", REMOTE_ADDR="203.0.113.52")
    assert r.status_code == 429 and "runs for everyone today" in text(r) and 1 <= int(r["Retry-After"]) <= 86401
    # the run that did not happen took nothing from its address
    assert WindowCount.objects.get(subject="ip:203.0.113.52", win__startswith="demo-ip:").count == 0
    assert WindowCount.objects.get(subject=demo.SUBJECT_ALL).count == 2


def test_counters_of_windows_that_are_over_are_deleted(client, examples):
    now = time.time()
    old_ip, _ = ratelimit.minute_window(demo.SCOPE_IP, 3600, now - 7200)
    old_all, _ = ratelimit.minute_window(demo.SCOPE_ALL, 86400, now - 2 * 86400)
    other, _ = ratelimit.minute_window("wl-ip", 3600, now - 7200)
    for subject, win in (("ip:198.51.100.1", old_ip), (demo.SUBJECT_ALL, old_all), ("ip:198.51.100.1", other)):
        ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", win, 10)
    assert client.get("/try/one/", REMOTE_ADDR="203.0.113.60").status_code == 200
    wins = set(WindowCount.objects.values_list("win", flat=True))
    assert old_ip not in wins and old_all not in wins and other in wins   # only the demo's own ended windows go
    cur_ip, _ = ratelimit.minute_window(demo.SCOPE_IP, 3600, now)
    assert cur_ip in wins


def test_headers_as_the_other_pages(client, examples):
    """The new pages carry the same response headers as the app's other pages (the security headers of Django's
    middleware, the content type, no cookie); HSTS is set by Caddy on every answer of the host (patentref repo,
    tests/test_caddy_site.py)."""
    ref = client.get("/waitlist/", HTTP_ACCEPT=BROWSER)
    shared = {k for k in ("X-Content-Type-Options", "Referrer-Policy", "Cross-Origin-Opener-Policy", "X-Frame-Options") if ref.has_header(k)}
    assert shared, "the reference page has none of the security headers"
    for path in ("/", "/try/", "/try/one/", "/try/nope/", "/try/one/?x=1"):
        r = client.get(path, HTTP_ACCEPT=BROWSER, REMOTE_ADDR="203.0.113.70")
        for k in shared:
            assert r.get(k) == ref.get(k), (path, k)
        assert r["Content-Type"] == "text/html; charset=utf-8" and not r.cookies and not r.has_header("Set-Cookie"), path
        assert r.has_header("X-Data-Version"), path


def test_the_privacy_policy_covers_the_address_count():
    """The per-address count rests on the usage data paragraph of the Privacy Policy (section 1); demo.py quotes it."""
    quote = ("For every API request we record the time, the key prefix, the request path and method, the response status, the "
             "time the request took, and the client IP address. Purpose: metering your monthly allowance, enforcing per-minute "
             "and per-address limits, abuse prevention")
    assert " ".join(quote.split()) in " ".join(demo.__doc__.split())
    from lapse_accounts.tests.test_legal_pages import _real_legal_dir

    real = _real_legal_dir()
    if real is None:
        pytest.skip("no patentref checkout beside this one")
    assert quote in (real / "privacy.md").read_text(encoding="utf-8")
