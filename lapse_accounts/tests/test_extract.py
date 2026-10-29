"""The on-demand extraction trial on the API (structure layer gate S1.1; API/lapse_extract.py, the routes in
lapse_accounts/root_urls.py, manage.py extract, the weekly report in lapse_accounts/usage_counts.py).

The engine is the patentref clone's lapse.extract (LAPSE_REPO_DIR when set, else a sibling checkout named ZD, lapse,
patentref or pr, or /srv/lapse, that holds lapse/extract/); its 20 known patents (lapse/extract/quality_patents/) fill a search file
and a long-text file in the build_text.py layout here. Every model call goes to the engine's local stand-in provider on
127.0.0.1: nothing is spent and no network is reached. Checks: dark by default (501 not enabled, nothing written, 404 for a
result, a job and the page), the key file and the worker's heartbeat needed, the whole path (202 with a job and the poll
address, the worker in this process, the job done, the result with its label and every quote at its place in the text in
service, the cache answering the next account with no new job and no new spend, the page), the daily limit per account
(429 with Retry-After), a job already waiting named again, both kill switches at once, the spend cap and a simulated
overspend, the errors, the key rule, the wording, the command and the weekly report.

  LAPSE_REPO_DIR=<patentref checkout> python -m pytest lapse_accounts/tests/test_extract.py -q -p no:cacheprovider -p no:django -o addopts=""
"""
import json
import os
import re
import sqlite3
import sys
import time
import uuid
import zlib
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from lapse_accounts.tests.conftest import _TMP

ROOT = Path(__file__).resolve().parents[2]


def find_repo_dir():
    def ok(p):
        return p and (Path(p) / "lapse" / "extract" / "__init__.py").is_file()

    if ok(os.environ.get("LAPSE_REPO_DIR")):
        return os.environ["LAPSE_REPO_DIR"]
    for cand in (ROOT.parent / "ZD", ROOT.parent / "lapse", ROOT.parent / "patentref", ROOT.parent / "pr", Path("/srv/lapse")):
        if ok(cand):
            return str(cand)
    return None


REPO_DIR = find_repo_dir()
if REPO_DIR and REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)
if not REPO_DIR:
    pytest.fail("the extraction engine lapse.extract lives in the patentref repo: set LAPSE_REPO_DIR to a patentref checkout "
                "that has lapse/extract/", pytrace=False)

from API import lapse_extract, lapse_structure, lapse_wording  # noqa: E402
from API.search_sqlite import LapseSQLiteSearch  # noqa: E402
from lapse.extract import LABEL, config, fake, ledger, prompt, quality, store, units, worker  # noqa: E402
from lapse_accounts import usage_counts  # noqa: E402

DATA_VERSION = "20261027.2"
TEXT_VERSION = "20261006.4"
FIX = quality.load_fixtures(quality.FIXTURES)
PIDS = [p["patent_id"] for p, _, _ in FIX]
NO_TEXT, UNKNOWN, DESIGN = "12999999", "9999998", "D1107373"
BASE = "/api/v1/structure/extract/"
BANNED = re.compile(r"\b(risk\w*|clear\w*|safe\w*|free to use|invalid\w*|valid\w*|scope\w*|breadth|strength\w*|broad\w*|"
                    r"narrow\w*|infring\w*|covers?|solves?)\b", re.I)


def build_files(search, text):
    for p in (search, text):
        if os.path.exists(p):
            os.unlink(p)
    con = sqlite3.connect(search)
    con.execute("CREATE TABLE _lapse_indices(idx TEXT PRIMARY KEY, tbl TEXT, key_field TEXT)")
    con.execute("CREATE TABLE _lapse_fields(idx TEXT, path TEXT, field TEXT, es_type TEXT, keyword_subfield INT)")
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", DATA_VERSION), ("release", "20261027")])
    con.execute("INSERT INTO _lapse_indices VALUES ('patents','patents','patent_id')")
    con.execute("CREATE TABLE patents(patent_id TEXT PRIMARY KEY, patent_zero_prefix TEXT, patent_title TEXT, patent_date TEXT, "
                "patent_type TEXT, withdrawn INT)")
    rows = [(p["patent_id"], p["patent_id"], p["title"], "2020-01-07", "utility") for p, _, _ in FIX]
    rows += [(NO_TEXT, NO_TEXT, "t", "2026-10-20", "utility"), (DESIGN, DESIGN, "Chair", "2025-12-30", "design")]
    con.executemany("INSERT INTO patents VALUES (?,?,?,?,?,0)", rows)
    con.commit()
    con.close()
    con = sqlite3.connect(text)
    con.execute('CREATE TABLE "g_claims" ("uuid" TEXT PRIMARY KEY, "patent_id" TEXT, "patent_zero_prefix" TEXT, "claim_number" TEXT, '
                '"claim_dependent" TEXT, "claim_text" BLOB, "claim_sequence" INTEGER, "exemplary" INTEGER)')
    con.execute('CREATE INDEX "ix_g_claims_patent_id_claim_sequence" ON "g_claims"("patent_id", "claim_sequence")')
    con.execute('CREATE TABLE "g_brf_sum_texts" ("uuid" TEXT PRIMARY KEY, "patent_id" TEXT, "patent_zero_prefix" TEXT, "summary_text" BLOB)')
    con.execute('CREATE INDEX "ix_g_brf_sum_texts_patent_id" ON "g_brf_sum_texts"("patent_id")')
    con.execute("CREATE TABLE _lapse_build(k TEXT, v TEXT)")
    con.executemany("INSERT INTO _lapse_build VALUES (?,?)", [("data_version", TEXT_VERSION), ("kind", "text")])
    for fx in (json.loads((quality.FIXTURES / f"{pid}.json").read_text(encoding="utf-8")) for pid in PIDS):
        pid = fx["patent_id"]
        for i, s in enumerate(fx["brief_summary"]):
            con.execute("INSERT INTO g_brf_sum_texts VALUES (?,?,?,?)", (f"{pid}-s{i}", pid, pid, zlib.compress(s.encode("utf-8"), 6)))
        for c in fx["claims"]:
            con.execute("INSERT INTO g_claims VALUES (?,?,?,?,?,?,?,?)",
                        (f"{pid}-{c['claim_sequence']}", pid, pid, str(c["claim_number"]), c["claim_dependent"] or None,
                         zlib.compress(c["claim_text"].encode("utf-8"), 6), int(c["claim_sequence"]), c["exemplary"]))
    con.commit()
    con.close()


@pytest.fixture(scope="module")
def files():
    search, text = str(_TMP / "extract_search.db"), str(_TMP / "extract_text.db")
    build_files(search, text)
    return search, text


@pytest.fixture(scope="module")
def prov():
    p = fake.FakeProvider({pa["patent_id"]: fake.answer_from_expected(e) for pa, e, _ in FIX}).start()
    yield p
    p.stop()


@pytest.fixture
def env(files, tmp_path, monkeypatch):
    """The test files in service, and a fresh trial folder and key file of this test's own (nothing there: dark)."""
    old = dict(settings.LAPSE_SQLITE)
    old_text = getattr(settings, "LAPSE_TEXT_PATH", None)
    settings.LAPSE_SQLITE["path"] = files[0]
    settings.LAPSE_TEXT_PATH = files[1]
    monkeypatch.setattr(settings, "LAPSE_REPO_DIR", REPO_DIR, raising=False)
    monkeypatch.setattr(settings, "LAPSE_EXTRACT_DIR", str(tmp_path / "extract"), raising=False)
    monkeypatch.setattr(settings, "LAPSE_EXTRACT_KEY_FILE", str(tmp_path / "extract.env"), raising=False)
    LapseSQLiteSearch._local.__dict__.clear()
    LapseSQLiteSearch._meta_cache.clear()
    lapse_structure.CACHE.clear()
    lapse_extract._local.__dict__.clear()
    try:
        yield config.Paths(tmp_path / "extract", tmp_path / "extract.env")
    finally:
        settings.LAPSE_SQLITE.clear()
        settings.LAPSE_SQLITE.update(old)
        settings.LAPSE_TEXT_PATH = old_text
        LapseSQLiteSearch._local.__dict__.clear()
        LapseSQLiteSearch._meta_cache.clear()


def switch_on(paths, prov, **over):
    """What the switch-on step leaves: config.json on with a model and the provider, the key file, the worker's heartbeat."""
    cfg = dict(config.DEFAULTS, enabled=True, model="fake/perfect", provider_url=prov.url, prices=dict(fake.FAKE_PRICES))
    cfg.update(over)
    config.write(paths.config, cfg)
    paths.key_file.write_text("EXTRACT_API_KEY=test-stand-in-value\n", encoding="utf-8")
    con = store.connect(paths.db, create=True)
    store.heartbeat(con, time.time(), 1)
    return con, config.load(paths.config)


def run_worker(paths, con):
    return worker.step(con, paths, lapse_extract.read_for_worker, use_child=False, log=lambda *_: None)


@pytest.fixture
def mint(client, token_factory):
    """A key of a fresh account (each account its own email domain: new accounts per domain and day are limited)."""
    def _mint():
        sub = str(uuid.uuid4())
        token = token_factory(sub=sub, email=f"user-{sub[:8]}@x{sub[:8]}.example.test")
        r = client.post("/api/v1/meta/keys/", data=json.dumps({"terms_version": settings.LAPSE_TERMS_VERSION}),
                        content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token)
        assert r.status_code == 201, r.content
        return r.json()["api_key"]
    return _mint


def post(client, key, pid, **kw):
    return client.post(BASE + pid + "/", HTTP_X_API_KEY=key, **kw)


def get(client, key, path, **kw):
    return client.get(path, HTTP_X_API_KEY=key, **kw)


# ------------------------------------------------------------------ dark by default

def test_dark_by_default_501_and_nothing_written(client, mint, env):
    key = mint()
    r = post(client, key, PIDS[0])
    assert r.status_code == 501 and r.json() == {"error": True}
    assert r["X-Status-Reason-Code"] == "ERR_NOT_IMPLEMENTED"
    assert r["X-Status-Reason"] == "The on-demand extraction trial is not enabled on this server."
    p = post(client, key, PIDS[0], HTTP_ACCEPT="application/problem+json").json()
    assert p["code"] == "ERR_NOT_IMPLEMENTED" and p["status"] == 501
    assert get(client, key, BASE + PIDS[0] + "/").status_code == 404
    assert get(client, key, BASE + "jobs/j" + "0" * 20 + "/").status_code == 404
    page = client.get(f"/p/{PIDS[0]}/extracted/")
    assert page.status_code == 404 and b"No machine-extracted map yet" in page.content
    assert not env.dir.exists() and not env.key_file.exists()


def test_off_without_the_engine_the_key_file_or_the_worker(client, mint, env, prov, monkeypatch):
    key = mint()
    con, cfg = switch_on(env, prov)
    env.key_file.unlink()
    assert post(client, key, PIDS[0]).status_code == 501
    env.key_file.write_text("EXTRACT_API_KEY=x\n", encoding="utf-8")
    con.execute("DELETE FROM meta")
    r = post(client, key, PIDS[0])
    assert r.status_code == 501 and "not enabled" in r["X-Status-Reason"]
    store.heartbeat(con, time.time() - 3600, 1)
    assert post(client, key, PIDS[0]).status_code == 501
    monkeypatch.setattr(lapse_extract, "engine_module", lambda: None)
    assert post(client, key, PIDS[0]).status_code == 501
    assert not con.execute("SELECT 1 FROM jobs").fetchall()


# ------------------------------------------------------------------ the whole path

def test_ask_poll_result_cache_and_page(client, mint, env, prov):
    con, cfg = switch_on(env, prov)
    pid = "6998784"
    key = mint()
    r = post(client, key, pid)
    assert r.status_code == 202, r.content
    js = r.json()
    assert js["status"] == "queued" and re.fullmatch(r"j[0-9a-f]{20}", js["job_id"]) and js["poll_after_seconds"] == 10
    assert r["Location"] == js["poll"] == f"{BASE}jobs/{js['job_id']}/" and r["Retry-After"] == "10"
    assert js["result_url"] == f"{BASE}{pid}/" and js["page"] == f"/p/{pid}/extracted/" and js["label"].startswith("Machine-extracted.")
    j = get(client, key, js["poll"]).json()
    assert j["status"] == "queued" and "result" not in j
    assert run_worker(env, con) == "ran done"
    key2 = mint()
    j = get(client, key2, js["poll"]).json()
    assert j["status"] == "done" and j["result"]["patent_id"] == pid and "poll_after_seconds" not in j
    res = get(client, key2, BASE + pid + "/")
    assert res.status_code == 200
    body = res.json()
    assert body["machine_extracted"] is True and body["label"] == LABEL
    assert body["structure_version"] == "s1-1" and body["model"] == "fake/perfect" and body["prompt_hash"] == prompt.prompt_hash(cfg)
    assert body["text_data_version"] == TEXT_VERSION and body["units"]
    patent = lapse_extract.read_for_worker(pid)
    for u in body["units"]:
        loc = u["locator"]
        assert units.text_of(patent, loc["field"], loc["sequence"])[loc["start"]:loc["end"]] == u["text"]
        assert u["structure_version"] == "s1-1" and u["model"] == "fake/perfect" and u["prompt_hash"] == body["prompt_hash"]
    spent, jobs = ledger.spent(con), store.job_counts(con)
    again = post(client, mint(), pid)
    assert again.status_code == 200 and again.json() == body
    assert ledger.spent(con) == spent and store.job_counts(con) == jobs
    page = client.get(f"/p/{pid}/extracted/")
    html = page.content.decode("utf-8")
    assert page.status_code == 200 and page["X-Robots-Tag"] == "noindex" and '<meta name="robots" content="noindex">' in html
    assert "Machine-extracted." in html and "No person has reviewed them." in html
    cord = next(u for u in body["units"] if u["type"] == "Component" and u["term"] == "cord")
    loc = cord["locator"]
    assert f"Term: cord. From claim 1, characters {loc['start']} to {loc['end']}; recited in claims 1, " in html
    first = next(u for u in body["units"] if u["type"] == "StatedPriorArtDrawback")
    assert f"From the brief summary, characters {first['locator']['start']} to {first['locator']['end']}." in html


def test_a_job_already_waiting_is_named_again(client, mint, env, prov):
    con, cfg = switch_on(env, prov)
    a = post(client, mint(), "9334234").json()
    b = post(client, mint(), "9334234").json()
    assert a["job_id"] == b["job_id"] and a["status"] == b["status"] == "queued"
    assert store.job_counts(con) == {"queued": 1}


def test_the_daily_limit_is_per_account_and_the_cache_is_not_counted(client, mint, env, prov):
    con, cfg = switch_on(env, prov)
    key = mint()
    for pid in PIDS[:3]:
        assert post(client, key, pid).status_code == 202
    r = post(client, key, PIDS[3])
    assert r.status_code == 429 and r.json() == {"error": True}
    assert "free daily limit of 3 new extractions" in r["X-Status-Reason"] and 0 < int(r["Retry-After"]) <= 86400
    assert post(client, key, PIDS[3], HTTP_ACCEPT="application/problem+json").json()["code"] == "THROTTLED"
    assert run_worker(env, con) == "ran done"
    assert post(client, key, PIDS[0]).status_code == 200   # made already: served, not counted
    assert post(client, mint(), PIDS[3]).status_code == 202   # another account


def test_either_kill_switch_stops_new_jobs_at_once(client, mint, env, prov):
    con, cfg = switch_on(env, prov)
    calls = len(prov.requests)
    key = mint()
    assert post(client, key, PIDS[0]).status_code == 202
    env.kill.write_text("", encoding="utf-8")
    r = post(client, key, PIDS[1])
    assert r.status_code == 501 and r["X-Status-Reason"].startswith("The on-demand extraction trial is paused.")
    assert run_worker(env, con) == "refused 1 (paused)"
    j = store.get_job(con, con.execute("SELECT job_id FROM jobs").fetchone()[0])
    assert j["status"] == "refused" and j["reason"].startswith("The on-demand extraction trial is paused.")
    env.kill.unlink()
    assert post(client, key, PIDS[1]).status_code == 202
    config.write(env.config, dict(cfg, kill=True))
    assert post(client, key, PIDS[2]).status_code == 501
    assert run_worker(env, con) == "refused 1 (paused)"   # the job queued while it was open is ended, never run
    assert len(prov.requests) == calls                     # no model call at all


def test_the_spend_cap_and_a_simulated_overspend(client, mint, env, prov):
    con, cfg = switch_on(env, prov)
    key = mint()
    assert post(client, key, PIDS[4]).status_code == 202
    ledger.reserve(con, "earlier", "api", "fake/perfect", 99.9999, 100.0, time.time())   # the ledger near the cap
    n = len(prov.requests)
    assert run_worker(env, con) == "refused 1 (cap)"
    assert len(prov.requests) == n and ledger.spent(con) == pytest.approx(99.9999)
    r = post(client, key, PIDS[5])
    assert r.status_code == 501 and r["X-Status-Reason"].startswith("The on-demand extraction trial has used its spend cap.")
    con.execute("DELETE FROM spend")
    ledger.reserve(con, "earlier", "api", "fake/perfect", 99.999, 100.0, time.time())   # under min_remaining: queue refused
    assert post(client, key, PIDS[5]).status_code == 501


def test_errors(client, mint, env, prov):
    switch_on(env, prov)
    key = mint()
    seen = []
    r = post(client, key, "abc-1")
    assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q"
    seen.append(r["X-Status-Reason"])
    r = get(client, key, BASE + "abc-1/")
    assert r.status_code == 400 and r["X-Status-Reason-Code"] == "ERR_Q"
    seen.append(r["X-Status-Reason"])
    r = post(client, key, UNKNOWN)
    assert r.status_code == 404 and r["X-Status-Reason-Code"] == "NOT_FOUND"
    seen.append(r["X-Status-Reason"])
    key = mint()
    r = post(client, key, NO_TEXT)
    assert r.status_code == 404 and r["X-Status-Reason"].startswith("No claim text")
    seen.append(r["X-Status-Reason"])
    r = post(client, key, DESIGN)
    assert r.status_code == 400 and "utility patents and reissues only" in r["X-Status-Reason"]
    seen.append(r["X-Status-Reason"])
    r = get(client, key, BASE + "jobs/nope/")
    assert r.status_code == 400
    seen.append(r["X-Status-Reason"])
    key = mint()
    r = get(client, key, BASE + "jobs/j" + "f" * 20 + "/")
    assert r.status_code == 404
    seen.append(r["X-Status-Reason"])
    r = get(client, key, BASE + PIDS[6] + "/")
    assert r.status_code == 404
    seen.append(r["X-Status-Reason"])
    assert client.get("/p/zzz!/extracted/").status_code == 404
    for text in seen:   # every reason these routes give is in the trial's own wording too
        assert text and not BANNED.search(text), (BANNED.search(text).group(0) if text else "", text)
        assert lapse_wording.check_banned(text) == [], text


def test_403_without_a_key_as_every_data_path(client, env):
    assert client.post(BASE + PIDS[0] + "/").status_code == 403
    assert client.get(BASE + PIDS[0] + "/").status_code == 403
    assert client.get(BASE + "jobs/j" + "0" * 20 + "/").status_code == 403


def test_the_wording_of_what_the_trial_writes(client, mint, env, prov):
    con, cfg = switch_on(env, prov)
    key = mint()
    assert post(client, key, "10812441").status_code == 202
    run_worker(env, con)
    page = client.get("/p/10812441/extracted/").content.decode("utf-8")
    ours = re.sub(r'<(span|pre) [^>]*data-user-content="true"[^>]*>.*?</\1>', "", page, flags=re.S)
    reasons = list(config.REASONS.values()) + [
        "The free daily limit of 3 new extractions for this key is used; it starts again at 00:00 UTC. Results already made do not count toward it.",
        "The extraction trial has as many jobs waiting as it takes at once; try again in a minute.",
        "The extraction trial reads utility patents and reissues only", lapse_extract.__doc__]
    for text in [ours] + reasons:
        assert lapse_wording.check_banned(text) == [], text[:200]
        assert not BANNED.search(text), (BANNED.search(text).group(0), text[:200])


# ------------------------------------------------------------------ the operator's command and the weekly report

def test_the_command_status_and_selfcheck(env, prov):
    out = StringIO()
    call_command("extract", "status", stdout=out)
    s = json.loads(out.getvalue())
    assert s["state"] == "off" and s["key_file"] is False and s["database"] is False and s["cap_usd"] == 100.0
    switch_on(env, prov)
    out = StringIO()
    call_command("extract", "status", stdout=out)
    s = json.loads(out.getvalue())
    assert s["state"] == "open" and "test-stand-in-value" not in out.getvalue()
    out = StringIO()
    call_command("extract", "kill", stdout=out)
    assert out.getvalue().startswith("KILL: DONE") and env.kill.exists()
    call_command("extract", "resume", stdout=StringIO())
    assert not env.kill.exists()
    out = StringIO()
    with pytest.raises(SystemExit):
        call_command("extract", "enable", stdout=out)   # no quality pass recorded: refused
    assert out.getvalue().startswith("ENABLE: REFUSED")
    out = StringIO()
    call_command("extract", "selfcheck", stdout=out)   # on the box the job runs in a forked child under its limits
    assert out.getvalue().strip().splitlines()[-1] == "EXTRACT SELFCHECK: PASS (11 checks)"


def test_the_weekly_report(tmp_path, client, mint, env, prov, monkeypatch):
    from lapse_accounts import requestlog, status

    con, cfg = switch_on(env, prov)
    assert post(client, mint(), "4151221").status_code == 202
    assert run_worker(env, con) == "ran done"
    now = time.time()
    rows = [(now, 202, 2.0, "userkey1", BASE + "4151221/", "POST"), (now, 200, 2.0, "userkey2", BASE + "4151221/", "POST"),
            (now, 429, 2.0, "userkey1", BASE + "7779668/", "POST"), (now, 501, 2.0, "userkey3", BASE + "7779668/", "POST"),
            (now, 200, 2.0, "userkey1", BASE + "jobs/j" + "0" * 20 + "/", "GET"), (now, 200, 2.0, "userkey2", BASE + "4151221/", "GET"),
            (now, 200, 2.0, "", "/p/4151221/extracted/", "GET"), (now, 202, 2.0, "ownkey01", BASE + "6079975/", "POST")]
    path = str(tmp_path / "usage.sqlite3")
    c = requestlog.connect(path)
    c.executemany("INSERT INTO request_log VALUES (?,?,?,?,?,?)", rows)
    c.commit()
    c.close()
    monkeypatch.setattr(settings, "USAGE_LOG_SQLITE_PATH", path)
    monkeypatch.setattr(status, "_own_cache", {"at": time.time(), "prefixes": frozenset({"ownkey01"})})
    monday = usage_counts.week_start(usage_counts.parse_day(store.utc_day(now)))
    w = usage_counts.week_row(monday)
    req, trial = w["requests"], w["trial"]
    assert req["asked"] == 2 and req["cache_hits"] == 1 and req["queued"] == 1 and req["refused_daily_limit"] == 1
    assert req["refused_not_open"] == 1 and req["job_polls"] == 1 and req["result_reads"] == 1 and req["page_views"] == 1
    assert req["extract_keys"] == 2 and req["extract_requests"] == 6 and req["extract_calls"] == 4
    assert trial["extractions"] == 1 and trial["requesters"] == 1 and trial["spend_usd"] > 0 and trial["left_usd"] < 100.0
    out = tmp_path / "usagecounts"
    s = StringIO()
    call_command("structure_usage", "week", "--date", monday.isoformat(), "--out", str(out), stdout=s)
    first = json.loads(s.getvalue())
    assert first["written"] is True and first["extractions"] == 1 and first["cache_hits"] == 1
    saved = (out / f"extract_week_{monday.isoformat()}.json").read_text(encoding="utf-8")
    for p in ("userkey", "ownkey", "4151221", "test-stand-in"):
        assert p not in saved
    s = StringIO()
    call_command("structure_usage", "week", "--date", monday.isoformat(), "--out", str(out), stdout=s)
    assert json.loads(s.getvalue())["written"] is False
