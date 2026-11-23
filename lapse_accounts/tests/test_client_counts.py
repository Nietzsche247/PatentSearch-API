"""Release AH (PatentRef 1.25.0): the daily counts of client kinds (lapse_accounts/clients.py, lapse_accounts/requestlog.py and
manage.py client_counts). OFF by default (settings.LAPSE_CLIENT_COUNTS): no table, no count, the command writes nothing. On: one
count per (UTC day, area, kind), the service's own keys and tools as own, no User-Agent text, address, key, prefix or path
kept, at most MAX_KINDS_A_DAY kinds a day, rows kept as long as the request rows, and the command's daily file of counts.

  python -m pytest lapse_accounts/tests/test_client_counts.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import sqlite3
import time
from datetime import datetime, timezone
from io import StringIO

from django.conf import settings
from django.core.management import call_command
from django.http import HttpResponse
from django.test import RequestFactory

from lapse_accounts import clients, requestlog, status

KINDS = {
    None: "none", "": "none", "   ": "none",
    "patentref-mcp/0.5.0": "patentref-mcp/0.5.0",
    "patentref-mcp/0.5.0 (+https://patentref.io)": "patentref-mcp/0.5.0",
    "PatentRef-MCP/0.4.1": "patentref-mcp/0.4.1",
    "patentref-mcp/beta": "patentref-mcp/other",
    "patentref-skill/claim-tree/1.2.0": "patentref-skill/claim-tree/1.2.0",
    "patentref-skill/Claim Tree/1": "patentref-skill/other",
    "patentref-ah-build-check": "own",
    "lapse-contract-runner": "own",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ChatGPT-User/1.0; +https://openai.com/bot)": "agent/chatgpt",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-User/1.0; +Claude-User@anthropic.com)": "agent/claude",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Perplexity-User/1.0)": "agent/perplexity",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ClaudeBot/1.0; +claudebot@anthropic.com)": "ai-crawler/claudebot",
    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; GPTBot/1.2; +https://openai.com/gptbot)": "ai-crawler/gptbot",
    "CCBot/2.0 (https://commoncrawl.org/faq/)": "ai-crawler/ccbot",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)": "crawler/googlebot",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)": "crawler/bingbot",
    "Mozilla/5.0 (compatible; SomeNewBot/0.1)": "crawler/other",
    "curl/8.5.0": "tool/curl",
    "python-requests/2.32.3": "tool/python-requests",
    "python-httpx/0.28.1": "tool/python-httpx",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/131.0.0.0 Safari/537.36": "tool/headless-chrome",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36": "browser",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:132.0) Gecko/20100101 Firefox/132.0": "browser",
    "SomethingElse/1.0": "other",
}
AREAS = {"/api/v1/patent/": "data", "/api/v1/structure/units/1/": "data", "/api/v1/meta/health/": "meta", "/llms.txt": "file",
         "/openapi.json": "file", "/0123456789abcdef0123456789abcdef.txt": "file", "/p/10000000/": "page", "/": "page",
         "/agents/": "page", "/docs/legal/privacy": "page"}


def test_each_user_agent_has_its_kind():
    for ua, want in KINDS.items():
        assert clients.kind(ua) == want, (ua, clients.kind(ua))


def test_each_path_has_its_area():
    for path, want in AREAS.items():
        assert clients.area(path) == want, path


def test_the_counter_is_off_by_default():
    assert settings.LAPSE_CLIENT_COUNTS is False and clients.on() is False


def _mw(tmp_path, monkeypatch, name):
    path = str(tmp_path / name)
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    monkeypatch.setattr(settings, "USAGE_LOG_BACKEND", "sqlite")
    monkeypatch.setattr(requestlog, "writer", requestlog._Writer())
    mw = requestlog.RequestLogMiddleware(lambda req: HttpResponse("x"))
    mw.on = True
    return path, mw


def test_off_no_table_and_no_count(tmp_path, monkeypatch):
    path, mw = _mw(tmp_path, monkeypatch, "off.sqlite3")
    rf = RequestFactory()
    mw(rf.get("/api/v1/patent/", HTTP_USER_AGENT="curl/8.5.0"))
    mw(rf.get("/p/10000000/", HTTP_USER_AGENT=list(KINDS)[-3]))
    assert requestlog.writer._counts == {}
    requestlog.writer.flush()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 2   # the request log as before
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'client_counts'").fetchone()
    con.close()
    out = StringIO()
    call_command("client_counts", "day", "--out", str(tmp_path / "rows"), stdout=out)
    got = json.loads(out.getvalue())
    assert got["counter"] == "off" and got["written"] is False and not (tmp_path / "rows").exists()


def test_on_counts_kinds_per_day_and_area_and_keeps_no_user_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_CLIENT_COUNTS", True)
    monkeypatch.setattr(status, "_own_cache", {"at": time.time(), "prefixes": frozenset({"ownkey01"})})
    path, mw = _mw(tmp_path, monkeypatch, "on.sqlite3")
    rf = RequestFactory()
    fake = ".".join(("prefix12", "q" * 12))      # a made-up key: prefix, dot, rest
    own = ".".join(("ownkey01", "w" * 12))
    browser = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    mw(rf.get("/api/v1/patent/", HTTP_X_API_KEY=fake, HTTP_USER_AGENT="curl/8.5.0"))
    mw(rf.get("/api/v1/patent/", HTTP_X_API_KEY=fake, HTTP_USER_AGENT="curl/8.4.0"))
    mw(rf.get("/api/v1/structure/units/1/", HTTP_X_API_KEY=fake, HTTP_USER_AGENT="patentref-mcp/0.5.0"))
    mw(rf.get("/api/v1/patent/", HTTP_X_API_KEY=own, HTTP_USER_AGENT="python-httpx/0.28.1"))   # the service's own key
    mw(rf.get("/p/10000000/", HTTP_USER_AGENT=browser))
    mw(rf.get("/llms.txt", HTTP_USER_AGENT="Mozilla/5.0 (compatible; GPTBot/1.2; +https://openai.com/gptbot)"))
    mw(rf.get("/api/v1/meta/health/"))
    requestlog.writer.flush()
    con = sqlite3.connect(path)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = con.execute("SELECT day, area, kind, n FROM client_counts ORDER BY area, kind").fetchall()
    assert rows == [(day, "data", "own", 1), (day, "data", "patentref-mcp/0.5.0", 1), (day, "data", "tool/curl", 2),
                    (day, "file", "ai-crawler/gptbot", 1), (day, "meta", "none", 1), (day, "page", "browser", 1)]
    assert [r[1] for r in con.execute("PRAGMA table_info(client_counts)")] == ["day", "area", "kind", "n"]
    assert con.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 7
    dump = "\n".join(con.iterdump())
    counts_text = "".join(ln for ln in dump.splitlines() if "client_counts" in ln)
    for words in ("8.5.0", "8.4.0", "0.28.1", "Mozilla", "Windows", "prefix12", "ownkey01", "/p/", "/llms.txt", "q" * 12, "w" * 12):
        assert words not in counts_text, words          # no User-Agent text, key, prefix or path with a count
    assert "Mozilla" not in dump and "8.5.0" not in dump   # nor anywhere in the file
    con.close()
    mw(rf.get("/api/v1/patent/", HTTP_X_API_KEY=fake, HTTP_USER_AGENT="curl/8.5.0"))   # added to the day's count
    requestlog.writer.flush()
    con = sqlite3.connect(path)
    assert con.execute("SELECT n FROM client_counts WHERE area = 'data' AND kind = 'tool/curl'").fetchone()[0] == 3
    con.close()
    out = StringIO()
    call_command("client_counts", "day", "--date", day, "--out", str(tmp_path / "rows"), stdout=out)
    got = json.loads(out.getvalue())
    assert got["written"] is True and got["requests"] == 8
    row = json.loads((tmp_path / "rows" / f"clients_{day}.json").read_text(encoding="utf-8"))
    assert row["areas"] == {"data": {"patentref-mcp/0.5.0": 1, "tool/curl": 3}, "file": {"ai-crawler/gptbot": 1}, "meta": {"none": 1},
                            "page": {"browser": 1}} and row["own"] == {"data": 1} and row["counter"] == "on"
    out = StringIO()
    call_command("client_counts", "day", "--date", day, "--out", str(tmp_path / "rows"), stdout=out)
    assert json.loads(out.getvalue())["written"] is False


def test_a_day_counts_at_most_max_kinds_and_the_rest_as_other(monkeypatch):
    monkeypatch.setattr(clients, "MAX_KINDS_A_DAY", 3)
    w = requestlog._Writer()
    for k in ("tool/curl", "tool/wget", "browser", "agent/claude", "agent/chatgpt", "tool/curl"):
        w._count(("2026-11-25", "data", k))
    assert w._counts == {("2026-11-25", "data", "tool/curl"): 2, ("2026-11-25", "data", "tool/wget"): 1,
                         ("2026-11-25", "data", "browser"): 1, ("2026-11-25", "data", "other"): 2}


def test_counts_older_than_the_request_rows_are_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_CLIENT_COUNTS", True)
    path, mw = _mw(tmp_path, monkeypatch, "prune.sqlite3")
    con = requestlog.connect(path, clients=True)
    con.execute("INSERT INTO client_counts VALUES ('2020-01-01', 'data', 'tool/curl', 5)")
    con.commit()
    con.close()
    mw(RequestFactory().get("/api/v1/patent/", HTTP_USER_AGENT="curl/8.5.0"))
    requestlog.writer.flush()
    con = sqlite3.connect(path)
    assert [r[0] for r in con.execute("SELECT day FROM client_counts")] == [datetime.now(timezone.utc).strftime("%Y-%m-%d")]
    con.close()


def test_the_counter_never_costs_the_request_log_its_row(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LAPSE_CLIENT_COUNTS", True)
    path, mw = _mw(tmp_path, monkeypatch, "boom.sqlite3")

    def boom(*a, **k):
        raise RuntimeError("made up")
    monkeypatch.setattr(clients, "counter_key", boom)
    mw(RequestFactory().get("/api/v1/patent/", HTTP_USER_AGENT="curl/8.5.0"))
    requestlog.writer.flush()
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM request_log").fetchone()[0] == 1
    con.close()
