"""Release AD (PatentRef 1.22.0): the page for agents at /agents/; the record page's address without a number, a redirect to
the docs; every page header's as-of date, the build facts' as_of; the triage example drawn on the server; the status page's
words for an open alert and for refused requests; the key note beside the record page's API links.

  python -m pytest lapse_accounts/tests/test_site_pages.py -q -p no:django
"""
import hashlib
import html
import json
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.conf import settings
from django.template.loader import render_to_string
from django.urls import Resolver404, resolve

from API import lapse_wording
from lapse_accounts import metering, public, site_pages, status, triage_page
from lapse_accounts.models import PLAN_FREE

HERE = Path(__file__).resolve().parent
README = (HERE / "fixtures" / "patentref_mcp_0.4.0_README.md").read_text(encoding="utf-8").replace("\r\n", "\n")
README_SHA256 = "284a6642d2059dc6efa231c88f57656a509cf912a553155c31ae0f11d65dfb26"   # PyPI's description of patentref-mcp 0.4.0
TEMPLATES = HERE.parent / "templates" / "lapse_accounts"
DASHES = (chr(0x2014), chr(0x2013))
# The words the coordinator bars from release AD's copy (2026-11-05), whole words, any case, with their endings.
AD_WORDS = re.compile(r"\b(risk\w*|clear\w*|safe\w*|free to use|invalid\w*|valid\w*|scope\w*|breadth\w*|strength\w*|broad\w*|"
                      r"narrow\w*|infring\w*|no other|first)\b", re.I)
NEXT = ("Next: for each patent, the problem it states and the parts it names, each quoted with its place in the text, and linked "
        "to other patents that name the same parts. Not live yet. If your agent would use it, tell us at hello@patentref.io.")


class Visible(HTMLParser):
    """The text a reader is given: text outside script and style, and the aria-label, title, alt and meta description."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("script", "style"):
            self.skip += 1
        for k in ("aria-label", "title", "alt"):
            if a.get(k):
                self.out.append(" " + a[k] + " ")
        if tag == "meta" and a.get("name") == "description":
            self.out.append(" " + (a.get("content") or "") + " ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def visible(h):
    v = Visible()
    v.feed(h)
    v.close()
    return re.sub(r"\s+", " ", "".join(v.out))


def plain(s):
    """markdown or HTML to its words: no backticks, no tags, entities read, spaces collapsed"""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s.replace("`", "")))).strip()


def readme_tools():
    """(call, endpoint, description) of each line of the README's Tools section, its formatting taken out."""
    part = README.split("## Tools\n", 1)[1].split("\n## ", 1)[0]
    out = []
    for ln in part.splitlines():
        if not ln.startswith("- `"):
            continue
        m = re.fullmatch(r"- (.+?): (.+)\. `(GET [^`]+)`", ln)
        assert m, ln
        out.append((m.group(1).replace("`", ""), m.group(3), m.group(2).replace("`", "")))
    return out


def page(client, path="/agents/"):
    r = client.get(path)
    return r, r.content.decode("utf-8")


def _reset(monkeypatch):
    """A worker's status request with nothing held: no answer and no 30-day figures kept (as test_status.py's)."""
    monkeypatch.setattr(status, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(status, "_figures_30d", {"at": 0.0, "path": None, "data": None})


# ---------------------------------------------------------------- the README copy

def test_the_readme_fixture_is_the_one_pypi_serves():
    assert hashlib.sha256(README.encode("utf-8")).hexdigest() == README_SHA256
    assert "<!-- mcp-name: io.github.patentref/patentref-mcp -->" in README


# ---------------------------------------------------------------- /agents/

def test_the_agents_page_answers_with_its_own_policy(client):
    r, h = page(client)
    assert r.status_code == 200 and r["Content-Type"] == "text/html; charset=utf-8" and r["Cache-Control"] == "public, max-age=300"
    assert r["Content-Security-Policy"] == triage_page.csp_for(h)
    csp = r["Content-Security-Policy"]
    assert "default-src 'none'" in csp and "script-src 'sha256-" in csp and "style-src 'sha256-" in csp and "unsafe" not in csp
    assert h.count("<script>") == 1 and h.count("<style>") == 1 and " style=" not in h and " on" + "click=" not in h
    assert client.get("/agents").status_code == 200
    assert client.post("/agents/").status_code == 405
    assert resolve("/agents/").func is site_pages.agents and resolve("/agents").func is site_pages.agents
    assert '<link rel="canonical" href="https://patentref.io/agents/">' in h
    assert re.search(r"<title>Agents: the MCP server and the machine-readable files \| PatentRef</title>", h)
    # the header as every page has it (release AD leaves it as it is)
    assert '<header class="pr-header">' in h and '<a class="pr-name" href="/">PatentRef</a>' in h
    assert re.search(r'<p class="pr-asof">Data version [^<]+\. Free beta\.</p>', h)


def test_the_agents_page_says_what_the_canvas_says(client):
    _, h = page(client)
    v = visible(h)
    assert "<h1>PatentRef for agents</h1>" in h
    assert ("An agent can get a claim tree, a brief, a status record, a search over US grants, the patents nearest to a text, rows "
            "of patent office rules and the build facts of the data it is reading, each answer stamped with X-Data-Version. Nothing "
            "PatentRef returns is a legal conclusion; every answer describes the text as printed.") in v
    assert ("Registry: io.github.patentref/patentref-mcp (listed 2026-11-05) · PyPI patentref-mcp 0.4.0 · MIT · a local stdio "
            "package, no hosted endpoint") in v
    assert '<a href="https://pypi.org/project/patentref-mcp/0.4.0/">PyPI patentref-mcp 0.4.0</a>' in h
    for heading in ("Install", "Tools", "Key rule and limits", "Machine-readable"):
        assert re.search(r"<h2[^>]*>" + heading + "</h2>", h), heading
    monthly, per_minute = metering.plan_limits(PLAN_FREE)
    assert (f"Every data request carries X-Api-Key; without it the API answers 403. A free key allows {monthly:,} requests a month "
            f"and {per_minute} a minute. Identify a scripted client in its User-Agent with a name and a way to reach you, so the "
            f"operator can contact a runaway client.") in v
    # coordinator fix 1: no hosted MCP endpoint is named; the approved PyPI sentence is not on the page
    assert "/mcp/" not in h and "Also answers" not in h and not re.search(r"patentref\.io/mcp", h)
    assert README.split("\n\n")[2] not in v and "we know of" not in v
    # the rail names only files this site serves
    assert "/docs/llms.txt" not in h and "page.html.md" not in h and "describedby" not in h
    for path in site_pages.MACHINE_READABLE:
        assert f'<li><a href="{path}">{path}</a></li>' in h, path
        try:
            resolve(path)
        except Resolver404:
            pytest.fail(f"{path} has no route")


def test_install_lines_and_the_smoke_test_word_for_word_from_the_readme(client):
    run = README.split("## Run\n\n```\n", 1)[1].split("\n```", 1)[0]
    assert site_pages.RUN_BLOCK == run
    desktop = README.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert site_pages.CONFIG_JSON == desktop and "Claude Desktop (`claude_desktop_config.json`):" in README
    assert f"Check an install: `{site_pages.SMOKE}` starts the server over stdio" in README
    _, h = page(client)
    blocks = [html.unescape(b) for b in re.findall(r"<pre><code>(.*?)</code></pre>", h, re.S)]
    assert blocks == [b for _, _, b in site_pages.INSTALL]
    names = re.findall(r'<div class="ag-panel" id="([a-z-]+)"><h3>([^<]+)</h3>', h)
    assert names == [(i, n) for i, n, _ in site_pages.INSTALL] and [n for _, n in names] == ["Claude Desktop", "Claude Code", "Cursor", "Generic stdio"]
    for block in blocks:
        for ln in block.splitlines():
            if ln and not ln.startswith(("# ", "claude mcp add ")):
                assert ln in README, ln   # every line but a file's name and Claude Code's own command is the README's
    code = blocks[1].splitlines()[1]
    assert code == "claude mcp add patentref -e PATENTREF_API_KEY=... -- patentref-mcp"   # Claude Code's documented form
    for words in ("Check an install:", "Environment:"):
        sentence = next(s for s in README.split("\n\n") if s.startswith(words))
        assert plain(sentence) in visible(h), words


def test_the_tools_table_is_the_readmes_word_for_word(client):
    rows = readme_tools()
    assert rows == [(c, e, d) for c, e, d in site_pages.TOOLS] and len(rows) == 8
    _, h = page(client)
    body = h.split('<table class="ag-tools">', 1)[1].split("</table>", 1)[0]
    got = re.findall(r'<tr><td class="mono">(.*?)</td><td class="mono ep">(.*?)</td><td>(.*?)</td></tr>', body)
    assert [tuple(html.unescape(x) for x in r) for r in got] == rows
    assert re.findall(r"<th>([^<]+)</th>", body) == ["Tool", "Endpoint", "Description"]
    v = visible(h)
    for words in ("Each tool sends one HTTPS request to the public API with the key from its environment and returns the API's JSON as the API sent it.",
                  "An error from the API comes back as the tool's error text: the HTTP status and the API's X-Status-Reason. The server does not retry."):
        assert words in plain(README) and words in v, words
    assert "Tool names, endpoints and descriptions as the patentref-mcp 0.4.0 README on PyPI prints them." in v
    # each landmark has a name of its own (axe landmark-unique): the tools section is named by its heading, the table's scroll box by its own label
    named = re.findall(r'<(?:section|div)[^>]*?(?:aria-labelledby="([^"]+)"|aria-label="([^"]+)")', h)
    keys = [a or "label:" + b for a, b in named]
    assert len(keys) == len(set(keys)) and "label:Tools table" in keys, keys


def test_the_next_block_is_the_coordinators_text_between_the_tools_and_the_key_rule(client):
    _, h = page(client)
    v = visible(h)
    assert v.count(NEXT) == 1
    assert ('<p class="ag-next" id="ag-next"><strong>Next:</strong> ' + site_pages.NEXT_TEXT
            + ' <a href="mailto:hello@patentref.io">hello@patentref.io</a>.</p>') in h
    assert h.index('<table class="ag-tools">') < h.index('id="ag-next"') < h.index('id="ag-key-h"')
    block = h.split('id="ag-next"', 1)[1].split("</p>", 1)[0]
    assert "waitlist" not in block and not re.search(r"\d{4}-\d{2}-\d{2}|\$|USD|price", block)
    assert not AD_WORDS.search(NEXT) and not any(d in NEXT for d in DASHES)


def test_the_live_values_are_read_when_the_page_is_made(client, monkeypatch, tmp_path):
    from lapse_accounts.views import _data_version

    served = _data_version()[0]
    side = tmp_path / "snapshot_current.json"
    side.write_text(json.dumps({"data_version": served, "release": "20261027"}), encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path), "current_sidecar": str(side)})
    monkeypatch.setattr(site_pages, "_text_data_version", lambda: "20991231.9")
    monkeypatch.setattr(site_pages, "_structure_version", lambda: "7")
    _, h = page(client)
    assert (f"Data version {served}, as of 2026-10-27; text data version 20991231.9; claim trees by structure version 7. "
            f"Build facts: /api/v1/meta/. What changed: /changes.txt.") in visible(h)
    assert public.meta_facts()["as_of"] == "2026-10-27"
    monkeypatch.setattr(site_pages, "_text_data_version", lambda: None)
    monkeypatch.setattr(site_pages, "_structure_version", lambda: None)
    _, h = page(client)
    assert f"Data version {served}, as of 2026-10-27. Build facts:" in visible(h)


def test_the_agents_page_words_colours_and_fonts(client):
    _, h = page(client)
    v = visible(h)
    assert not AD_WORDS.search(v), AD_WORDS.findall(v)
    assert not lapse_wording.structure_words(v) and not lapse_wording.check_banned(h)
    assert not any(d in h for d in DASHES)
    css = (TEMPLATES / "agents.html").read_text(encoding="utf-8").split("{% block css %}", 1)[1].split("{% endblock %}", 1)[0]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgb|hsl", css)          # tokens only: no colour of its own, so no red and no green
    for words in ("@font-face", "fonts.googleapis", "fonts.gstatic", "<img", "<svg", "url("):
        assert words not in h, words
    assert not re.search(r"font-family:[^;}]*Inter\b", h)


# ---------------------------------------------------------------- /p/ without a number

def test_the_record_address_without_a_number_redirects_to_the_docs(client):
    for path in ("/p/", "/p"):
        r = client.get(path)
        assert r.status_code == 302 and r["Location"] == "/docs/" and r["Cache-Control"] == "public, max-age=300", path
    assert client.head("/p/").status_code == 302
    assert client.post("/p/").status_code == 405
    assert resolve("/p/").func is site_pages.record_index
    assert resolve("/p/10000000").url_name == "record-page" and resolve("/p/10000000/extracted/").url_name == "extract-page"


# ---------------------------------------------------------------- the header's as-of date

def test_release_and_as_of_follows_the_build_facts_rule():
    assert public.release_and_as_of("20261027.2", {}, {}) == ("20261027", "2026-10-27")
    assert public.release_and_as_of("20261027.2", {"release": "20261020"}, {}) == ("20261020", "2026-10-20")
    assert public.release_and_as_of("20261027.2", {"release": "20261020"}, {"data_version": "20261027.2", "release": "20261026"}) == ("20261026", "2026-10-26")
    assert public.release_and_as_of("20261027.2", {}, {"data_version": "20261013.1", "release": "20261013"}) == ("20261027", "2026-10-27")
    assert public.release_and_as_of("search", {}, {}) == ("", None)


def test_every_page_header_shows_the_build_facts_as_of_not_the_files_day(client, monkeypatch, tmp_path):
    from lapse_accounts.views import _data_version

    served = _data_version()[0]
    side = tmp_path / "snapshot_current.json"
    side.write_text(json.dumps({"data_version": served, "release": "20261027"}), encoding="utf-8")
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path), "current_sidecar": str(side),
                                                         "backup_journal": str(tmp_path / "bj.jsonl")})
    db = settings.LAPSE_SQLITE["path"]
    old = os.stat(db)
    day = time.mktime((2026, 10, 28, 12, 0, 0, 0, 0, -1))   # the day the build was made, a day after its as-of date
    os.utime(db, (day, day))
    _reset(monkeypatch)
    try:
        assert client.get("/api/v1/meta/").json()["as_of"] == "2026-10-27"
        assert _data_version() == (served, "2026-10-27")
        for path in ("/", "/try/", "/agents/", "/api/v1/meta/signup/", "/api/v1/meta/status/", "/docs/legal/terms", "/waitlist/"):
            h = client.get(path, HTTP_ACCEPT="text/html").content.decode("utf-8")
            assert f"Data version {served}, as of 2026-10-27. Free beta." in h, path
            assert "as of 2026-10-28" not in h, path
    finally:
        os.utime(db, (old.st_atime, old.st_mtime))


# ---------------------------------------------------------------- the triage example, drawn on the server

def _example_body():
    def part(kind, pid, n, start, end, text, label=None):
        return {"part": kind, "claim": n, "claim_id": f"US{pid}-c{n}", "part_id": f"US{pid}-c{n}-{kind}-{start}", "label": label,
                "text": text, "start": start, "end": end}
    built = {"row": 0, "patent_id": "11773614", "found": True, "built": True, "title": "Pool cleaner with a drive motor",
             "permalink": "https://patentref.io/p/11773614", "patent_date": "2023-10-03", "note": None,
             "status": {"status": "active", "basis": ["term_20y"], "expiry_date_estimated": "2041-03-01", "lapse_date": None,
                        "reinstated_date": None, "reinstatement_possible": False, "as_of": "2026-10-27"},
             "cells": [{"key": "drive motor", "position": "every_independent", "match": "exact", "independent_claims": [1, 12],
                        "dependent_claims": [3, 4, 5, 6, 7, 8, 9, 10, 11], "claim_keys": ["drive motor"]},
                       {"key": "filter", "position": "absent", "match": None, "independent_claims": [], "dependent_claims": [],
                        "claim_keys": []},
                       {"key": "housing", "position": "only_dependent", "match": "partial", "independent_claims": [],
                        "dependent_claims": [2], "claim_keys": ["upper housing"]}],
             "claims": {"total": 12, "independent": [1, 12]},
             "claim_parts": [{"claim": 1, "claim_id": "US11773614-c1", "category": "apparatus",
                              "parts": [part("preamble", "11773614", 1, 0, 20, "A pool cleaner"), part("transition", "11773614", 1, 21, 31, "comprising"),
                                        part("element", "11773614", 1, 32, 50, "a drive motor", "a")]}]}
    unbuilt = {"row": 1, "patent_id": "6601255", "found": True, "built": False, "title": "An older cleaner", "permalink": "https://patentref.io/p/6601255",
               "patent_date": "2003-08-05", "note": "no_claim_text",
               "status": {"status": "expired_term", "basis": ["term_20y"], "expiry_date_estimated": "2021-07-30", "lapse_date": None,
                          "reinstated_date": None, "reinstatement_possible": False, "as_of": "2026-10-27"},
               "cells": [], "claims": None, "claim_parts": []}
    return {"complete": True, "status_available": True, "data_version": "20261027.2", "text_data_version": "20261027.3",
            "as_of": "2026-10-27", "time_limit_seconds": 20,
            "counts": {"patents": 2, "found": 2, "built": 1, "not_built": 1},
            "input": {"kind": "list", "unread_count": 0, "unread": [], "unread_components": []},
            "components": [{"key": "drive motor", "from": "request"}, {"key": "filter", "from": "request"}, {"key": "housing", "from": "table"}],
            "export_columns": ["patent_id"], "patents": [built, unbuilt]}


def test_the_triage_example_is_drawn_on_the_server(client, monkeypatch):
    body = _example_body()
    monkeypatch.setattr(triage_page, "example_table", lambda request: (body, None))
    r = client.get("/triage/example/")
    h = r.content.decode("utf-8")
    assert r.status_code == 200 and r["Content-Security-Policy"] == triage_page.csp_for(h)
    table = h.split('<table id="tt-table">', 1)[1].split("</table>", 1)[0]
    assert table.startswith('<caption id="tt-caption">The status of 2 patents and where their claims mention each component. '
                            'Scroll sideways for every column.</caption>')
    assert ("<thead><tr><th>Patent</th><th>Status</th><th>drive motor<span class=\"small\">from your list</span></th>"
            "<th>filter<span class=\"small\">from your list</span></th><th>housing<span class=\"small\">picked by the table</span></th>"
            "<th>Claims</th><th>Parts</th></tr></thead>") in table
    row = table.split('<tr id="tt-row-11773614">', 1)[1].split("</tr>", 1)[0]
    assert '<th><a class="pid" href="https://patentref.io/p/11773614">US 11773614</a><span class="small">Pool cleaner with a drive motor</span></th>' in row
    assert ("<td><strong>Active</strong><span class=\"small\">Basis: term_20y</span><span class=\"small\">Term end, computed: 2041-03-01</span>"
            "<span class=\"small\">Reinstatement possible: no</span></td>") in row
    assert ('<td class="pos-every_independent"><span class="pos-label">Every independent claim</span><span class="small">Claims: '
            'independent 1, 12; dependent 3, 4, 5, 6, 7, 8, 9, 10 and 1 more</span></td>') in row
    assert '<td class="pos-absent"><span class="pos-label">Not in the claims</span></td>' in row
    assert ('<td class="pos-only_dependent"><span class="pos-label">Dependent claims only</span><span class="small">Claims: dependent 2</span>'
            '<span class="small">Partial: upper housing</span></td>') in row
    assert row.endswith('<td>12 claims, 2 independent</td><td><span class="small">2 parts</span></td>')
    other = table.split('<tr id="tt-row-6601255">', 1)[1].split("</tr>", 1)[0]
    assert ('<td colspan="3">The long-text file in service has no claims for this patent (granted after its last grant date).</td>'
            '<td></td><td></td>') in other
    assert ('<p id="tt-summary">2 patents in the table, 1 with a claim tree. 1 without a tree; their rows say why. Data version '
            '20261027.2, claims from 20261027.3, as of 2026-10-27.</p>') in h
    assert '<section id="tt-result" aria-labelledby="tt-result-h">' in h and '<div class="row" id="tt-controls" hidden>' in h
    assert 'id="tt-example" type="application/json"' in h            # the script still draws its own table from the answer
    assert "The example table below comes drawn from the server." in h
    assert not any(d in h for d in DASHES) and not AD_WORDS.search(visible(table))


def test_the_triage_page_without_the_example_draws_nothing_on_the_server(client):
    h = client.get("/triage/").content.decode("utf-8")
    assert '<table id="tt-table"></table>' in h and '<section id="tt-result" aria-labelledby="tt-result-h" hidden>' in h
    assert "This page builds the table in your browser and needs JavaScript." in h


def test_the_server_and_the_page_script_use_the_same_words():
    js = (TEMPLATES / "_triage.js").read_text(encoding="utf-8")
    for d in (triage_page.POS_TEXT, triage_page.STATUS_TEXT, triage_page.NOTE_TEXT):
        for k, words in d.items():
            assert f'{k}: "{words}"' in js, (k, words)
    for words in ('" and where their claims mention each component. Scroll sideways for every column."', '"picked by the table"',
                  '"from your list"', '" claims, "', '" independent"', '"Basis: "', '"Term end, computed: "', '"Fee lapse: "',
                  '"Reinstated: "', '"Reinstatement possible: "', '"Claims: "', '"Partial: "', '" with a claim tree."',
                  '" without a tree; their rows say why."', '" not in the data in service."', '"Data version "', '", claims from "',
                  'byId("tt-controls").hidden = false;', 'var MARKABLE = {preamble: true, element: true, wherein_clause: true};'):
        assert words in js, words


# ---------------------------------------------------------------- the status page's words

def _alerts(tmp_path, check):
    (tmp_path / "reports").mkdir(exist_ok=True)
    (tmp_path / "reports" / "alerts.jsonl").write_text(json.dumps({"time": status.iso(time.time() - 60), "check": check, "state": "breach"}) + "\n")


def test_page_state_names_each_open_alert():
    alert = {"time": "2026-11-05T10:15:00Z", "check": "refresh_units", "state": "breach"}
    d = {"state": ["degraded", "1 alert(s) open on the box."], "alerts": {"open": [alert]}, "external_check": {"fresh": True, "state": "up"}}
    assert status.page_state(d) == ("degraded", "API up, 1 alert open on the box",
                                    "The API answers its health check on the box and UptimeRobot reports it up. Open on the box: a scheduled "
                                    "job of the box in a failed state, such as a data refresh step, the backup or a nightly check "
                                    "(refresh_units, since 2026-11-05 10:15 UTC).")
    two = dict(d, state=["degraded", "2 alert(s) open on the box."], external_check={},
               alerts={"open": [alert, {"time": "2026-11-05T11:00:00Z", "check": "new_check", "state": "breach"}]})
    st, heading, sentence = status.page_state(two)
    assert heading == "API up, 2 alerts open on the box" and "UptimeRobot" not in sentence and "new check (new_check, since 2026-11-05 11:00 UTC)" in sentence
    down = dict(d, state=["degraded", "UptimeRobot reports the API down from its network; the box itself answers."])
    assert status.page_state(down)[1] == "Degraded" and status.page_state(down)[2].endswith("Open on the box: " + status.alert_label("refresh_units")
                                                                                            + " (refresh_units, since 2026-11-05 10:15 UTC).")
    assert status.page_state({"state": ["ok", "All checks passing."], "alerts": {"open": []}}) == ("ok", "Operational", "All checks passing.")
    assert status.page_state({"state": ["down", "The last two external probes failed."], "alerts": {}})[1] == "Down"
    for check, label in status.ALERT_LABELS.items():   # no internal machinery on the public page (test_status.py's lists)
        assert not AD_WORDS.search(label) and "/data" not in label and "GB" not in label, check
        for words in ("desk", "checklist", "scaffold", "ops/", "gate ", "contract", "deploy", "audit", "bundle", "operator key"):
            assert words not in label.lower(), (check, words)


def test_the_status_page_names_the_alert_and_explains_refused_requests(client, monkeypatch, tmp_path):
    _reset(monkeypatch)
    monkeypatch.setattr(settings, "LAPSE_STATUS_PATHS", {"lapse_data": str(tmp_path), "api_dir": str(tmp_path),
                                                         "backup_journal": str(tmp_path / "bj.jsonl")})
    _alerts(tmp_path, "refresh_units")
    d = client.get("/api/v1/meta/status.json").json()
    assert d["state"] == ["degraded", "1 alert(s) open on the box."]       # status.json as it was
    assert d["alerts"]["open"][0]["check"] == "refresh_units" and "label" not in d["alerts"]["open"][0]
    h = client.get("/api/v1/meta/status/").content.decode("utf-8")
    v = visible(h)
    assert "API up, 1 alert open on the box" in v and "Degraded" not in v
    assert ("Open on the box: a scheduled job of the box in a failed state, such as a data refresh step, the backup or a nightly check "
            "(refresh_units, since ") in v
    assert "<th>Check</th><th>What it means</th><th>Since</th><th>State</th>" in h
    assert "<td><code>refresh_units</code></td><td>a scheduled job of the box in a failed state" in h
    assert "Refused: no key or an unknown key (403), over a limit (429)" in v and "Rejected" not in v and "rejected is" not in v
    assert ("A refused request is the key rule at work, not a failure of the service: every data request needs a key, a request "
            "without one is answered 403 at once without reading any data, and a key over its allowance is answered 429. Failures of "
            "the service are the server errors (5xx).") in v
    assert "refused is 403 (no key, or a key PatentRef does not know) and 429 (over a limit)" in v
    assert not AD_WORDS.search(v.split("Status ", 1)[1].split("External check", 1)[0])


# ---------------------------------------------------------------- the record page's key note

def test_the_record_page_says_its_api_links_need_a_free_key():
    rec = {"plain": "As of 2026-10-27, a sentence (data version 20261027.2).", "status": "active", "basis": ["term_20y"],
           "permalink": "https://patentref.io/p/12514139?v=20261027.2", "as_of": "2026-10-27", "data_version": "20261027.2",
           "reinstatement_possible": False, "reinstatement_basis": "none"}
    h = render_to_string("lapse_accounts/record.html", {"rec": rec, "pid": "12514139", "title": None, "superseded": None,
                                                        "data_version": "20261027.2", "as_of": "2026-10-27", "footer_lines": ["x"],
                                                        "family": None, "basis_text": "", "term_end_text": "", "status_json": "{}",
                                                        "claims": {"state": "no_text_file"}})
    for path, words in (("/api/v1/patent/12514139/", "Full record through the API"), ("/api/v1/lapse/status/12514139/", "This verdict as JSON"),
                        ("/api/v1/structure/claims/12514139/", "The claim tree as JSON"),
                        ("/api/v1/structure/brief/12514139/", "A short brief of this patent as JSON")):
        assert f'<li><a href="{path}">{words}</a> (needs a free key)</li>' in h, path
    assert h.count("(needs a free key)") == 4 and "(key required)" not in h
    assert ('<p class="muted" id="key-note">The four API links answer JSON to a client that sends a free key in the X-Api-Key header. '
            'A click in a browser sends no key, so the API answers it with 403. <a href="/api/v1/meta/signup/">Get a free key</a>.</p>') in h
    assert not lapse_wording.check_banned(h) and not AD_WORDS.search(visible(h.split("<h2>Links</h2>", 1)[1].split("<h2>Record</h2>", 1)[0]))
