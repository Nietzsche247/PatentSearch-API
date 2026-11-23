"""Release AI's filing pages (PatentRef 1.26.0), on the site's page template (base.html):

  GET /filing/              the hub: the filing timeline (every deadline in date order, earliest at the top, and a calendar
                            file) and the cost estimate (required and suggested costs per office, with ranges and sources)
  GET /filing/<code>/       one office's page (ep, cn, jp, kr, ca, au, in, gb, br, mx): deadlines, fees, rules, questions and
                            the estimate, each value with its source and the date it was last checked
  GET /filing/<code>.json   the office's data file as the pages use it (its public view: no held value), CC BY 4.0; also
                            us and pct, the two data files without a page
  GET /filing/rates.json    the exchange rates the estimate uses, with their source and date

The pages come whole from the patentref clone's lapse.filing_pages (LAPSE_REPO_DIR): the words from its strings file, the
facts from its data files; this module serves them. No key: the pages and the files are public, and the page's own script
reads the files and computes in the browser, so the forms answer without a key (the API paths under /api/v1/filing/ take
one). The Content-Security-Policy allows the page's one inline style and one inline script by their SHA-256, as on the
triage page (triage_page.csp_for), and fetches from this site only (connect-src 'self').
"""
from django.http import Http404, HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from API.lapse_filing import filing_module, pages_module, store
from lapse_accounts import previews, public
from lapse_accounts.triage_page import csp_for

SITE = "https://patentref.io"
PAGE_CACHE = "public, max-age=300"
FILE_CACHE = "public, max-age=3600"


def _unavailable():
    resp = HttpResponse("The filing pages are not available on this server.\n", status=503, content_type="text/plain; charset=utf-8")
    resp["Cache-Control"] = "no-store"
    return resp


def _render(request, page, twin=None):
    from lapse_accounts.views import _data_version   # here, not at the top: views imports public, which this imports

    version, as_of = _data_version()
    ctx = {"data_version": version, "as_of": as_of, "footer_lines": public.footer_lines(), "page": page,
           "preview": previews.site_page(page["title"], page["description"], page["path"])}
    html = render_to_string("lapse_accounts/filing.html", ctx, request=request)
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = csp_for(html)
    resp["Cache-Control"] = PAGE_CACHE
    resp["Content-Language"] = page["lang"]
    if twin:
        resp["Link"] = f'<{SITE}{twin}>; rel="alternate"; type="application/json"'
    return resp


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def hub(request):
    pages, st = pages_module(), store()
    if pages is None or st is None:
        return _unavailable()
    return _render(request, pages.render_hub("en", st=st))


@csrf_exempt
@require_safe
def country(request, code):
    f, pages, st = filing_module(), pages_module(), store()
    if f is None or pages is None or st is None:
        return _unavailable()
    if code not in f.PAGE_CODES:
        raise Http404("no filing page for this code")
    return _render(request, pages.render_country(code, "en", st=st), twin=f"/filing/{code}.json")


def _file(body, link=None):
    resp = HttpResponse(body, content_type="application/json; charset=utf-8")
    resp["Cache-Control"] = FILE_CACHE
    resp["Access-Control-Allow-Origin"] = "*"
    if link:
        resp["Link"] = link
    return resp


@csrf_exempt
@require_safe
def twin(request, code):
    f, pages, st = filing_module(), pages_module(), store()
    if f is None or pages is None or st is None:
        return _unavailable()
    if code not in f.DATA_CODES:
        raise Http404("no filing data for this code")
    link = f'<{SITE}/filing/{code}/>; rel="alternate"; type="text/html"' if code in f.PAGE_CODES else None
    return _file(pages.twin_bytes(code, st), link)


@csrf_exempt
@require_safe
def rates(request):
    pages, st = pages_module(), store()
    if pages is None or st is None:
        return _unavailable()
    return _file(pages.rates_bytes(st))
