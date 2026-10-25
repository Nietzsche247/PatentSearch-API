"""/swagger-ui/: upstream's interactive page, kept and pinned (PatentRef lane Q2, the finding of the gate 7.6 run).

Upstream served it: PatentSearch-API routes swagger-ui/ in pvapi/urls.py (templates/swagger-ui.html), and its own docs link
it ("Try out the API with our Swagger Page", https://search.patentsview.org/swagger-ui/, docs/docs/Search API/
SearchAPIReference.md). So PatentRef keeps the path and answers it from this view instead of upstream's template, which
loaded unpinned unpkg scripts (swagger-ui-dist@3 and the hierarchical-tags plugin with no version), sent no
Content-Security-Policy and pointed at /static/openapi.json, which this server does not serve (the page showed an error).

What this page is instead:
  - Swagger UI 5.32.15, the release pinned below (the 3.x line upstream used cannot read OpenAPI 3.1, which /openapi.json is).
    Each file carries Subresource Integrity: the SHA-384 of that file in the npm tarball swagger-ui-dist-5.32.15.tgz, whose
    own sha512 matches the npm registry's dist.integrity, and unpkg serves those exact bytes (checked 2026-10-25).
  - The spec is /openapi.json, PatentRef's own description of what this server answers.
  - A Content-Security-Policy that lets in only the two pinned files, the one inline script (by its SHA-256), requests to
    this origin (the spec and "Try it out") and data: images (the icons in Swagger UI's stylesheet); nothing inline but
    that script. Checked in headless Chromium on 2026-10-25: the page renders all 109 operations, an operation opens, "Try
    it out" sends its request and shows the answer, and the Authorize dialog opens, with no CSP violation reported.
  - No CSRF token and no cookie: the API takes X-Api-Key and nothing on this page uses a session (upstream's template put a
    token into every request it sent). The hierarchical-tags plugin is left out: /openapi.json has flat tags.
  - lang, a title and a main landmark (the gate 7.6 accessibility run found none).
"""
import base64
import hashlib

from django.http import HttpResponse
from django.views.decorators.http import require_safe

SWAGGER_UI_VERSION = "5.32.15"
CDN = f"https://unpkg.com/swagger-ui-dist@{SWAGGER_UI_VERSION}/"
CSS_URL = CDN + "swagger-ui.css"
JS_URL = CDN + "swagger-ui-bundle.js"
CSS_SRI = "sha384-fgyWYkUAamzuI8mJFu/xpRP0JWCJRwkwUwsYDoOYVHUJ8NQE5cENn8ib3ppwFFSX"
JS_SRI = "sha384-m7zaGj7MPzU+G4lz2eyy73GxK9bbRDr9bB2CSdj8wodg2wu/Wnt6wsoLP3JD+RS9"
SPEC_URL = "/openapi.json"

INIT_SCRIPT = (
    'window.ui = SwaggerUIBundle({url: "' + SPEC_URL + '", dom_id: "#swagger-ui", docExpansion: "list", deepLinking: true, '
    'validatorUrl: null, presets: [SwaggerUIBundle.presets.apis], layout: "BaseLayout"});'
)
INIT_SHA256 = "sha256-" + base64.b64encode(hashlib.sha256(INIT_SCRIPT.encode("utf-8")).digest()).decode("ascii")

CSP = "; ".join([
    "default-src 'none'",
    f"script-src {JS_URL} '{INIT_SHA256}'",
    f"style-src {CSS_URL}",
    "img-src 'self' data:",
    "connect-src 'self'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
])

PAGE = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Swagger UI | PatentRef API</title>
  <meta name="description" content="The PatentRef API's OpenAPI description as an interactive page: every route with its parameters and answers. Requests need a PatentRef key in X-Api-Key.">
  <link rel="stylesheet" href="{CSS_URL}" integrity="{CSS_SRI}" crossorigin="anonymous" referrerpolicy="no-referrer">
</head>
<body>
  <main id="swagger-ui"></main>
  <script src="{JS_URL}" integrity="{JS_SRI}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
  <script>{INIT_SCRIPT}</script>
</body>
</html>
"""


@require_safe
def page(request):  # GET and HEAD, as upstream's TemplateView
    resp = HttpResponse(PAGE, content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = CSP
    resp["Cache-Control"] = "public, max-age=300"
    return resp
