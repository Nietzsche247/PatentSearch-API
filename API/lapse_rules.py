"""PatentRef's rules layer on REST (PatentRef checklist 11.3; docs/SCAFFOLD.md 5.5 of the patentref repo).

  GET /api/v1/rules/                                          rules_index: version, as_of, offices, tools, vocabulary
  GET /api/v1/rules/rep_required/?office=&act=                representation rows
  GET /api/v1/rules/fee_schedule/?office=&as_of=YYYY-MM-DD    fee rows in force on as_of (default today, UTC)
  GET /api/v1/rules/route_windows/?office=                    generic windows, never a date for an application
  GET /api/v1/rules/pph_routes/?from_office=&to_office=       PPH rows of to_office whose source names from_office
  GET /api/v1/rules/ai_use_statement_required/?office=        AI-use statement rows (none means no cited rule)
  GET /api/v1/rules/grace_period/?office=                     grace-period rows, citations only
  GET /api/v1/rules/changelog/?since=x.y.z|YYYY-MM-DD         rules_changelog

The rows and every answer come from the patentref clone's one rules engine, `lapse.rules` (LAPSE_REPO_DIR, the same
clone whose `lapse.expiry` serves the status record); that engine also checks the parameters, refuses an
application number, a patent number or a priority date, and runs the wording check. This module maps the query
string in and the errors out. Keyed and throttled like every data path under /api/v1/ (they count toward the
monthly allowance like the other data paths). Errors keep the upstream shape: 400 {"error": true} with
X-Status-Reason and X-Status-Reason-Code ERR_Q; 503 ERR_ES when the engine or the dataset cannot be read.
"""
import os
import sys
import threading

from rest_framework import status as http
from rest_framework.response import Response
from rest_framework.views import APIView

from API.lapse_group import repo_dir

_local = threading.local()
CACHE = "public, max-age=300"


def rules_module():
    """`lapse.rules` from the patentref clone; None (the reason kept) when it is not importable."""
    mod = getattr(_local, "rules", None)
    if mod is not None:
        return mod
    try:
        from lapse import rules  # noqa: F401
    except ImportError:
        d = repo_dir()
        if d and d not in sys.path and os.path.isdir(d):
            sys.path.insert(0, d)
        try:
            from lapse import rules
        except ImportError as e:
            _local.rules_error = f"the rules engine is not importable: {e.__class__.__name__}"
            return None
    _local.rules = rules
    return rules


def _error(reason, code="ERR_Q", status=http.HTTP_400_BAD_REQUEST):
    resp = Response(status=status, data={"error": True})
    resp["X-Status-Reason"] = reason
    resp["X-Status-Reason-Code"] = code
    return resp


class RulesView(APIView):
    """One rules tool; `tool` is set per route in lapse_accounts/root_urls.py."""

    tool = None

    def get(self, request, format=None):
        rules = rules_module()
        if rules is None:
            return _error("The rules dataset is not available on this server.", "ERR_ES", http.HTTP_503_SERVICE_UNAVAILABLE)
        params = {}
        for k in request.GET.keys():
            values = request.GET.getlist(k)
            params[k] = values if len(values) > 1 else values[0]
        try:
            ds = rules.load(os.path.join(repo_dir(), "rules"))
        except (OSError, ValueError):
            ds = None
        if ds is None or not ds.rows:
            return _error("The rules dataset could not be read.", "ERR_ES", http.HTTP_503_SERVICE_UNAVAILABLE)
        try:
            body = rules.call(self.tool, params, ds)
        except rules.RulesError as e:
            return _error(str(e))
        except RuntimeError:  # the wording check stopped a row (never-regress 8): nothing leaves the process
            return _error("The rules answer failed its wording check; retry later.", "ERR_ES", http.HTTP_500_INTERNAL_SERVER_ERROR)
        resp = Response(body)
        resp["Cache-Control"] = CACHE
        resp["X-Rules-Version"] = body.get("rules_version", "")
        return resp


ROUTES = (  # (path under api/v1/rules/, tool)
    ("", "rules_index"),
    ("rep_required", "rep_required"),
    ("fee_schedule", "fee_schedule"),
    ("route_windows", "route_windows"),
    ("pph_routes", "pph_routes"),
    ("ai_use_statement_required", "ai_use_statement_required"),
    ("grace_period", "grace_period"),
    ("changelog", "rules_changelog"),
)


def rules_version():
    """The dataset version in service, for GET /api/v1/meta/ (None when the dataset cannot be read)."""
    rules = rules_module()
    if rules is None:
        return None
    try:
        return rules.load(os.path.join(repo_dir(), "rules")).version
    except Exception:
        return None
