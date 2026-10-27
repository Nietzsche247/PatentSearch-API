"""The home page and the keyless demo (PatentRef release S, API 1.17.0; specs 3 and 10 of the marketing plan).

  GET /               the home page: what PatentRef is in two sentences, the base URL in R and in Python, the free key and the
                      links (docs, demo, status, snapshot, waitlist). No script. Before 1.17.0, / redirected to /docs/.
  GET /try/           the demo: the fixed requests of EXAMPLES, each shown as the curl command a client sends with its own
                      key (YOUR_KEY is a placeholder) and a link that runs it. Nothing else can be asked: no query box.
  GET /try/<name>/    runs one example and shows the answer: its status, its X-Data-Version and its JSON body, the body the
                      API gives a keyed client for the same request.

The demo allowance. A run is answered on one allowance shared by every visitor, never on a key: per client address
LAPSE_DEMO_PER_HOUR runs a clock hour (default 20) and for everyone together LAPSE_DEMO_PER_DAY runs a UTC day (default
2,000), counted on the throttles' atomic window counters (lapse_accounts.ratelimit) and keyed on the address every
per-address limit uses (lapse_accounts.abuse.client_ip). Above either, 429 with Retry-After and a page that points to the free
key. Every run deletes the demo's counters of windows that are over, so an address is kept at most until the end of its
hour. The Privacy Policy's paragraph on usage data covers this: "For every API request we record the time, the key prefix,
the request path and method, the response status, the time the request took, and the client IP address. Purpose: metering
your monthly allowance, enforcing per-minute and per-address limits, abuse prevention, ..." (docs/legal/privacy.md,
section 1).

No key, and nothing counted against an account. The run calls the API's own view for the example's path in this process,
with a request built here: GET, the example's q, f, s and o, Accept: application/json, the host and client address the
proxy gave, and nothing else (no X-Api-Key, no Authorization, no cookie). The view runs without its key permission and
without its throttles, so no month is reserved and no account or key is metered; upstream's usage log records keyed
requests only, so it keeps no row. A request to /try/<name>/ that carries X-Api-Key or Authorization, or any query string,
answers 400 and runs nothing.
"""
import json
import sys
from dataclasses import dataclass, field
from io import BytesIO
from urllib.parse import urlencode

from django.conf import settings
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.urls import resolve
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_safe

from lapse_accounts import abuse, public, ratelimit

SITE = "https://patentref.io"
SCOPE_IP, SCOPE_ALL = "demo-ip", "demo-all"
SUBJECT_ALL = "demo:all"
PLACEHOLDER = "YOUR_KEY"


@dataclass(frozen=True)
class Example:
    name: str
    title: str
    what: str
    path: str
    params: dict = field(default_factory=dict)   # q, f, s, o in that order, as JSON values

    def query_string(self):
        return urlencode([(k, json.dumps(v, ensure_ascii=False, separators=(",", ":"))) for k, v in self.params.items()])

    def curl(self):
        """The request as a client sends it with its own key: curl -G with each parameter URL-encoded."""
        lines = [f'curl -s -G -H "X-Api-Key: {PLACEHOLDER}" {SITE}{self.path}']
        for k, v in self.params.items():
            lines.append(f"  --data-urlencode '{k}={json.dumps(v, ensure_ascii=False, separators=(',', ':'))}'")
        return " \\\n".join(lines)


PATENT = "10905426"   # the patent of llms.txt's own first query
EXAMPLES = (
    Example("patent", "One patent by its number",
            "The record of US 10905426: its title, grant date and type, its assignee and its inventors.",
            "/api/v1/patent/",
            {"q": {"patent_id": PATENT},
             "f": ["patent_id", "patent_title", "patent_date", "patent_type", "assignees.assignee_organization",
                   "inventors.inventor_name_first", "inventors.inventor_name_last"]}),
    Example("assignee", "Grants to one assignee in one year",
            "Patents granted to Tesla, Inc. in 2024, oldest first, the first ten of them; total_hits counts them all.",
            "/api/v1/patent/",
            {"q": {"_and": [{"assignees.assignee_organization": "Tesla, Inc."}, {"_gte": {"patent_date": "2024-01-01"}},
                            {"_lte": {"patent_date": "2024-12-31"}}]},
             "f": ["patent_id", "patent_title", "patent_date"],
             "s": [{"patent_date": "asc"}, {"patent_id": "asc"}],
             "o": {"size": 10}}),
    Example("claims", "The claims of one patent",
            "The first three claims of US 10905426, word for word, in their order; total_hits counts them all.",
            "/api/v1/g_claim/",
            {"q": {"patent_id": PATENT},
             "f": ["patent_id", "claim_sequence", "claim_number", "claim_dependent", "claim_text"],
             "s": [{"claim_sequence": "asc"}],
             "o": {"size": 3}}),
    Example("status", "Expiry and maintenance-fee status",
            "PatentRef's lapse group for US 10905426: what the USPTO record shows about its term and its maintenance fees, "
            "with the basis and the as-of date.",
            "/api/v1/patent/",
            {"q": {"patent_id": PATENT}, "f": ["patent_id", "patent_title", "lapse"]}),
)
BY_NAME = {e.name: e for e in EXAMPLES}


def per_hour():
    return int(getattr(settings, "LAPSE_DEMO_PER_HOUR", 20))


def per_day():
    return int(getattr(settings, "LAPSE_DEMO_PER_DAY", 2000))


# ---------------------------------------------------------------- the allowance

def purge(now=None):
    """Delete the demo's counters of windows that are over (an address's hour, everyone's day). Returns the rows removed."""
    from django.db import connection

    removed = 0
    with connection.cursor() as cur:
        for scope, seconds in ((SCOPE_IP, 3600), (SCOPE_ALL, 86400)):
            current, _ = ratelimit.minute_window(scope, seconds, now)
            cur.execute(f"DELETE FROM {ratelimit.WINDOW_TABLE} WHERE substr(win, 1, %s) = %s AND win < %s",
                        [len(scope) + 1, scope + ":", current])
            removed += cur.rowcount
    return removed


def take(ip, now=None):
    """One run from the allowance: (None, None) when it may run, else (which allowance is used up, seconds to wait)."""
    purge(now)
    subject = "ip:" + ip
    win_ip, wait_ip = ratelimit.minute_window(SCOPE_IP, 3600, now)
    if ratelimit.bump(ratelimit.WINDOW_TABLE, subject, "win", win_ip, per_hour()) is None:
        return "address", wait_ip
    win_all, wait_all = ratelimit.minute_window(SCOPE_ALL, 86400, now)
    if ratelimit.bump(ratelimit.WINDOW_TABLE, SUBJECT_ALL, "win", win_all, per_day()) is None:
        ratelimit.refund(ratelimit.WINDOW_TABLE, subject, "win", win_ip)   # a run that did not happen does not count
        return "everyone", wait_all
    return None, None


# ---------------------------------------------------------------- the run

_views = {}


def _view_for(path):
    """The API's own view for `path`, without its key permission and its throttles (the demo's request carries no key)."""
    if path not in _views:
        match = resolve(path)
        cls = getattr(match.func, "cls", None)
        if cls is None:
            raise LookupError(f"{path} is not an API view")
        _views[path] = (cls.as_view(permission_classes=[], throttle_classes=[], **getattr(match.func, "initkwargs", {})),
                        match.kwargs)
    return _views[path]


def inner_request(outer, example):
    """The request the demo sends to the view: GET with the example's parameters, Accept: application/json, the host and
    client address as the proxy gave them, and no other header (no key, no Authorization, no cookie)."""
    env = {
        "REQUEST_METHOD": "GET", "PATH_INFO": example.path, "SCRIPT_NAME": "", "QUERY_STRING": example.query_string(),
        "SERVER_NAME": outer.META.get("SERVER_NAME", "localhost"), "SERVER_PORT": str(outer.META.get("SERVER_PORT", "80")),
        "SERVER_PROTOCOL": "HTTP/1.1", "REMOTE_ADDR": outer.META.get("REMOTE_ADDR", ""),
        "HTTP_ACCEPT": "application/json", "HTTP_USER_AGENT": "patentref-demo",
        "wsgi.version": (1, 0), "wsgi.url_scheme": outer.META.get("wsgi.url_scheme", "http"), "wsgi.input": BytesIO(b""),
        "wsgi.errors": sys.stderr, "wsgi.multithread": True, "wsgi.multiprocess": True, "wsgi.run_once": False,
    }
    for name in ("HTTP_HOST", "HTTP_X_FORWARDED_PROTO", "HTTP_X_FORWARDED_HOST", "HTTP_X_FORWARDED_FOR"):
        if outer.META.get(name):
            env[name] = outer.META[name]
    return WSGIRequest(env)


def run(outer, example):
    """Run one example through the API's view: {"status", "data_version", "text_data_version", "body", "json"}."""
    from API import lapse_errors

    view, kwargs = _view_for(example.path)
    resp = view(inner_request(outer, example), **kwargs)
    if hasattr(resp, "render"):
        resp.render()
    body = bytes(resp.content)
    headers = getattr(lapse_errors._pending, "headers", None) or {}
    lapse_errors._pending.headers = None   # the view's error headers belong to this answer, not to the page
    lapse_errors._pending.status = None
    if resp.status_code == 501 and headers:
        body = lapse_errors.BODY_501   # as LapseErrorHeadersMiddleware gives every client
    try:
        from API.search_sqlite import current_data_version, current_text_data_version

        version, text_version = current_data_version(), current_text_data_version()
    except Exception:  # noqa: BLE001 - the version line never breaks the page
        version, text_version = None, None
    try:
        shown = json.dumps(json.loads(body.decode("utf-8")), indent=2, ensure_ascii=False)
    except ValueError:
        shown = body.decode("utf-8", "replace")
    return {"status": resp.status_code, "data_version": version,
            "text_data_version": text_version if lapse_errors.is_text_path(example.path) else None,
            "reason": headers.get("X-Status-Reason") or resp.get("X-Status-Reason"), "body": body, "json": shown}


# ---------------------------------------------------------------- pages

def _ctx(**extra):
    from lapse_accounts import metering
    from lapse_accounts.models import PLAN_FREE
    from lapse_accounts.views import _data_version   # here, not at the top: views imports public, which this imports

    version, as_of = _data_version()
    monthly, per_minute = metering.plan_limits(PLAN_FREE)
    ctx = {"data_version": version, "as_of": as_of, "footer_lines": public.footer_lines(), "examples": EXAMPLES,
           "per_hour": per_hour(), "per_day": f"{per_day():,}", "placeholder": PLACEHOLDER, "site": SITE,
           "monthly": f"{monthly:,}", "per_minute": per_minute}
    ctx.update(extra)
    return ctx


def _page(request, template, ctx, status=200, cache="public, max-age=300", noindex=False):
    resp = HttpResponse(render_to_string(template, ctx, request=request), status=status, content_type="text/html; charset=utf-8")
    resp["Cache-Control"] = cache
    if noindex:
        resp["X-Robots-Tag"] = "noindex"
    return resp


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def home(request):
    return _page(request, "lapse_accounts/home.html", _ctx())


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def try_index(request):
    return _page(request, "lapse_accounts/try.html", _ctx(state="index"))


def _refuse(request, state, status, example=None, wait=None):
    resp = _page(request, "lapse_accounts/try.html", _ctx(state=state, ran=example, wait_minutes=(wait + 59) // 60 if wait else None),
                 status, cache="no-store", noindex=True)
    if wait:
        resp["Retry-After"] = str(int(wait))
    return resp


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_GET
def try_run(request, name):
    example = BY_NAME.get(name)
    if example is None:
        return _refuse(request, "unknown", 404)
    if request.META.get("HTTP_X_API_KEY") or request.META.get("HTTP_AUTHORIZATION"):
        return _refuse(request, "key", 400, example)       # the demo never takes a key
    if request.META.get("QUERY_STRING"):
        return _refuse(request, "params", 400, example)    # nor a parameter: only the fixed request runs
    used_up, wait = take(abuse.client_ip(request))
    if used_up:
        return _refuse(request, "limit-" + used_up, 429, example, wait)
    answer = run(request, example)
    return _page(request, "lapse_accounts/try.html", _ctx(state="ran", ran=example, answer=answer), cache="no-store", noindex=True)
