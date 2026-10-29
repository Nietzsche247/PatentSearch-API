"""PatentRef's structure layer, tier S1 (gate S1.1): the on-demand extraction trial, one patent at a time. Built dark.

  POST /api/v1/structure/extract/<patent_id>/     ask for the patent's machine-extracted units: 200 with the result when
                                                  it is made already (the cache), else 202 with a job id and the address to
                                                  poll; a request never waits on a model
  GET  /api/v1/structure/extract/<patent_id>/     the result when one is made, else 404; never starts an extraction
  GET  /api/v1/structure/extract/jobs/<job_id>/   a job's state, and its result once done
  GET  /p/<patent_id>/extracted/                  the result as a page, no key: the label, every quote and its place

The engine is lapse.extract in the patentref clone (LAPSE_REPO_DIR, as lapse.rules and lapse.expiry are): the trial's
settings and files in settings.LAPSE_EXTRACT_DIR, the gate and both kill switches, the store and its cache, the spend cap.
The model is called only by the worker, a process of its own (manage.py extract worker; on the box the unit
patentref-extract.service, which the switch-on step installs); this module only reads the store and queues jobs.

Dark until switched on: while config.json does not say enabled with a model, or the key file
(settings.LAPSE_EXTRACT_KEY_FILE) is not there, or the worker has not run, POST answers 501 ERR_NOT_IMPLEMENTED "The
on-demand extraction trial is not enabled on this server." and nothing is written anywhere; GET and the page answer 404
(no result is made). Either kill switch (config.json "kill": true, or the KILL file) or the spend cap gives the same 501
with its own reason, and results already made are still served.

Per key: daily_free_per_key new extractions a UTC day (3; counted per account, the monthly counter's subject, so a new key
of the same account does not start again); a result from the cache is not counted. Over it: 429 with Retry-After to 00:00
UTC. Keyed, throttled and metered like every /api/v1/ data path (a 501 and a 429 are not counted toward the month).
Every unit is a word-for-word quote from the patent with its place, checked against the text before it was stored; the
result carries the machine-extracted label.
"""
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from rest_framework import status as http
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from API.lapse_group import repo_dir
from API.lapse_views import _error, _record_page_error, normalize_pid
from API.search import get_searcher
from API.search_sqlite import plain, read_data_version, text_real

logger = logging.getLogger(__name__)
_local = threading.local()
MAX_CLAIMS = 1000
BASE = "/api/v1/structure/extract/"
JOB_RE = re.compile(r"^j[0-9a-f]{20}$")
SUMMARY_SQL = "SELECT summary_text FROM {s}g_brf_sum_texts WHERE patent_id = ? ORDER BY rowid LIMIT 8"
CLAIMS_SQL = ("SELECT claim_sequence, claim_number, claim_dependent, claim_text, exemplary FROM {s}g_claims WHERE patent_id = ? "
              "ORDER BY claim_sequence LIMIT ?")
KNOWN_SQL = "SELECT patent_title FROM {m}patents WHERE patent_id = ?"


def engine_module():
    """lapse.extract from the patentref clone, or None (the reason kept) when it is not importable: the trial is then off."""
    mod = getattr(_local, "engine", None)
    if mod is not None:
        return mod
    try:
        from lapse import extract  # noqa: F401
    except ImportError:
        d = repo_dir()
        if d and d not in sys.path and os.path.isdir(d):
            sys.path.insert(0, d)
        try:
            from lapse import extract  # noqa: F401
        except ImportError as exc:
            _local.engine_error = f"lapse.extract is not importable: {exc.__class__.__name__}"
            return None
    import lapse.extract.cli
    import lapse.extract.config
    import lapse.extract.prompt
    import lapse.extract.store
    import lapse.extract.units  # noqa: F401

    _local.engine = sys.modules["lapse.extract"]
    return _local.engine


def trial_paths(eng):
    return eng.config.Paths(getattr(settings, "LAPSE_EXTRACT_DIR", "/data/api/extract"),
                            getattr(settings, "LAPSE_EXTRACT_KEY_FILE", "/data/api/extract.env"))


def read_patent(con, pid, text_version, eng, schema="txt.", main="main."):
    """The patent as the trial reads it, from a connection with the long-text file attached as txt: its brief summary rows
    and claim rows (through their patent_id indexes), its title, and claimgraph's claims. None when it has neither."""
    summaries = [plain(r[0]) for r in con.execute(SUMMARY_SQL.format(s=schema), (pid,))]
    rows = con.execute(CLAIMS_SQL.format(s=schema), (pid, MAX_CLAIMS + 1)).fetchall()
    if len(rows) > MAX_CLAIMS:
        raise ValueError(f"the claims of {pid} are over {MAX_CLAIMS} rows")
    if not rows and not any(summaries):
        return None
    row = con.execute(KNOWN_SQL.format(m=main), (pid,)).fetchone()
    title = plain(row[0]) if row else ""
    claim_rows = [(seq, num, dep, plain(t), ex) for seq, num, dep, t, ex in rows]
    return eng.units.patent_from_rows(pid, title, [s if isinstance(s, str) else "" for s in summaries], claim_rows, text_version)


def read_for_worker(pid):
    """read_patent on connections of its own (the worker's child process opens its own files, read only)."""
    eng = engine_module()
    search = os.path.realpath(settings.LAPSE_SQLITE["path"])
    text = text_real()
    if not text:
        return None
    con = sqlite3.connect(f"file:{search}?mode=ro", uri=True, timeout=10)
    try:
        con.execute("ATTACH DATABASE ? AS txt", ("file:" + text.replace("\\", "/") + "?mode=ro",))
        version = read_data_version(con, text, fallback_env=False, schema="txt")
        return read_patent(con, pid, version, eng)
    finally:
        con.close()


def _json(body, status=200, extra=None):
    resp = HttpResponse(json.dumps(body, ensure_ascii=False, separators=(",", ":")), status=status, content_type="application/json")
    resp["Cache-Control"] = "no-store"
    for k, v in (extra or {}).items():
        resp[k] = v
    return resp


def _not_enabled(eng=None, state="off"):
    reasons = eng.config.REASONS if eng else {"off": "The on-demand extraction trial is not enabled on this server."}
    return _error(reasons.get(state, reasons["off"]), "ERR_NOT_IMPLEMENTED", http.HTTP_501_NOT_IMPLEMENTED)


def _seconds_to_midnight(now):
    d = datetime.fromtimestamp(now, tz=timezone.utc)
    nxt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)
    return max(1, int((nxt - d).total_seconds()))


def _rechecked(eng, body, searcher):
    """A stored result read against the long-text file in service: when its text_data_version differs, every unit is read
    back from the text in service and only those that still hold are served (noted in rechecked_against)."""
    current = searcher.text_data_version()
    if not current or body.get("text_data_version") in (None, current):
        return body
    try:
        patent = read_patent(searcher.connection(), body["patent_id"], current, eng)
    except Exception:  # noqa: BLE001
        patent = None
    if not patent:
        return dict(body, units=[], relations=[], rechecked_against=current)
    kept, rels, dropped = eng.units.check(body["units"], body["relations"], patent)
    return dict(body, units=kept, relations=rels, rechecked_against=current, left_out_on_recheck=dropped)


def _job_body(eng, job, result=None, cfg=None):
    out = {"error": False, "job_id": job["job_id"], "status": job["status"], "patent_id": job["patent_id"],
           "model": job["model"], "prompt_hash": job["prompt_hash"], "created": eng.store.iso(job["created"]),
           "started": eng.store.iso(job["started"]) if job.get("started") else None,
           "finished": eng.store.iso(job["finished"]) if job.get("finished") else None,
           "poll": f"{BASE}jobs/{job['job_id']}/", "result_url": f"{BASE}{job['patent_id']}/", "page": f"/p/{job['patent_id']}/extracted/",
           "label": eng.LABEL}
    if job["status"] in ("failed", "refused"):
        out["reason"] = job.get("reason") or ""
    if job["status"] in ("queued", "running"):
        out["poll_after_seconds"] = int((cfg or {}).get("poll_after_seconds", 10))
    if result is not None:
        out["result"] = result
    return out


def _subject(request):
    from lapse_accounts import metering

    key = request.META.get("HTTP_X_API_KEY") or ""
    prefix = key.split(".", 1)[0][:16]
    return metering.subject_for_prefix(prefix)["subject"] if prefix else "anonymous"


class ExtractView(APIView):
    """POST and GET /api/v1/structure/extract/<patent_id>/ (gate S1.1)."""

    def get(self, request, pk, format=None):
        pid = normalize_pid(pk)
        if not pid:
            return _error(f"Not a patent number: {pk!r}")
        eng = engine_module()
        con = eng.store.connect(trial_paths(eng).db) if eng else None
        if con is None:
            return _error(f"No machine-extracted result for US {pid} yet", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        try:
            cfg = eng.config.load(trial_paths(eng).config)
            body = (eng.store.get_result(con, pid, eng.prompt.prompt_hash(cfg), cfg["model"]) if cfg.get("model") else None) \
                or eng.store.newest_result(con, pid)
        finally:
            con.close()
        if body is None:
            return _error(f"No machine-extracted result for US {pid} yet", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        return _json(_rechecked(eng, body, get_searcher()))

    def post(self, request, pk, format=None):
        pid = normalize_pid(pk)
        if not pid:
            return _error(f"Not a patent number: {pk!r}")
        eng = engine_module()
        if eng is None:
            return _not_enabled()
        paths = trial_paths(eng)
        cfg = eng.config.load(paths.config)
        con = eng.store.connect(paths.db)
        try:
            ph, model = eng.prompt.prompt_hash(cfg), cfg.get("model") or ""
            if con is not None and model:
                body = eng.store.get_result(con, pid, ph, model)
                if body is not None:   # made already: served even while the trial is off or paused, and never counted
                    return _json(_rechecked(eng, body, get_searcher()))
            gate = eng.config.job_gate(cfg, paths)
            if gate:
                return _not_enabled(eng, gate[0])
            now = time.time()
            age = eng.store.heartbeat_age(con, now) if con is not None else None
            if age is None or age > cfg["heartbeat_max_age_seconds"]:
                return _not_enabled(eng)
            searcher = get_searcher()
            text_version = searcher.text_data_version()
            if not text_version:
                return _not_enabled(eng)
            sc = searcher.connection()
            known = sc.execute(KNOWN_SQL.format(m="main."), (pid,)).fetchone()
            if not known:
                return _error(f"Unknown patent_id: {pid!r} is not in the data in service", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
            if pid.upper().startswith(("D", "PP")):
                return _error("The extraction trial reads utility patents and reissues only", "ERR_Q")
            has_text = sc.execute(CLAIMS_SQL.format(s="txt."), (pid, 1)).fetchone() is not None
            if not has_text:
                return _error(f"No claim text for patent_id {pid!r} in the long-text file in service ({text_version})", "NOT_FOUND",
                              http.HTTP_404_NOT_FOUND)
            who = eng.store.who_for(_subject(request))
            outcome, value = eng.store.admit(con, pid, model, ph, who, now, int(cfg["daily_free_per_key"]), int(cfg["queue_max"]),
                                             eng.config.cap_usd(cfg), float(cfg["min_remaining_usd"]))
        finally:
            if con is not None:
                con.close()
        if outcome == "cached":
            return _json(_rechecked(eng, value, get_searcher()))
        if outcome == "cap":
            return _not_enabled(eng, eng.config.CAP)
        if outcome == "limit":
            resp = Response(status=http.HTTP_429_TOO_MANY_REQUESTS, data={"error": True})
            resp["X-Status-Reason"] = (f"The free daily limit of {int(cfg['daily_free_per_key'])} new extractions for this key is "
                                       "used; it starts again at 00:00 UTC. Results already made do not count toward it.")
            resp["Retry-After"] = str(_seconds_to_midnight(now))
            return resp
        if outcome == "full":
            resp = Response(status=http.HTTP_429_TOO_MANY_REQUESTS, data={"error": True})
            resp["X-Status-Reason"] = "The extraction trial has as many jobs waiting as it takes at once; try again in a minute."
            resp["Retry-After"] = "60"
            return resp
        con = eng.store.connect(paths.db)
        try:
            job = eng.store.get_job(con, value)
        finally:
            con.close()
        body = _job_body(eng, job, cfg=cfg)
        return _json(body, http.HTTP_202_ACCEPTED, {"Location": body["poll"], "Retry-After": str(body["poll_after_seconds"])})


class ExtractJobView(APIView):
    """GET /api/v1/structure/extract/jobs/<job_id>/ (gate S1.1)."""

    def get(self, request, job_id, format=None):
        if not JOB_RE.match(job_id or ""):
            return _error(f"Not a job id: {job_id!r}")
        eng = engine_module()
        con = eng.store.connect(trial_paths(eng).db) if eng else None
        if con is None:
            return _error(f"Unknown job_id: {job_id!r}", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        try:
            job = eng.store.get_job(con, job_id)
            result = eng.store.get_result(con, job["patent_id"], job["prompt_hash"], job["model"]) if job and job["status"] == "done" else None
            cfg = eng.config.load(trial_paths(eng).config)
        finally:
            con.close()
        if job is None or job.get("kind") != "api":
            return _error(f"Unknown job_id: {job_id!r}", "NOT_FOUND", http.HTTP_404_NOT_FOUND)
        if result is not None:
            result = _rechecked(eng, result, get_searcher())
        body = _job_body(eng, job, result, cfg)
        extra = {"Retry-After": str(body["poll_after_seconds"])} if "poll_after_seconds" in body else None
        return _json(body, 200, extra)


TYPE_HEADINGS = (("StatedProblem", "Problems the patent states"), ("StatedAdvantage", "Advantages the patent states"),
                 ("StatedPriorArtDrawback", "Drawbacks of earlier work the patent states"), ("Component", "Components the claims recite"))


def _place(unit, numbers):
    loc = unit["locator"]
    if loc["field"] == "claim":
        return f"claim {numbers.get(loc['sequence'], loc['sequence'] + 1)}, characters {loc['start']} to {loc['end']}"
    row = f" (row {loc['sequence'] + 1})" if loc["sequence"] else ""
    return f"the brief summary{row}, characters {loc['start']} to {loc['end']}"


class ExtractPage(APIView):
    """GET /p/<patent_id>/extracted/: the result as a page. No key; the per-IP meta rate, like the record page."""

    permission_classes = [AllowAny]
    throttle_scope = "meta"

    def get_throttles(self):
        from lapse_accounts.throttling import MetaThrottle

        return [MetaThrottle()]

    def get(self, request, pk, format=None):
        from lapse_accounts.public import footer_lines

        pid = normalize_pid(pk)
        none = ("No machine-extracted map yet", f"PatentRef has no machine-extracted map of US {pid or pk} yet.", f"/p/{pid}" if pid else "/docs/",
                f"The record page of US {pid}" if pid else "PatentRef documentation")
        if not pid:
            return _record_page_error(404, *none)
        eng = engine_module()
        con = eng.store.connect(trial_paths(eng).db) if eng else None
        if con is None:
            return _record_page_error(404, *none)
        try:
            cfg = eng.config.load(trial_paths(eng).config)
            body = (eng.store.get_result(con, pid, eng.prompt.prompt_hash(cfg), cfg["model"]) if cfg.get("model") else None) \
                or eng.store.newest_result(con, pid)
        finally:
            con.close()
        if body is None:
            return _record_page_error(404, *none)
        searcher = get_searcher()
        body = _rechecked(eng, body, searcher)
        numbers = {}
        try:
            for seq, num in searcher.connection().execute("SELECT claim_sequence, claim_number FROM txt.g_claims WHERE patent_id = ? "
                                                          "ORDER BY claim_sequence LIMIT ?", (pid, MAX_CLAIMS)):
                numbers[int(seq)] = int(num) if str(num).isdigit() else int(seq) + 1
        except Exception:  # noqa: BLE001  (no long-text file: claims are named by their row)
            numbers = {}
        claim_no = {}
        for r in body["relations"]:
            if r["rel"] == "recites":
                claim_no.setdefault(r["dst"], []).append(numbers.get(r["locator"]["sequence"], r["locator"]["sequence"] + 1))
        groups = []
        for t, heading in TYPE_HEADINGS:
            items = [{"text": u["text"], "place": _place(u, numbers), "term": u.get("term"),
                      "claims": sorted(set(claim_no.get(u["unit_id"], [])))} for u in body["units"] if u["type"] == t]
            groups.append({"type": t, "heading": heading, "items": items})
        data_version = searcher.data_version() or ""
        html = render_to_string("lapse_accounts/extracted.html", {
            "pid": pid, "body": body, "groups": groups, "label": body["label"], "data_version": data_version,
            "as_of": (body.get("extracted_at") or "")[:10], "footer_lines": footer_lines(),
            "json": json.dumps(body, ensure_ascii=False, indent=1)})
        resp = HttpResponse(html, content_type="text/html; charset=utf-8")
        resp["Cache-Control"] = "public, max-age=300"
        resp["X-Robots-Tag"] = "noindex"
        return resp
