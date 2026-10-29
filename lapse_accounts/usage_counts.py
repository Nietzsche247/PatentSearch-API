"""The structure layer's usage counter (release X, for the S1 tripwire of the structure layer spec).

Read from the usage-log file (settings.USAGE_LOG_SQLITE_PATH, /data/api/usage_log.sqlite3 on the box), the same rows the
status page's request figures come from (lapse_accounts.requestlog, lapse_accounts.status.request_figures):

  request_log      one row per response: time, status, latency, key prefix, path, method
  structure_log    one row per response under /api/v1/structure/: time, status, key prefix, path, and client ("mcp"
                   when the request came from the PatentRef MCP package, its User-Agent patentref-mcp/<version>)

What a call is here: the monthly counter's rule (lapse_accounts.metering): a request to a data path (under /api/v1/, not
/api/v1/meta/) that carried an X-Api-Key and was answered with a status below 500 other than 403 and 429. Outside means a
key prefix that is not one of the service's own: the prefixes of lapse_accounts.status.own_key_prefixes(), the same set the
status page leaves out of its figures (the operator key that LAPSE_API_KEY_FILE names, plus, from release Y, the GitHub
probe's key and the beta test account's key, settings.LAPSE_OWN_KEY_EXTRA_FILES; each read inside the process, none
printed). The service's own structure requests are counted apart as own_structure_requests.

A row (one UTC day, or any window):
  claim_tree_calls, claim_tree_keys       calls to /api/v1/structure/claims/ and the distinct outside key prefixes behind them
  structure_calls, structure_keys         the same for everything under /api/v1/structure/ (the claim tree, and the
                                          extraction trial's paths, whose own weekly report is week_row below)
  mcp_claim_tree_calls, mcp_claim_tree_keys   the claim tree calls that came from the MCP package (get_claim_tree); None
                                          when the file has no structure_log table yet (a release before X wrote it)
  outside_calls, outside_keys             every outside call to a data path, the claim tree's included
  share_percent                           claim_tree_calls / outside_calls x 100, 3 decimals (None with no outside call)
  structure_requests_all                  every request under /api/v1/structure/ by anyone but the service's own keys,
                                          whatever its status, keyless 403s included
  own_structure_requests                  the service's own requests under /api/v1/structure/ (left out of the above)

Only counts leave this module: never a key prefix, an address or a path with an id. Request rows are kept 35 days
(requestlog.RETENTION_DAYS), so a window must be read within 35 days of its start; covers_window says whether the log
still holds all of it. The S1 tripwire (spec 2026-10-28, market scan 2026-10-28): within 30 days of Stage 1 opening, 10 or
more distinct outside keys call the claim tree, or claim tree calls reach 5 percent of outside calls (the third leg, 2 or
more inbound requests for problem or component extraction, is counted by hand, not here).
"""
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings

DATA = "/api/v1/"
META = "/api/v1/meta/"
STRUCTURE = "/api/v1/structure/"
CLAIMS = "/api/v1/structure/claims/"
DAY = 86400
TRIPWIRE_DAYS = 30
TRIPWIRE_KEYS = 10
TRIPWIRE_SHARE = 5.0
COUNTED = "status < 500 AND status NOT IN (403, 429)"


def log_path():
    return getattr(settings, "USAGE_LOG_SQLITE_PATH", "")


def _own(own):
    if own is None:
        from lapse_accounts import status

        own = status.own_key_prefixes()
    return sorted(own)


def _day_start(d):
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()


def parse_day(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty():
    return {"claim_tree_calls": 0, "claim_tree_keys": 0, "structure_calls": 0, "structure_keys": 0,
            "mcp_claim_tree_calls": None, "mcp_claim_tree_keys": None, "outside_calls": 0, "outside_keys": 0,
            "share_percent": None, "structure_requests_all": 0, "own_structure_requests": 0}


def figures(path, since, until, own=None):
    """The counts over [since, until) (epoch seconds) as a dict (see the module text); `log_from` is the oldest row the
    request log still holds and `covers_window` whether that is at or before `since`."""
    out = empty()
    out.update({"from": iso(since), "until": iso(until), "log_from": None, "covers_window": False})
    if not path or not os.path.exists(path):
        out["note"] = "no usage-log file"
        return out
    own = _own(own)
    marks = ",".join("?" * len(own))
    not_own = f" AND COALESCE(prefix, '') NOT IN ({marks})" if own else ""
    is_own = f" AND prefix IN ({marks})" if own else " AND 0"
    win = "ts >= ? AND ts < ?"
    counted = f"{win} AND COALESCE(prefix, '') != '' AND {COUNTED}{not_own}"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        out["note"] = f"usage-log file unreadable ({exc.__class__.__name__})"
        return out
    try:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "request_log" not in names:
            out["note"] = "no request_log table"
            return out
        first = con.execute("SELECT MIN(ts) FROM request_log").fetchone()[0]
        if first is not None:
            out["log_from"] = iso(first)
            out["covers_window"] = first <= since
        args = (since, until, *own)

        def calls_keys(table, prefix):
            row = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT prefix) FROM {table} WHERE {counted} AND path LIKE ?",
                              args + (prefix + "%",)).fetchone()
            return int(row[0]), int(row[1])

        out["claim_tree_calls"], out["claim_tree_keys"] = calls_keys("request_log", CLAIMS)
        out["structure_calls"], out["structure_keys"] = calls_keys("request_log", STRUCTURE)
        row = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT prefix) FROM request_log WHERE {counted} AND path LIKE ? AND path NOT LIKE ?",
                          args + (DATA + "%", META + "%")).fetchone()
        out["outside_calls"], out["outside_keys"] = int(row[0]), int(row[1])
        out["structure_requests_all"] = int(con.execute(f"SELECT COUNT(*) FROM request_log WHERE {win}{not_own} AND path LIKE ?",
                                                        args + (STRUCTURE + "%",)).fetchone()[0])
        out["own_structure_requests"] = int(con.execute(f"SELECT COUNT(*) FROM request_log WHERE {win}{is_own} AND path LIKE ?",
                                                        args + (STRUCTURE + "%",)).fetchone()[0])
        if "structure_log" in names:
            row = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT prefix) FROM structure_log WHERE {counted} AND client = 'mcp' AND path LIKE ?",
                              args + (CLAIMS + "%",)).fetchone()
            out["mcp_claim_tree_calls"], out["mcp_claim_tree_keys"] = int(row[0]), int(row[1])
        else:
            out["note"] = "no structure_log table yet: the MCP package's calls are not told apart"
        if out["outside_calls"]:
            out["share_percent"] = round(100.0 * out["claim_tree_calls"] / out["outside_calls"], 3)
    except sqlite3.Error as exc:
        out["note"] = f"usage-log read failed ({exc.__class__.__name__})"
    finally:
        con.close()
    return out


RULE = ("calls: keyed requests to data paths answered below 500 other than 403 and 429; outside: not the service's own keys; "
        "counts only")


def day_row(day, path=None, own=None, trailing=True):
    """One UTC day's row; with `trailing`, also the 30 days ending that day (the tripwire's window when the day is Stage 1
    plus 29), whose distinct keys cannot be added up from daily rows."""
    path = path if path is not None else log_path()
    own = _own(own)
    t0 = _day_start(day)
    row = {"day": day.isoformat(), "rule": RULE}
    row.update(figures(path, t0, t0 + DAY, own))
    if trailing:
        trail = figures(path, t0 - (TRIPWIRE_DAYS - 1) * DAY, t0 + DAY, own)
        row["trailing_30d"] = {k: trail[k] for k in ("claim_tree_calls", "claim_tree_keys", "mcp_claim_tree_calls", "mcp_claim_tree_keys",
                                                      "outside_calls", "outside_keys", "share_percent", "from", "covers_window")}
    return row


def tripwire(since, days=TRIPWIRE_DAYS, path=None, own=None, now=None):
    """The S1 tripwire's usage legs over the `days` UTC days from `since` (a date): the counts, then whether either leg holds."""
    path = path if path is not None else log_path()
    now = now if now is not None else time.time()
    t0 = _day_start(since)
    t1 = t0 + days * DAY
    f = figures(path, t0, min(t1, now), own)
    keys_leg = f["claim_tree_keys"] >= TRIPWIRE_KEYS
    share_leg = f["share_percent"] is not None and f["share_percent"] >= TRIPWIRE_SHARE
    return {"since": since.isoformat(), "days": days, "window_open": now < t1, **f,
            "tripwire": {"keys_10_or_more": keys_leg, "share_5_percent_or_more": share_leg, "usage_leg_holds": keys_leg or share_leg,
                         "rule": f"within {days} days of Stage 1 opening: {TRIPWIRE_KEYS} or more distinct outside keys call the claim "
                                 f"tree, or claim tree calls reach {TRIPWIRE_SHARE:g} percent of outside calls; the third leg (2 or more "
                                 "inbound requests for problem or component extraction) is counted by hand"}}


def yesterday(now=None):
    now = now if now is not None else time.time()
    return datetime.fromtimestamp(now, tz=timezone.utc).date() - timedelta(days=1)


# ---------------------------------------------------------------- the extraction trial's weekly report (gate S1.1)

EXTRACT = "/api/v1/structure/extract/"
EXTRACT_JOBS = "/api/v1/structure/extract/jobs/"
EXTRACT_RULE = ("requests: from request_log (the extraction trial's paths, outside keys only; page views keyless); trial: "
                "from the trial's own database (extractions are jobs that ended with a result; requesters are distinct "
                "accounts, counted by a stored hash); spend in USD; counts only")


def extract_figures(path, since, until, own=None):
    """The extraction trial's requests over [since, until) (epoch seconds), from request_log, which has the method:
      extract_requests     every outside request under /api/v1/structure/extract/, whatever its answer
      extract_calls, extract_keys   the counted calls there (the monthly counter's rule) and the distinct outside keys
      asked                POSTs for a patent answered 200 (from the cache) or 202 (a job)
      cache_hits           POSTs answered 200: the result was made already, nothing was spent
      queued               POSTs answered 202: a job was queued, or one already waiting was named
      refused_daily_limit  POSTs answered 429
      refused_not_open     POSTs answered 501 (the trial off, paused, or at its cap)
      result_reads         GETs of a patent's result answered 200
      job_polls            GETs of a job
      page_views           keyless views of /p/<patent_id>/extracted/ answered 200"""
    out = {"extract_requests": 0, "extract_calls": 0, "extract_keys": 0, "asked": 0, "cache_hits": 0, "queued": 0,
           "refused_daily_limit": 0, "refused_not_open": 0, "result_reads": 0, "job_polls": 0, "page_views": 0}
    if not path or not os.path.exists(path):
        out["note"] = "no usage-log file"
        return out
    own = _own(own)
    marks = ",".join("?" * len(own))
    not_own = f" AND COALESCE(prefix, '') NOT IN ({marks})" if own else ""
    win = "ts >= ? AND ts < ?"
    args = (since, until, *own)
    patent = f"path LIKE '{EXTRACT}%' AND path NOT LIKE '{EXTRACT_JOBS}%'"
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        out["note"] = f"usage-log file unreadable ({exc.__class__.__name__})"
        return out
    try:
        if "request_log" not in {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}:
            out["note"] = "no request_log table"
            return out

        def n(cond, a=args):
            return int(con.execute(f"SELECT COUNT(*) FROM request_log WHERE {win}{not_own} AND {cond}", a).fetchone()[0])

        out["extract_requests"] = n(f"path LIKE '{EXTRACT}%'")
        row = con.execute(f"SELECT COUNT(*), COUNT(DISTINCT prefix) FROM request_log WHERE {win} AND COALESCE(prefix, '') != '' "
                          f"AND {COUNTED}{not_own} AND path LIKE '{EXTRACT}%'", args).fetchone()
        out["extract_calls"], out["extract_keys"] = int(row[0]), int(row[1])
        out["asked"] = n(f"{patent} AND method = 'POST' AND status IN (200, 202)")
        out["cache_hits"] = n(f"{patent} AND method = 'POST' AND status = 200")
        out["queued"] = n(f"{patent} AND method = 'POST' AND status = 202")
        out["refused_daily_limit"] = n(f"{patent} AND method = 'POST' AND status = 429")
        out["refused_not_open"] = n(f"{patent} AND method = 'POST' AND status = 501")
        out["result_reads"] = n(f"{patent} AND method = 'GET' AND status = 200")
        out["job_polls"] = n(f"path LIKE '{EXTRACT_JOBS}%' AND method = 'GET'")
        out["page_views"] = int(con.execute(f"SELECT COUNT(*) FROM request_log WHERE {win} AND path LIKE '/p/%/extracted%' "
                                            "AND status = 200", (since, until)).fetchone()[0])
    except sqlite3.Error as exc:
        out["note"] = f"usage-log read failed ({exc.__class__.__name__})"
    finally:
        con.close()
    return out


def trial_figures(since, until, own=None):
    """The trial's own figures over [since, until) (lapse.extract.report): extractions, jobs by how they ended, distinct
    requesting accounts (the service's own left out), spend in the window and in all, the cap and what is left."""
    from API import lapse_extract
    from lapse_accounts import metering

    eng = lapse_extract.engine_module()
    if eng is None:
        return {"note": "the extraction engine is not importable here"}
    paths = lapse_extract.trial_paths(eng)
    who = [eng.store.who_for(metering.subject_for_prefix(p)["subject"]) for p in _own(own)]
    con = sqlite3.connect(f"file:{paths.db}?mode=ro", uri=True, timeout=5) if os.path.exists(paths.db) else None
    try:
        return eng.report.figures(con, since, until, eng.config.load(paths.config), who)
    finally:
        if con is not None:
            con.close()


def week_start(d):
    """The Monday of the ISO week that holds d."""
    return d - timedelta(days=d.weekday())


def last_week(now=None):
    """The Monday of the last complete ISO week (Monday to Sunday, UTC)."""
    today = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc).date()
    return week_start(today) - timedelta(days=7)


def week_row(monday, path=None, own=None):
    """The extraction trial's weekly usage report for the ISO week from `monday`: its requests and its own figures."""
    path = path if path is not None else log_path()
    own = _own(own)
    t0 = _day_start(monday)
    t1 = t0 + 7 * DAY
    return {"week": monday.isoformat(), "from": iso(t0), "until": iso(t1), "rule": EXTRACT_RULE,
            "requests": extract_figures(path, t0, t1, own), "trial": trial_figures(t0, t1, own)}


def last_days(n, now=None, path=None, own=None):
    """Rows for the last n complete UTC days and today so far, oldest first (no trailing windows: one read per day)."""
    path = path if path is not None else log_path()
    own = _own(own)
    today = datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc).date()
    return [day_row(today - timedelta(days=i), path, own, trailing=False) for i in range(n, -1, -1)]
