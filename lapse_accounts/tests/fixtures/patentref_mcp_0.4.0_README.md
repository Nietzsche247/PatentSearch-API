# patentref-mcp

<!-- mcp-name: io.github.patentref/patentref-mcp -->

The only free API and MCP tool we know of that returns a US patent's claim tree with element segmentation, antecedent links and character offsets, built by deterministic code, 1976 to now.

patentref-mcp is an MCP server for the PatentRef API (https://patentref.io). Each tool sends one HTTPS request to the public API with the key from its environment and returns the API's JSON as the API sent it.

## Run

```
pip install patentref-mcp
export PATENTREF_API_KEY=...        # a key from https://patentref.io/api/v1/meta/signup/
patentref-mcp                       # stdio, what most MCP clients start
patentref-mcp --http --port 8787    # streamable HTTP at http://127.0.0.1:8787/mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{"mcpServers": {"patentref": {"command": "patentref-mcp", "env": {"PATENTREF_API_KEY": "..."}}}}
```

Environment: `PATENTREF_API_KEY` (sent by every tool but `build_facts` and `search_disclosures`), `PATENTREF_BASE` (default `https://patentref.io`), `PATENTREF_TIMEOUT` (seconds, default 30).

Check an install: `python -m patentref_mcp.smoke 10000364` starts the server over stdio, lists its tools, makes two tool calls and prints the transcript.

## Tools

- `get_claim_tree(patent_id)`: one patent's claims as a tree, from the claim text as the USPTO published it; every part carries start and end offsets into its claim's text. `GET /api/v1/structure/claims/<patent_id>/`
- `get_patent_brief(patent_id, max_tokens)`: one patent in a fixed order, cut to fit `max_tokens` (300 to 32000, default 2000), each independent claim as its elements with their offsets. `GET /api/v1/structure/brief/<patent_id>/`
- `check_patent_status(patent_id)`: the status record of one patent, with its basis, as-of date and data version. `GET /api/v1/lapse/status/<patent_id>/`
- `search_patents(q, f, s, size, after)`: the PatentSearch v1 query grammar. `GET /api/v1/patent/`
- `similar_patents(text, n)`: the US patents nearest to a title and abstract. `GET /api/v1/lapse/similar`
- `build_facts()`: the data version and as-of date in service. `GET /api/v1/meta/`
- `search_disclosures(q)`: public records of the PatentRef registry. `GET /registry/search`
- `rep_required`, `fee_schedule`, `route_windows`, `pph_routes`, `ai_use_statement_required`, `grace_period`, `rules_changelog`, `rules_index`: rows of patent office rules, each with its source URL and a verbatim quote. `GET /api/v1/rules/`

An error from the API comes back as the tool's error text: the HTTP status and the API's `X-Status-Reason`. The server does not retry.

## License

MIT, see `LICENSE`. The data the API returns has its own license and attribution lines: https://patentref.io/LICENSES.md
