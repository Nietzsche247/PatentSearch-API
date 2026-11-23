# patentref-mcp

<!-- mcp-name: io.github.patentref/patentref-mcp -->

[![patentref-mcp MCP server - quality and maintenance score on Glama](https://glama.ai/mcp/servers/patentref/patentref-mcp/badges/score.svg)](https://glama.ai/mcp/servers/patentref/patentref-mcp)

patentref-mcp is an MCP server for the PatentRef API (https://patentref.io), a PatentsView-compatible API for US patents. Its 19 read-only tools give an agent a US patent's claim tree with element segmentation, antecedent links and character offsets (built by deterministic code), a patent brief that fits a token budget, the expiry and maintenance-fee status record, search over US grants from 1976 to now, patents similar to a text, patent office rules with their official sources, and (experimental, S1-D) the problem a patent states and the parts its claims name, each quoted with its place in the text, with the patents whose claims name the same parts.

## How it runs

The server runs on your own machine. Your MCP client starts it as a local process and talks to it over stdio, the default. Each tool call becomes one HTTPS request from your machine to the PatentRef API (https://patentref.io) with the key from your environment, and the tool returns the API's JSON as the API sent it. There is no hosted MCP endpoint for this server: you run it yourself.

`patentref-mcp --http` is for MCP clients that connect to a URL instead of starting a process. It serves the same tools as streamable HTTP from your machine, at http://127.0.0.1:8787/mcp by default (`--host` and `--port` change the address). The requests to the API still go out from your machine with your key, so anyone who can reach that address can use your key's allowance: keep the default 127.0.0.1 unless you mean other machines to reach it.

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

A free key allows 1,000 requests a month and 45 a minute. Each call of a keyed tool is one request; answers 400 and 404 count too, 403 and 429 do not. `build_facts` and `search_disclosures` send no key.

Check an install: `python -m patentref_mcp.smoke 10000364` starts the server over stdio, lists its tools, makes two tool calls and prints the transcript. `patentref-mcp --version` prints the version.

## Tools

- **check_patent_status**: Get the expiry and maintenance-fee status record of one granted US patent: status (active, expired_term, expired_fee_nonpayment, withdrawn or unknown), its basis, reinstatement_possible with its legal basis, as_of, data_version, a versioned permalink, a one-sentence `plain` statement and next_steps. Record facts, not a legal opinion. Use it for one known patent number; use search_patents with the "lapse" field group for the status of many patents at once, and get_patent_brief for the claims together with the status.

- **search_patents**: Search granted US patents by field values with the PatentSearch v1 query grammar and return the matching rows, a page at a time. Use it to find patents by title words, dates, CPC class, assignee, inventor or other fields; use similar_patents to rank patents by closeness to a passage of text, check_patent_status for one known number, and search_disclosures for PatentRef registry records rather than patents. Query operators: _eq, _neq, _gt, _gte, _lt, _lte, _begins, _contains, _text_all, _text_any, _text_phrase, _and, _or, _not; dotted names reach nested fields. The "lapse" field group adds expiry_status, expiry_date_estimated, expiry_basis, reinstatement_possible, fee_status and lapse_date. For the next page, pass the last row's sort values as after. Field names and the full grammar: https://patentref.io/llms-full.txt.

- **similar_patents**: Rank granted US patents by how close their title and abstract are to a passage of text, and return the n nearest with data_version and as_of. Use it when you have a description, an abstract or a claim and want related patents; use search_patents for exact filters such as dates, classes or names. The ranking is full-text bm25 over titles and abstracts, searched with the rarest words of the text, so the answer is a record of what the data holds, not a search opinion.

- **get_claim_tree**: Get every claim of one granted US patent as a tree parsed from the claim text the USPTO published: per claim its number, category, dependency, preamble, transition, elements, term mentions (each "the" or "said" phrase linked to its earlier mention), claim references and flags, with structure_version, data_version, text_data_version and as_of. Every part carries start and end offsets into its claim's text (text[start:end] is the part). Use it when you need all claims in full detail; use get_patent_brief for a shorter answer that fits a token budget, and check_patent_status for the status alone. Flags are notes on the text and the data, not conclusions about the claims. A patent without claim text in the data answers 404.

- **get_patent_brief**: Get a brief of one granted US patent that fits a token budget, in a fixed order: identity and dates, the status record with its basis, each independent claim as its elements with start and end offsets into the claim text, one line per dependent claim, and the claim term keys. The brief stops at the item that would go over max_tokens; truncated and omitted say what was left out, and tokens gives its size, never over max_tokens. Every claim and element carries an anchor: page + "#" + anchor opens it on the record page. Quotes are the claim text as published. Use it to read or summarize a patent within a context budget; use get_claim_tree for all claims in full structure, and check_patent_status for the status alone.

- **get_patent_units**: Get the S1-D units of one granted US patent (experimental): the parts its claims name (unit type Component, the claim's own words with the part in normal form) and the problem it states (unit type StatedProblem, the patent's own sentence or clause in its brief summary or the background, found by a named cue rule), each with a locator {patent_id, field, sequence, start, end}; quote the text at the locator. none_found means the rules found no such sentence, not that the patent has none. Every answer says experimental true and names s1d_version. Use it to read the problem a patent states and the parts it claims; use get_same_part_links for the patents whose claims name the same parts, locate_text to read a locator back, and get_claim_tree for the claims in full.

- **get_same_part_links**: Get the granted US patents whose claims name the same parts as one patent (S1-D names_same_part, experimental), ranked by the number of shared parts, then by how many patents name those parts (fewer before more), then by patent number, a page at a time; each link lists up to 10 shared parts with their quotes and locators in both patents. A link says only that both patents' claims name these part words. For the next page pass next_after as after. Use get_patent_units for one patent's own parts and problems, and similar_patents to rank patents by a passage of text.

- **locate_text**: Get the text at one S1-D locator (experimental): the field of one granted US patent at sequence, from start to end, up to 2,000 characters. Use it to quote or check a unit or a shared part that get_patent_units, get_same_part_links or search_units returned, and quote the text it gives; use get_claim_tree for whole claims.

- **search_units**: Search S1-D units across granted US patents (experimental) with the PatentsView grammar: q names patent_id (a value or a list of up to 100) or part (a part in normal form), and type, cue and section filter it; plain values, _eq, and _and or _or on one field. Each row is a unit with its text and locator; total_hits_exact and budget_reached say how far the count goes. For the next page pass next_after as after. Use get_patent_units for one patent, and get_same_part_links for ranked links between patents.

- **rep_required**: List the representation rules of a patent office: whether and when an applicant must act through a registered agent, each row with its official source URL and a verbatim quote. Use it for questions about needing a local agent; use fee_schedule for fees, route_windows for time limits, and rules_index for the offices and the act vocabulary. office takes US, EP, CN, JP, KR, AU or PCT.

- **fee_schedule**: List the official fees of a patent office in force on a date: filing, search, examination, grant, renewal and other fees with their amounts, currency and entity tiers as the cited schedule prints them, each row with its official source URL and a verbatim quote. A row is in force when effective_from <= as_of < effective_to. Use it for what an office charges; use rep_required for agent rules and route_windows for time limits. office takes US, EP, CN, JP, KR, AU or PCT.

- **route_windows**: List the generic time windows of a patent office, such as the PCT national phase time limit in months from the priority date, each row with its official source URL and a verbatim quote. Use it to learn how long a period an office allows; it never computes a last day for a specific application and takes no application number or date. Use fee_schedule for fees and pph_routes for Patent Prosecution Highway routes. office takes US, EP, CN, JP, KR, AU or PCT.

- **pph_routes**: List the Patent Prosecution Highway (PPH) rows of to_office whose cited source names from_office as a partner office, each with its official source URL and a verbatim quote, and to_office_pph_rows, the number of PPH rows held for to_office. Use it to see whether work done at one office can support a PPH request at another; use route_windows for time limits. An empty list means no PPH row of to_office names from_office.

- **ai_use_statement_required**: List the rows on whether a patent office requires a statement on the use of AI, each with its official source URL and a verbatim quote. An empty answer means the data holds no cited rule for that office; nothing follows from its absence. Use it for AI-use statement rules; use rep_required, fee_schedule or grace_period for the other rules of an office. office takes US, EP, CN, JP, KR, AU or PCT.

- **grace_period**: List what the cited law of a patent office says about disclosures made before filing (the grace period), each row with its official source URL and a verbatim quote. Citations only: it does not say whether any disclosure falls inside the period. Use it to read an office's grace period text; use search_disclosures to find dated public disclosures in the PatentRef registry, and route_windows for other time limits. office takes US, EP, CN, JP, KR, AU or PCT.

- **rules_changelog**: List the releases of the patent office rules data from newest to oldest, each with its version, date and the rows it added, closed, changed or removed. Use it to see what changed since a version or date you used before; use rules_index for the current version, the offices and the vocabulary.

- **rules_index**: Describe the patent office rules data: its version and as_of date, the offices it holds with their row counts by rule kind, each rules tool with its parameters, and the act vocabulary. Call it before the other rules tools to learn which offices and act values exist; use rules_changelog for the history of changes. Takes no arguments, so nothing can be filtered here.

- **build_facts**: Get the facts of the patent data build the API serves now: data_version, as_of, refreshed_at, the snapshot hash, superseded versions and the table delta. Use it to cite or check which data an answer came from, or to see when the data was last refreshed; it holds no patent data (use search_patents or check_patent_status) and no rules data (use rules_index). Takes no arguments.

- **search_disclosures**: Search the public records of the PatentRef registry (dated technical disclosures) whose title or abstract match q, from newest to oldest, up to 20. Each record carries its permalink (https://patentref.io/r/ and the record's id), server_time, bundle_hash and proof_status; the submitter's text is inside user_content, as entered. A record shows what was published and when, not a patent or an opinion. Use it to find disclosures; use search_patents or similar_patents to find patents.

Every tool is read only and carries the MCP annotations readOnlyHint, idempotentHint and openWorldHint. An error from the API comes back as the tool's error text: the HTTP status and the API's `X-Status-Reason`. The server does not retry.

## Changes

See CHANGELOG.md in the repository: https://github.com/patentref/patentref-mcp/blob/main/CHANGELOG.md

## License

MIT, see `LICENSE`. The data the API returns has its own license and attribution lines: https://patentref.io/LICENSES.md

Source: https://github.com/patentref/patentref-mcp. The package is published on PyPI (https://pypi.org/project/patentref-mcp/). Questions and problems: hello@patentref.io.
