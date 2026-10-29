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
  - A Content-Security-Policy that lets in only the two pinned files, the one inline script and the one inline style (each
    by its SHA-256), requests to this origin (the spec and "Try it out") and data: images (the icons in Swagger UI's
    stylesheet). Checked in headless Chromium on 2026-10-25: the page renders all 109 operations, an operation opens, "Try
    it out" sends its request and shows the answer, and the Authorize dialog opens, with no CSP violation reported.
  - No CSRF token and no cookie: the API takes X-Api-Key and nothing on this page uses a session (upstream's template put a
    token into every request it sent). The hierarchical-tags plugin is left out: /openapi.json has flat tags.
  - lang and a title (the gate 7.6 accessibility run found none). Swagger UI draws its own main landmark (#operations), so
    the page's container is a div: a second main around it made two main landmarks, one inside the other.

Gate 7.6 (release W, independent audit 11): axe-core 4.10.3 found 1 critical and 3 serious rules on this page and 50 px of
sideways scroll at 320 px, all from Swagger UI's own markup and colors. What fixes each, here and nowhere else:
  - select-name (critical): Swagger UI's servers select sits in an empty label. The inline script gives it an accessible
    name ("Server") each time Swagger UI draws it.
  - nested-interactive (serious): with deep links on, every operation's path is a link inside the operation's button.
    deepLinking is off, so the path is plain text; an operation still opens with its button.
  - color-contrast (serious): the method badges (white on light blue and light green), the version badges, the links of
    the info block, the Authorize button, the schema "Expand all" buttons and the string constraint badge. STYLE sets
    colors with a contrast of at least 4.5 to 1 against their background.
  - target-size (serious): the copy-path buttons (0 px wide until hovered), the info block's links and the schema buttons.
    STYLE gives each at least 24 by 24 px.
  - sideways scroll at 320 px: the tag headers (24 px text, an empty small element with padding, the expand button) and
    the schema names. STYLE lets them wrap and shrink below 480 px.
The page is light in both color schemes: Swagger UI's dark theme needs a class on html that nothing sets, and
color-scheme light keeps the browser's own form controls light too.
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
    'window.ui = SwaggerUIBundle({url: "' + SPEC_URL + '", dom_id: "#swagger-ui", docExpansion: "list", deepLinking: false, '
    'validatorUrl: null, presets: [SwaggerUIBundle.presets.apis], layout: "BaseLayout"});'
    '(function () {'
    'function name(el, text) { if (text && !el.getAttribute("aria-label") && !(el.labels && el.labels.length)) { el.setAttribute("aria-label", text); } }'
    'function names() {'
    'var s = document.getElementById("servers");'
    'if (s && s.getAttribute("aria-label") !== "Server") { s.setAttribute("aria-label", "Server"); }'
    'document.querySelectorAll("tr[data-param-name]").forEach(function (tr) {'
    'tr.querySelectorAll("input, select, textarea").forEach(function (el) { name(el, tr.getAttribute("data-param-name")); });'
    '});'
    'document.querySelectorAll(".body-param textarea, .body-param__text").forEach(function (el) { name(el, "Request body"); });'
    'document.querySelectorAll(".swagger-ui pre.microlight, .swagger-ui pre.example").forEach(function (el) {'
    'if (!el.hasAttribute("tabindex")) { el.setAttribute("tabindex", "0"); }'
    '});'
    'document.querySelectorAll(".close-modal").forEach(function (el) { name(el, "Close"); });'
    '}'
    'new MutationObserver(names).observe(document.body, {childList: true, subtree: true});'
    'names();'
    '})();'
)
INIT_SHA256 = "sha256-" + base64.b64encode(hashlib.sha256(INIT_SCRIPT.encode("utf-8")).digest()).decode("ascii")

# Swagger UI's own rules that fail WCAG 2.2 AA, overridden (each selector as specific as the one it overrides, or more)
STYLE = """
  html { color-scheme: light; }
  body { margin: 0; background: #fff; }
  .swagger-ui .info a, .swagger-ui .info a:visited { color: #1f5fa8; }
  .swagger-ui .info a:hover { color: #174a85; }
  .swagger-ui .info .title small { background: #5b616d; }
  .swagger-ui .info .title small.version-stamp { background-color: #3d6b00; }
  .swagger-ui .info__contact a, .swagger-ui .info__license a, .swagger-ui .info .main a.link {
    display: inline-block; min-height: 24px; line-height: 24px; }
  .swagger-ui .btn.authorize { color: #0b6b3e; border-color: #0b6b3e; }
  .swagger-ui .btn.authorize svg { color: #0b6b3e; fill: #0b6b3e; }
  .swagger-ui .opblock.opblock-get .opblock-summary-method { background: #1f69c0; }
  .swagger-ui .opblock.opblock-post .opblock-summary-method { background: #0e7a4c; }
  .swagger-ui .opblock.opblock-put .opblock-summary-method { background: #a14d00; }
  .swagger-ui .opblock.opblock-delete .opblock-summary-method { background: #c01c28; }
  .swagger-ui .opblock.opblock-patch .opblock-summary-method { background: #0f6f62; }
  .swagger-ui .opblock.opblock-head .opblock-summary-method { background: #7a1ccb; }
  .swagger-ui .opblock.opblock-options .opblock-summary-method { background: #0d5aa7; }
  .swagger-ui .opblock.opblock-query .opblock-summary-method { background: #8a3a7c; }
  .swagger-ui .opblock.opblock-deprecated .opblock-summary-method { background: #6b6b6b; }
  .swagger-ui .opblock .opblock-summary .view-line-link.copy-to-clipboard { width: 24px; min-width: 24px; height: 26px; }
  .swagger-ui .opblock .opblock-summary .view-line-link.copy-to-clipboard button { width: 24px; min-width: 24px; height: 24px; }
  .swagger-ui .json-schema-2020-12-expand-deep-button { color: #505050; min-height: 24px; min-width: 24px; padding: 0 4px; }
  .swagger-ui .json-schema-2020-12__constraint--string { background-color: #8a5a00; }
  .swagger-ui .json-schema-2020-12-accordion { max-width: 100%; min-width: 0; text-align: left; }
  .swagger-ui .json-schema-2020-12__title, .swagger-ui .json-schema-2020-12-accordion__children { overflow-wrap: anywhere; min-width: 0; }
  .swagger-ui .opblock-tag > * { min-width: 0; overflow-wrap: anywhere; }
  .swagger-ui .opblock-tag small:empty { display: none; }
  .swagger-ui .expand-operation { flex: none; min-width: 24px; min-height: 24px; }
  .swagger-ui .parameter__in, .swagger-ui .parameter__deprecated { color: #595959; }
  .swagger-ui .btn.cancel { color: #b3261e; border-color: #b3261e; }
  .swagger-ui .btn.execute { background-color: #1f5fa8; border-color: #1f5fa8; }
  @media (max-width: 480px) {
    .swagger-ui .wrapper { padding: 0 8px; }
    .swagger-ui table.parameters { table-layout: fixed; width: 100%; }
    .swagger-ui table.parameters td, .swagger-ui table.parameters th { overflow-wrap: anywhere; word-break: break-word; }
    .swagger-ui table.responses-table, .swagger-ui table.responses-table thead, .swagger-ui table.responses-table tbody,
    .swagger-ui table.responses-table tr, .swagger-ui table.responses-table td { display: block; width: auto; max-width: 100%; }
    .swagger-ui table.responses-table td { overflow-wrap: anywhere; padding-left: 0; padding-right: 0; }
    .swagger-ui .responses-inner { padding: 8px; }
    .swagger-ui .opblock-body pre.microlight { max-width: 100%; }
    .swagger-ui .opblock-tag { font-size: 18px; padding: 10px 4px; }
    .swagger-ui .info { margin: 20px 0; }
    .swagger-ui .info .title { overflow-wrap: anywhere; }
    .swagger-ui .scheme-container .schemes > .schemes-server-container > label { margin: 0; }
    .swagger-ui .servers > label select { min-width: 0; }
  }
"""
STYLE_SHA256 = "sha256-" + base64.b64encode(hashlib.sha256(STYLE.encode("utf-8")).digest()).decode("ascii")

CSP = "; ".join([
    "default-src 'none'",
    f"script-src {JS_URL} '{INIT_SHA256}'",
    f"style-src {CSS_URL} '{STYLE_SHA256}'",
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
  <meta name="color-scheme" content="light">
  <title>Swagger UI | PatentRef API</title>
  <meta name="description" content="The PatentRef API's OpenAPI description as an interactive page: every route with its parameters and answers. Requests need a PatentRef key in X-Api-Key.">
  <link rel="stylesheet" href="{CSS_URL}" integrity="{CSS_SRI}" crossorigin="anonymous" referrerpolicy="no-referrer">
  <style>{STYLE}</style>
</head>
<body>
  <div id="swagger-ui"></div>
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
