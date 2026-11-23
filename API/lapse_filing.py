"""PatentRef's filing data on REST (release AI, PatentRef 1.26.0): the two GET forms of the filing build note.

  GET /api/v1/filing/timeline/?date=YYYY-MM-DD&countries=ep,cn,jp&route=pct|direct&basis=priority|filing&filed=YYYY-MM-DD
        every deadline the dates can date, for the picked offices, in date order (earliest at the top); as=ics answers
        the same rows as a calendar file (text/calendar)
  GET /api/v1/filing/estimate/?countries=ca,kr,au&claims=20&pages=30&small_entity=true&route=pct&currency=USD&as_of=YYYY-MM-DD
        required and suggested costs per office and in total, official fees exact and service ranges from published
        sources, in the office currency and the display currency; as=csv answers the lines as CSV

The answers come from the patentref clone's filing engine, `lapse.filing` (LAPSE_REPO_DIR, the clone whose rules engine
serves /api/v1/rules/): its data files (their public view: no held value leaves the engine), its strings and its exchange
rates. This module maps the query string in and the errors out. Keyed and throttled like every data path under /api/v1/
(they count toward the monthly allowance like the other data paths). A refused parameter answers 400 as an RFC 9457
problem details object (type https://patentref.io/docs/errors/ERR_Q, the parameter named in `param`) with X-Status-Reason
and X-Status-Reason-Code ERR_Q as on the other paths; 503 ERR_ES when the engine or its files cannot be read.
X-Filing-Data-Version carries the newest page.last_checked of the picked offices; X-Data-Version stays the search file's
version, as on every path (API/lapse_errors.py DataVersionMiddleware).
Every answer carries the notice that it is a planning aid, not legal advice and not a quote.
"""
import json
import os
import re
import sys
import threading
from http import HTTPStatus

from django.http import HttpResponse
from rest_framework.views import APIView

from API.lapse_group import repo_dir

_local = threading.local()
CACHE = "public, max-age=300"
PROBLEM_TYPE_BASE = "https://patentref.io/docs/errors/"
TIMELINE_PARAMS = ("date", "countries", "route", "basis", "filed", "as")
ESTIMATE_PARAMS = ("countries", "claims", "pages", "small_entity", "route", "currency", "as_of", "as")
TRUE, FALSE = ("true", "1", "yes"), ("false", "0", "no", "")


def _load(name):
    """`lapse.<name>` from the patentref clone (lapse.filing or lapse.filing_pages); None when it is not importable."""
    mod = getattr(_local, name, None)
    if mod is not None:
        return mod
    try:
        mod = __import__(f"lapse.{name}", fromlist=[name])
    except ImportError:
        d = repo_dir()
        if d and d not in sys.path and os.path.isdir(d):
            sys.path.insert(0, d)
        try:
            mod = __import__(f"lapse.{name}", fromlist=[name])
        except ImportError:
            return None
    setattr(_local, name, mod)
    return mod


def filing_module():
    return _load("filing")


def pages_module():
    return _load("filing_pages")


def store():
    """The filing files of the clone in service (read again when a file changes)."""
    f = filing_module()
    return f.store(repo_dir()) if f else None


def problem(request, detail, param=None, status=400, code="ERR_Q"):
    """A refusal as RFC 9457 problem details, with the upstream headers beside it."""
    detail = re.sub(r"[\x00-\x1f\x7f]", " ", str(detail))[:400]
    body = {"type": PROBLEM_TYPE_BASE + code, "title": HTTPStatus(status).phrase, "status": status, "detail": detail,
            "instance": request.path, "code": code}
    if param:
        body["param"] = param
    resp = HttpResponse(json.dumps(body, ensure_ascii=False), status=status, content_type="application/problem+json")
    resp["X-Status-Reason"] = detail
    resp["X-Status-Reason-Code"] = code
    resp["Cache-Control"] = "no-store"
    return resp


def _unavailable(request):
    return problem(request, "The filing data is not available on this server.", status=503, code="ERR_ES")


def _query(request, allowed):
    """The query string as one value per name; a name the path does not take, or a name given twice, is refused."""
    params = {}
    for k in request.GET.keys():
        if k not in allowed:
            return None, (f"unknown parameter {k!r}; this path takes {', '.join(allowed)}", k)
        values = request.GET.getlist(k)
        if len(values) > 1:
            return None, (f"{k} is given more than once", k)
        params[k] = values[0]
    return params, None


def _answer(body, content_type, version):
    resp = HttpResponse(body, content_type=content_type)
    resp["Cache-Control"] = CACHE
    if version:
        resp["X-Filing-Data-Version"] = version
    return resp


class TimelineView(APIView):
    def get(self, request, format=None):
        f, st = filing_module(), store()
        if f is None or st is None:
            return _unavailable(request)
        q, bad = _query(request, TIMELINE_PARAMS)
        if bad:
            return problem(request, *bad)
        if not q.get("date"):
            return problem(request, "date is required, written YYYY-MM-DD", "date")
        form = q.get("as") or "json"
        if form not in ("json", "ics"):
            return problem(request, f"as must be json or ics, got {form!r}", "as")
        try:
            ans = f.timeline(q.get("countries", ""), q["date"], route=q.get("route") or "pct", basis=q.get("basis") or "priority",
                             filed=q.get("filed") or None, st=st)
        except f.FilingInputError as e:
            return problem(request, str(e), e.param)
        except (OSError, ValueError, KeyError):
            return _unavailable(request)
        if form == "ics":
            resp = _answer(f.ics(ans, st.strings("en")), "text/calendar; charset=utf-8", f.data_version(ans))
            resp["Content-Disposition"] = 'attachment; filename="patentref-filing-deadlines.ics"'
            return resp
        return _answer(json.dumps(ans, ensure_ascii=False), "application/json", f.data_version(ans))


class EstimateView(APIView):
    def get(self, request, format=None):
        f, st = filing_module(), store()
        if f is None or st is None:
            return _unavailable(request)
        q, bad = _query(request, ESTIMATE_PARAMS)
        if bad:
            return problem(request, *bad)
        form = q.get("as") or "json"
        if form not in ("json", "csv"):
            return problem(request, f"as must be json or csv, got {form!r}", "as")
        se = (q.get("small_entity") or "").lower()
        if se not in TRUE + FALSE:
            return problem(request, f"small_entity must be true or false, got {q.get('small_entity')!r}", "small_entity")
        try:
            ans = f.estimate(q.get("countries", ""), claims=q.get("claims") or 20, pages=q.get("pages") or 30, small_entity=se in TRUE,
                             route=q.get("route") or "pct", currency=q.get("currency") or "USD", as_of=q.get("as_of") or None, st=st)
        except f.FilingInputError as e:
            return problem(request, str(e), e.param)
        except (OSError, ValueError, KeyError):
            return _unavailable(request)
        if form == "csv":
            resp = _answer(f.estimate_csv(ans), "text/csv; charset=utf-8", f.data_version(ans))
            resp["Content-Disposition"] = 'attachment; filename="patentref-filing-estimate.csv"'
            return resp
        return _answer(json.dumps(ans, ensure_ascii=False), "application/json", f.data_version(ans))
