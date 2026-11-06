"""Release AD's site pages (PatentRef 1.22.0), on the site's page template (base.html):

  GET /agents/   the page for agents, from the Agents artboard of the re-skin canvas (2026-11-05) with the coordinator's
                 changes: what an agent can get, the MCP server patentref-mcp (a local stdio package from PyPI, listed in the
                 MCP Registry as io.github.patentref/patentref-mcp; there is no hosted MCP endpoint), its install lines for
                 four clients, its tools with their endpoints, the key rule and the limits, and the machine-readable files.
                 The tool names, the endpoints, the descriptions, the install lines and the smoke test are word for word from
                 the patentref-mcp 0.4.0 README on PyPI (a copy is lapse_accounts/tests/fixtures/patentref_mcp_0.4.0_README.md,
                 which the tests read back). The data version, the as-of date, the text data version and the structure
                 version are read when the page is made, never typed in; the limits are the Free plan's.
  GET /p/        the record page's address without a patent number: a temporary redirect (302) to the docs. It answered 404.

The Agents page carries one inline style and one inline script (the client tabs and the copy button); its
Content-Security-Policy allows those two by their SHA-256, as on the triage page (triage_page.csp_for), and nothing from
elsewhere. Without the script every install block is shown, each under its client's name.
"""
from django.http import HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from lapse_accounts import public
from lapse_accounts.triage_page import csp_for

SITE = "https://patentref.io"
PAGE_CACHE = "public, max-age=300"
PYPI_URL = "https://pypi.org/project/patentref-mcp/0.4.0/"
REGISTRY_NAME = "io.github.patentref/patentref-mcp"
REGISTRY_LISTED = "2026-11-05"   # the MCP Registry's publishedAt for 0.4.0 (2026-11-05T20:00:57Z)
MCP_VERSION = "0.4.0"
MCP_LICENSE = "MIT"              # PyPI's license_expression for 0.4.0

# The README's lines, word for word (its Run block and its Claude Desktop config). The Claude Code and Cursor blocks follow
# each client's own documented form with the same command and the same environment variable.
RUN_BLOCK = ("pip install patentref-mcp\n"
             "export PATENTREF_API_KEY=...        # a key from https://patentref.io/api/v1/meta/signup/\n"
             "patentref-mcp                       # stdio, what most MCP clients start\n"
             "patentref-mcp --http --port 8787    # streamable HTTP at http://127.0.0.1:8787/mcp")
CONFIG_JSON = '{"mcpServers": {"patentref": {"command": "patentref-mcp", "env": {"PATENTREF_API_KEY": "..."}}}}'
INSTALL = (
    ("ag-claude-desktop", "Claude Desktop", "pip install patentref-mcp\n\n# claude_desktop_config.json\n" + CONFIG_JSON),
    ("ag-claude-code", "Claude Code", "pip install patentref-mcp\nclaude mcp add patentref -e PATENTREF_API_KEY=... -- patentref-mcp"),
    ("ag-cursor", "Cursor", "pip install patentref-mcp\n\n# .cursor/mcp.json\n" + CONFIG_JSON),
    ("ag-stdio", "Generic stdio", RUN_BLOCK),
)
SMOKE = "python -m patentref_mcp.smoke 10000364"

# The tools of patentref-mcp 0.4.0, word for word as the Tools section of its README prints them: the call, the endpoint and
# the description, in the README's order.
TOOLS = (
    ("get_claim_tree(patent_id)", "GET /api/v1/structure/claims/<patent_id>/",
     "one patent's claims as a tree, from the claim text as the USPTO published it; every part carries start and end offsets into its claim's text"),
    ("get_patent_brief(patent_id, max_tokens)", "GET /api/v1/structure/brief/<patent_id>/",
     "one patent in a fixed order, cut to fit max_tokens (300 to 32000, default 2000), each independent claim as its elements with their offsets"),
    ("check_patent_status(patent_id)", "GET /api/v1/lapse/status/<patent_id>/",
     "the status record of one patent, with its basis, as-of date and data version"),
    ("search_patents(q, f, s, size, after)", "GET /api/v1/patent/", "the PatentSearch v1 query grammar"),
    ("similar_patents(text, n)", "GET /api/v1/lapse/similar", "the US patents nearest to a title and abstract"),
    ("build_facts()", "GET /api/v1/meta/", "the data version and as-of date in service"),
    ("search_disclosures(q)", "GET /registry/search", "public records of the PatentRef registry"),
    ("rep_required, fee_schedule, route_windows, pph_routes, ai_use_statement_required, grace_period, rules_changelog, rules_index",
     "GET /api/v1/rules/", "rows of patent office rules, each with its source URL and a verbatim quote"),
)

# The coordinator's text for the block after the tools (2026-11-05), word for word; the address is a mailto link.
NEXT_TEXT = ("for each patent, the problem it states and the parts it names, each quoted with its place in the text, and linked "
             "to other patents that name the same parts. Not live yet. If your agent would use it, tell us at")
NEXT_MAIL = "hello@patentref.io"

# The files an agent can read without a key, each served by this site (the rail).
MACHINE_READABLE = ("/llms.txt", "/llms-full.txt", "/openapi.json", "/api/v1/meta/status.json", "/api/v1/meta/", "/changes.txt")


def _text_data_version():
    try:
        from API.search_sqlite import LapseSQLiteSearch

        return LapseSQLiteSearch.from_django_settings().text_data_version()
    except Exception:  # noqa: BLE001 - the page leaves the clause out
        return None


def _structure_version():
    try:
        from API import lapse_claimgraph

        return lapse_claimgraph.STRUCTURE_VERSION
    except Exception:  # noqa: BLE001 - claimgraph missing: the page leaves the clause out
        return None


def agents_context():
    from lapse_accounts import metering
    from lapse_accounts.models import PLAN_FREE
    from lapse_accounts.views import _data_version   # here, not at the top: views imports public, which this imports

    version, as_of = _data_version()
    monthly, per_minute = metering.plan_limits(PLAN_FREE)
    return {"data_version": version, "as_of": as_of, "text_data_version": _text_data_version(),
            "structure_version": _structure_version(), "footer_lines": public.footer_lines(), "site": SITE,
            "monthly": f"{monthly:,}", "per_minute": per_minute, "install": INSTALL, "smoke": SMOKE, "tools": TOOLS,
            "next_text": NEXT_TEXT, "next_mail": NEXT_MAIL, "machine_readable": MACHINE_READABLE, "pypi_url": PYPI_URL,
            "registry_name": REGISTRY_NAME, "registry_listed": REGISTRY_LISTED, "mcp_version": MCP_VERSION,
            "mcp_license": MCP_LICENSE}


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def agents(request):
    html = render_to_string("lapse_accounts/agents.html", agents_context(), request=request)
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["Content-Security-Policy"] = csp_for(html)
    resp["Cache-Control"] = PAGE_CACHE
    return resp


@csrf_exempt   # a POST is refused (405) by the method rule below, never by a CSRF page
@require_safe
def record_index(request):
    """/p/ and /p without a patent number: a temporary redirect to the docs (it answered 404 until release AD)."""
    resp = HttpResponseRedirect("/docs/")
    resp["Cache-Control"] = PAGE_CACHE
    return resp
