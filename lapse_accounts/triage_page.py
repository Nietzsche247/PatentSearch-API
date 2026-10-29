"""The triage table page (gate S1.8L): /triage/ and /triage/example/ on the site's page template (base.html).

  GET /triage/          the page: a pasted list of up to 500 patent numbers, or a search of the claims, sent from the browser
                        with the visitor's own key to POST /api/v1/structure/triage/ (API/lapse_triage.py); the answer is
                        drawn as a table, each independent claim as a list of its parts.
  GET /triage/example/  the same page with an example table already drawn: a fixed list (settings.LAPSE_TRIAGE_EXAMPLE, else
                        EXAMPLE) built by the same code as the API (lapse_triage.build_table) on read, without a key, and
                        kept per worker for the data versions in service. Nothing else can be asked here.

The have and lacks marks are the visitor's own notes on the parts of each independent claim. The page keeps them in the
browser (localStorage) and writes them into its own CSV and JSON export; they never reach the server. The key is held by the
page while it is open and sent only to this site's /api/v1/ (connect-src 'self'); the form's fields have no names, so no
browser ever puts them in an address.

Content-Security-Policy: the inline script and the inline style by their SHA-256, computed from the page as rendered;
requests to this origin only; no frames, no form posts, nothing from elsewhere.
"""
import base64
import hashlib
import json
import re
import threading

from django.conf import settings
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from lapse_accounts import public

SITE = "https://patentref.io"
# The example table: six pool cleaner patents of three statuses (two active, one whose maintenance fee went unpaid, three
# whose term ended) and four components that between them take each of the four positions. Picked by lane ZC on 2026-10-29
# from the claims index of the long-text file in service (claims with "pool cleaner" and brush, or "pool cleaner" and
# "drive motor"); each has claim text in that file, and the statuses and positions were read on patentref-us1 that day.
EXAMPLE = {"patents": ["11773614", "11649651", "8696821", "9051750", "6601255", "6797157"],
           "components": ["drive motor", "housing", "filter", "nozzle"]}
EXAMPLE_CACHE = "public, max-age=300"

_STYLE = re.compile(r"<style>(.*?)</style>", re.S)
_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
_example = {"key": None, "body": None}
_example_lock = threading.Lock()


def _sha(text):
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode("ascii") + "'"


def csp_for(html):
    """The policy of one rendered page: its own inline style and script by hash, nothing else."""
    styles = " ".join(_sha(s) for s in _STYLE.findall(html)) or "'none'"
    scripts = " ".join(_sha(s) for s in _SCRIPT.findall(html)) or "'none'"
    return "; ".join(["default-src 'none'", f"script-src {scripts}", f"style-src {styles}", "img-src 'self' data:",
                      "connect-src 'self'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"])


def _ctx(**extra):
    from API import lapse_triage
    from lapse_accounts.views import _data_version   # here, not at the top: views imports public, which this imports

    version, as_of = _data_version()
    ctx = {"data_version": version, "as_of": as_of, "footer_lines": public.footer_lines(), "site": SITE,
           "max_patents": lapse_triage.MAX_PATENTS, "max_components": lapse_triage.MAX_COMPONENTS,
           "time_limit": int(lapse_triage.TIME_LIMIT_SECONDS), "example": None, "problem": None}
    ctx.update(extra)
    return ctx


def render(request, ctx, status=200, cache="public, max-age=300"):
    html = render_to_string("lapse_accounts/triage.html", ctx, request=request)
    resp = HttpResponse(html, status=status, content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = csp_for(html)
    resp["Cache-Control"] = cache
    return resp


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def page(request):
    return render(request, _ctx())


def example_config():
    conf = getattr(settings, "LAPSE_TRIAGE_EXAMPLE", None) or EXAMPLE
    return {"patents": list(conf["patents"]), "components": list(conf.get("components") or [])}


def example_table(request):
    """The example's answer for the data in service, built once per worker and data version; (body, None) or (None, why)."""
    from API import lapse_triage
    from API.lapse_views import base_url
    from API.search import get_searcher

    conf = example_config()
    searcher = get_searcher()
    base = base_url(request)
    key = (searcher.data_version(), searcher.text_data_version(), base, json.dumps(conf, sort_keys=True))
    with _example_lock:
        if _example["key"] == key:
            return _example["body"], None
    try:
        req = lapse_triage.read_input({"patents": conf["patents"], "components": conf["components"] or None})
        body, _info = lapse_triage.build_table(searcher, req, base)
    except lapse_triage.Refusal as e:
        return None, e.reason
    if lapse_triage.wording_problems(lapse_triage.own_words(body)):
        return None, "the example failed the wording check"
    if body["complete"]:
        with _example_lock:
            _example.update(key=key, body=body)
    return body, None


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def example(request):
    body, why = example_table(request)
    if body is None:
        return render(request, _ctx(problem="The example table cannot be built right now. Try again in a few minutes."), 503,
                      cache="no-store")
    return render(request, _ctx(example=body), cache=EXAMPLE_CACHE if body["complete"] else "no-store")
