"""Release AD's site pages (PatentRef 1.22.0), on the site's page template (base.html):

  GET /agents/   the page for agents, from the Agents artboard of the re-skin canvas (2026-11-05) with the coordinator's
                 changes: what an agent can get, the MCP server patentref-mcp (a local stdio package from PyPI, listed in the
                 MCP Registry as io.github.patentref/patentref-mcp; there is no hosted MCP endpoint), its install lines for
                 four clients, its tools with their endpoints, the key rule and the limits, and the machine-readable files.
                 The install lines and the smoke test are word for word from the patentref-mcp README on PyPI (0.4.0's, which
                 0.5.0 keeps; copies are in lapse_accounts/tests/fixtures/, which the tests read back). The tools table (release
                 AH): the 19 tools of patentref-mcp 0.5.0 with the endpoint each one calls, in 12 rows (the eight rules tools
                 share one); the rows release AD took from the 0.4.0 README stand as they were, and the four S1-D tools
                 (experimental, live in the API since release AG2 on 2026-11-23, in patentref-mcp from 0.5.0) follow the brief.
                 The page names no PyPI release of its own (it links the project page), so it reads true whether 0.5.0 is on
                 PyPI yet or not. The data version, the as-of date, the text data
                 version and the structure version are read when the page is made, never typed in; the limits are the Free
                 plan's.
  GET /p/        the record page's address without a patent number: a temporary redirect (302) to the docs. It answered 404.

The Agents page carries one inline style and one inline script (the client tabs and the copy button); its
Content-Security-Policy allows those two by their SHA-256, as on the triage page (triage_page.csp_for), and nothing from
elsewhere. Without the script every install block is shown, each under its client's name.
"""
from django.http import HttpResponse, HttpResponseRedirect
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_safe

from lapse_accounts import previews, public
from lapse_accounts.triage_page import csp_for

SITE = "https://patentref.io"
PAGE_CACHE = "public, max-age=300"
PYPI_URL = "https://pypi.org/project/patentref-mcp/"   # release AH: the project page, which shows the newest release
REGISTRY_NAME = "io.github.patentref/patentref-mcp"
REGISTRY_LISTED = "2026-11-05"   # the day the MCP Registry first listed the server (0.4.0's publishedAt, 2026-11-05T20:00:57Z)
MCP_LICENSE = "MIT"              # PyPI's license_expression for 0.4.0, 0.4.1 and 0.5.0
TOOLS_COUNT = 19                 # the tools of patentref-mcp 0.5.0 (15 in 0.4.0 and 0.4.1)
S1D_TOOLS_FROM = "0.5.0"         # the release of patentref-mcp that adds the four S1-D tools

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

# The tools of patentref-mcp: the call, the endpoint and a description. The rows of the 0.4.0 tools are word for word as the
# Tools section of its README printed them (release AD), in its order; release AH puts the four S1-D tools of 0.5.0 after the
# brief (their calls and endpoints as patentref-mcp 0.5.0 sends them; the 0.5.0 README prints no endpoint and no short line).
S1D_TOOLS = (
    ("get_patent_units(patent_id, types)", "GET /api/v1/structure/units/<patent_id>/",
     "the parts a patent's claims name and the problem it states, each with a locator into the text (experimental)"),
    ("get_same_part_links(patent_id, size, after)", "GET /api/v1/structure/relations/<patent_id>/",
     "the patents whose claims name the same parts, ranked, a page at a time (experimental)"),
    ("locate_text(patent_id, field, sequence, start, end)", "GET /api/v1/structure/locate/",
     "the text at one locator, up to 2,000 characters (experimental)"),
    ("search_units(q, f, s, size, after)", "GET /api/v1/structure/units/",
     "units across patents in the PatentsView grammar, by patent number or by part (experimental)"),
)
TOOLS = (
    ("get_claim_tree(patent_id)", "GET /api/v1/structure/claims/<patent_id>/",
     "one patent's claims as a tree, from the claim text as the USPTO published it; every part carries start and end offsets into its claim's text"),
    ("get_patent_brief(patent_id, max_tokens)", "GET /api/v1/structure/brief/<patent_id>/",
     "one patent in a fixed order, cut to fit max_tokens (300 to 32000, default 2000), each independent claim as its elements with their offsets"),
) + S1D_TOOLS + (
    ("check_patent_status(patent_id)", "GET /api/v1/lapse/status/<patent_id>/",
     "the status record of one patent, with its basis, as-of date and data version"),
    ("search_patents(q, f, s, size, after)", "GET /api/v1/patent/", "the PatentSearch v1 query grammar"),
    ("similar_patents(text, n)", "GET /api/v1/lapse/similar", "the US patents nearest to a title and abstract"),
    ("build_facts()", "GET /api/v1/meta/", "the data version and as-of date in service"),
    ("search_disclosures(q)", "GET /registry/search", "public records of the PatentRef registry"),
    ("rep_required, fee_schedule, route_windows, pph_routes, ai_use_statement_required, grace_period, rules_changelog, rules_index",
     "GET /api/v1/rules/", "rows of patent office rules, each with its source URL and a verbatim quote"),
)

# The block after the tools (release AH; until then the coordinator's "Next" text of 2026-11-05, which said S1-D was not live
# yet, and it has been since release AG2 on 2026-11-23); the address is a mailto link.
S1D_TEXT = ("for each patent, the problem it states and the parts its claims name, each quoted with its place in the text, and "
            "the patents whose claims name the same parts. The four S1-D tools above call these endpoints, keyed and counted like "
            "the others, in the API since 2026-11-23. Tell us what your agent does with it at")
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
            "s1d_text": S1D_TEXT, "next_mail": NEXT_MAIL, "machine_readable": MACHINE_READABLE, "pypi_url": PYPI_URL,
            "registry_name": REGISTRY_NAME, "registry_listed": REGISTRY_LISTED, "tools_count": TOOLS_COUNT,
            "s1d_tools_from": S1D_TOOLS_FROM, "mcp_license": MCP_LICENSE, "preview": previews.agents_page()}   # release AE: the link preview


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
