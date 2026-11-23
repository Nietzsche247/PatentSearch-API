"""Release AI (PatentRef 1.26.0): the filing pages (/filing/, /filing/<code>/), the files they read (/filing/<code>.json,
/filing/rates.json) and the two API paths (/api/v1/filing/timeline/, /api/v1/filing/estimate/), read through the patentref
clone's lapse.filing and lapse.filing_pages (LAPSE_REPO_DIR when set, else a sibling checkout named lapse, patentref or pr,
or /srv/lapse, that holds lapse/filing.py).
  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_accounts/tests/test_filing.py -q -p no:cacheprovider
"""
import base64
import hashlib
import json
import os
import re
from pathlib import Path

import pytest
from django.conf import settings

from API import lapse_filing
from API.models import APIUserKey
from lapse_accounts.tests.conftest import _TMP  # noqa: F401  (the conftest's Django setup)

ROOT = Path(__file__).resolve().parents[2]
PAGE_CODES = ("ep", "cn", "jp", "kr", "ca", "au", "in", "gb", "br", "mx")


def find_repo_dir():
    if os.environ.get("LAPSE_REPO_DIR") and (Path(os.environ["LAPSE_REPO_DIR"]) / "lapse" / "filing.py").is_file():
        return os.environ["LAPSE_REPO_DIR"]
    for cand in (ROOT.parent / "lapse", ROOT.parent / "patentref", ROOT.parent / "pr", Path("/srv/lapse")):
        if (cand / "lapse" / "filing.py").is_file():
            return str(cand)
    return None


REPO_DIR = find_repo_dir()


@pytest.fixture
def filing_env():
    if not REPO_DIR:
        pytest.fail("the filing engine lapse.filing lives in the patentref repo: set LAPSE_REPO_DIR to a patentref checkout "
                    "that has lapse/filing.py", pytrace=False)
    old = getattr(settings, "LAPSE_REPO_DIR", None)
    settings.LAPSE_REPO_DIR = REPO_DIR
    lapse_filing._local.__dict__.clear()
    try:
        yield REPO_DIR
    finally:
        settings.LAPSE_REPO_DIR = old
        lapse_filing._local.__dict__.clear()


@pytest.fixture
def opkey():
    _, key = APIUserKey.objects.create_key(name="filing-op", username="filing-op-" + os.urandom(4).hex(), email="op-filing@example.test")
    return key


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def walk(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


@pytest.mark.parametrize("path", ["/filing/", "/filing", "/filing/cn/", "/filing/ep/", "/filing/mx"])
def test_pages_answer_without_a_key_with_their_own_policy(client, filing_env, path):
    r = client.get(path)
    assert r.status_code == 200 and r["Content-Type"] == "text/html; charset=utf-8"
    html = r.content.decode("utf-8")
    csp = r["Content-Security-Policy"]
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    styles = re.findall(r"<style>(.*?)</style>", html, re.S)
    assert len(scripts) == 1 and len(styles) == 1
    assert f"script-src {_sha(scripts[0])}" in csp and f"style-src {_sha(styles[0])}" in csp
    assert "connect-src 'self'" in csp and "default-src 'none'" in csp
    assert r["Cache-Control"] == "public, max-age=300" and r["Content-Language"] == "en"
    assert '<link rel="canonical" href="https://patentref.io/filing/' in html and 'hreflang="x-default"' in html
    assert "not legal advice" in html and "| PatentRef</title>" in html and html.count("| PatentRef</title>") == 1
    assert 'id="fl-data"' in html and "application/ld+json" in html
    assert 'og:url" content="https://patentref.io/filing/' in html   # the link preview


def test_office_pages_name_their_file(client, filing_env):
    for code in PAGE_CODES:
        r = client.get(f"/filing/{code}/")
        assert r.status_code == 200, code
        assert r["Link"] == f'<https://patentref.io/filing/{code}.json>; rel="alternate"; type="application/json"'
        assert f'<link rel="alternate" type="application/json" href="/filing/{code}.json">' in r.content.decode("utf-8")


@pytest.mark.parametrize("path", ["/filing/zz/", "/filing/us/", "/filing/pct/", "/filing/zz.json", "/filing/EP/"])
def test_codes_without_a_page_or_file_answer_404(client, filing_env, path):
    assert client.get(path).status_code == 404


def test_post_is_refused(client, filing_env):
    assert client.post("/filing/").status_code == 405 and client.post("/filing/cn.json").status_code == 405


def test_the_files_the_pages_read(client, filing_env):
    from lapse import filing

    st = filing.store(REPO_DIR)
    for code in PAGE_CODES + ("us", "pct"):
        r = client.get(f"/filing/{code}.json")
        assert r.status_code == 200 and r["Content-Type"] == "application/json; charset=utf-8", code
        assert r["Access-Control-Allow-Origin"] == "*" and r["Cache-Control"] == "public, max-age=3600"
        body = json.loads(r.content.decode("utf-8"))
        assert body == st.public(code)
        keys = {k for k, _ in walk(body)}
        assert not keys & {"held", "held_reason", "held_fields", "quote", "dropped_questions"}, code
        if code in PAGE_CODES:
            assert r["Link"] == f'<https://patentref.io/filing/{code}/>; rel="alternate"; type="text/html"'
    r = client.get("/filing/rates.json")
    rates = r.json()
    assert r.status_code == 200 and rates["date"] and rates["source"] and rates["to_usd"]["USD"] == 1


def test_the_api_paths_need_a_key(client, filing_env):
    assert client.get("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "cn"}).status_code == 403
    assert client.get("/api/v1/filing/estimate/", {"countries": "cn"}).status_code == 403


def test_timeline_answer(client, filing_env, opkey):
    r = client.get("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "ep,cn,gb,ca"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 200 and r["Content-Type"] == "application/json"
    a = r.json()
    assert {"date", "basis", "route", "generated_at", "data_versions", "rows", "stale"} <= set(a)
    assert a["date"] == "2026-08-31" and a["route"] == "pct" and a["basis"] == "priority"
    # the filing data's date in its own header; X-Data-Version stays the search file's, as on every path
    assert r["X-Filing-Data-Version"] == max(a["data_versions"].values())
    assert r.get("X-Data-Version") != r["X-Filing-Data-Version"]
    dates = [row["date"] for row in a["rows"]]
    assert dates == sorted(dates)
    row_keys = {"date", "country_code", "country", "event_id", "event", "months", "days", "basis", "fee", "extension",
                "source_url", "source_title", "last_checked", "stale", "page_url"}
    assert all(row_keys <= set(row) for row in a["rows"])
    got = {(row["country_code"], row["event_id"]): row["date"] for row in a["rows"]}
    assert got[("cn", "cn-pct-entry")] == "2029-02-28" and got[("ep", "pct-entry-31m")] == "2029-03-31"
    assert got[("cn", "cn-pct-entry-late")] == "2029-04-30" and got[("ca", "ca-pct-late-entry-outer")] == "2030-02-28"
    assert "not legal advice" in a["notice"]


def test_timeline_without_countries_is_empty(client, filing_env, opkey):
    r = client.get("/api/v1/filing/timeline/", {"date": "2026-08-31"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 200 and r.json()["rows"] == []


def test_timeline_as_a_calendar_file(client, filing_env, opkey):
    r = client.get("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "jp", "as": "ics"}, HTTP_X_API_KEY=opkey)
    text = r.content.decode("utf-8")
    assert r.status_code == 200 and r["Content-Type"] == "text/calendar; charset=utf-8"
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n") and "DTSTART;VALUE=DATE:20290228" in text
    assert all(len(line.encode("utf-8")) <= 75 for line in text.split("\r\n"))


def test_estimate_answer_and_csv(client, filing_env, opkey):
    r = client.get("/api/v1/filing/estimate/", {"countries": "ca,kr,au", "claims": "20", "pages": "30", "small_entity": "true",
                                               "route": "pct", "currency": "USD"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 200
    a = r.json()
    assert {"inputs", "rates", "generated_at", "countries", "total"} <= set(a) and a["rates"]["source"] and a["rates"]["date"]
    for c in a["countries"]:
        assert {"code", "country", "office", "currency", "required", "suggested", "total", "lines"} <= set(c)
        for ln in c["lines"]:
            assert ln["group"] in ("required", "suggested") and ln["kind"] in ("official", "service")
            assert ln["converted"]["currency"] == "USD" and ln["source_url"].startswith("http")
    assert a["total"]["currency"] == "USD" and "quote" not in json.dumps(a).replace("not a quote", "")
    r = client.get("/api/v1/filing/estimate/", {"countries": "ca", "as": "csv"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 200 and r["Content-Type"] == "text/csv; charset=utf-8" and r.content.startswith(b"country,group,kind,")


@pytest.mark.parametrize("path,params,param", [
    ("/api/v1/filing/timeline/", {"date": "2026-02-30", "countries": "cn"}, "date"),
    ("/api/v1/filing/timeline/", {"date": "31/08/2026", "countries": "cn"}, "date"),
    ("/api/v1/filing/timeline/", {"countries": "cn"}, "date"),
    ("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "cn,zz"}, "countries"),
    ("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "cn", "route": "air"}, "route"),
    ("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "cn", "filed": "2028-01-01"}, "filed"),
    ("/api/v1/filing/timeline/", {"date": "2026-08-31", "country": "cn"}, "country"),
    ("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "cn", "as": "xml"}, "as"),
    ("/api/v1/filing/estimate/", {"countries": "cn", "claims": "0"}, "claims"),
    ("/api/v1/filing/estimate/", {"countries": "cn", "pages": "many"}, "claims"),
    ("/api/v1/filing/estimate/", {"countries": "cn", "currency": "XXX"}, "currency"),
    ("/api/v1/filing/estimate/", {"countries": "cn", "small_entity": "maybe"}, "small_entity"),
    ("/api/v1/filing/estimate/", {"countries": "cn", "as_of": "2027-13-01"}, "as_of"),
])
def test_bad_input_answers_problem_details(client, filing_env, opkey, path, params, param):
    r = client.get(path, params, HTTP_X_API_KEY=opkey)
    assert r.status_code == 400 and r["Content-Type"] == "application/problem+json"
    p = r.json()
    assert p["type"] == "https://patentref.io/docs/errors/ERR_Q" and p["status"] == 400 and p["param"] == param
    assert p["instance"] == path and p["detail"] and r["X-Status-Reason-Code"] == "ERR_Q" and r["X-Status-Reason"] == p["detail"]


def test_a_repeated_parameter_is_refused(client, filing_env, opkey):
    r = client.get("/api/v1/filing/timeline/?date=2026-08-31&date=2026-09-01&countries=cn", HTTP_X_API_KEY=opkey)
    assert r.status_code == 400 and r.json()["param"] == "date"


def test_a_newline_in_a_parameter_never_reaches_a_header(client, filing_env, opkey):
    r = client.get("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "cn\r\nX-Evil: 1"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 400 and "\n" not in r["X-Status-Reason"] and "X-Evil" not in r


def test_engine_unavailable_answers_503(client, filing_env, opkey, monkeypatch):
    monkeypatch.setattr(lapse_filing, "filing_module", lambda: None)
    r = client.get("/api/v1/filing/timeline/", {"date": "2026-08-31", "countries": "cn"}, HTTP_X_API_KEY=opkey)
    assert r.status_code == 503 and r.json()["code"] == "ERR_ES"
    from lapse_accounts import filing_pages

    monkeypatch.setattr(filing_pages, "pages_module", lambda: None)
    assert client.get("/filing/").status_code == 503


FOOTER_LINK = '<nav aria-label="Site"><a href="/filing/">Foreign filing</a> <a href="/api/v1/meta/status/">Status</a>'


@pytest.mark.parametrize("path", ["/", "/agents/", "/docs/legal/", "/docs/legal/terms", "/filing/", "/filing/cn/", "/waitlist/"])
def test_every_page_links_to_the_filing_pages_in_its_footer(client, filing_env, path):
    r = client.get(path)
    assert r.status_code == 200, path
    assert r.content.decode("utf-8").count(FOOTER_LINK) == 1, path
    assert client.get("/filing/").status_code == 200


def test_the_home_and_agents_pages_name_the_filing_pages(client, filing_env):
    home, agents = (client.get(p).content.decode("utf-8") for p in ("/", "/agents/"))
    assert '<li><a href="/filing/">Foreign filing: PCT and national phase deadlines and costs for ten patent offices</a></li>' in home
    assert '<p>Foreign filing: <a href="/filing/">/filing/</a> lists the PCT and national phase deadlines' in agents


def test_the_estimate_names_its_rates_in_every_form(client, filing_env, opkey):
    a = client.get("/api/v1/filing/estimate/", {"countries": "ep,jp", "currency": "USD"}, HTTP_X_API_KEY=opkey).json()
    assert a["rates"]["source"] and a["rates"]["date"]
    lines = client.get("/api/v1/filing/estimate/", {"countries": "ep,jp", "as": "csv"}, HTTP_X_API_KEY=opkey).content.decode("utf-8").splitlines()
    assert lines[0].endswith(",rate_source,rate_date") and all(ln.endswith("," + a["rates"]["date"]) for ln in lines[1:])
