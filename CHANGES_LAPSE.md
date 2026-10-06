# Lapse changes to PatentSearch-API (branch `sqlite-backend`)

Goal: serve the PatentSearch v1 API (paused by USPTO on 2026-03-20) from SQLite instead of
Elasticsearch, keeping upstream endpoint configs, serializers, URL routes, query grammar, validation
and error strings unchanged. Upstream base: `e998bf2`.

## Design

The seam is `PVAPIView.get_elastic_response`, which calls `search()` and `count()` on a searcher and
reads `hits.hits[]._source` and `count`. The upstream `QueryParser` is left untouched and still emits
Elasticsearch DSL. The new backend translates that DSL (the small subset the parser can produce:
`match`, `match_phrase`, `terms`, `range`, `prefix`, `wildcard`, `bool`, `nested`) into SQL and
returns ES-shaped responses. Keeping the parser unchanged means the grammar, field validation and
every 400 message are byte-identical by construction. Value errors that ES would raise (bad dates,
bad numbers, sort on text fields, malformed `match` objects) are raised as `elasticsearch.ApiError`
objects with the same `root_cause` types, so the unchanged `API/exceptions.py` maps them to the same
status codes and `X-Status-Reason` headers.

Select the backend with the Django setting `LAPSE_BACKEND` (`sqlite` or `elasticsearch`, default
`elasticsearch`, so upstream settings modules behave exactly as before).

## Files touched

| File | Change | Why |
|---|---|---|
| `API/search.py` | added `get_searcher()` (10 lines) | backend switch; ES class unchanged |
| `API/PVAPIViews.py` | import `get_searcher`; one call site uses it | the only coupling point to ES |
| `API/UsageLogging.py` | `USAGE_LOG_BACKEND` = redis (default) / sqlite / none | run without Redis |
| `API/search_sqlite.py` | new | ES DSL to SQL translator, SQLite executor, `_source` filtering, keyset `search_after` |
| `pvapi/settings/lapse_local.py` | new | SQLite Django DB, SQLite search backend, no MySQL/Redis/ES/OAuth, console logging, the 501 header middleware |
| `API/lapse_errors.py` | new | `LapseNotImplemented` (the documented 501) and the middleware that puts its headers on the response |
| `requirements/lapse.txt` | new | `base.txt` minus `mysqlclient` (needs libmysqlclient-dev to build; unused with SQLite) |
| `lapse_tools/build_sample.py` | new | builds the sample database from PatentsView bulk TSVs |
| `lapse_tools/create_test_key.py` | new | creates a local API key (written outside the repo) |
| `lapse_tools/compat/` | new | contract runner and contract scripts (copies of `C:\LapseAPI\compat`) |
| `.gitignore` | appended | never commit databases, bulk files or keys |
| `CHANGES_LAPSE.md` | new | this file |

No endpoint config, serializer, URL route, parser, exception handler, permission or throttle file was changed.

## Database layout (read by `API/search_sqlite.py`)

* `<index>`: one row per ES document (`patents`, `inventors`, `assignees`, `locations`, `us_patent_citations`), columns named exactly like the API/ES fields.
* `<index>__<nested path>`: one row per nested object, `_pid` = parent key, `_ord` = source order (for example `patents__inventors`, `patents__cpc_current`, `inventors__inventor_years`).
* `fts_<table>`: FTS5 (`unicode61 remove_diacritics 0 tokenchars '_'`) over every ES `text` field of that table, including nested text fields.
* `_lapse_fields`: ES type and `.keyword` availability per field, copied from es-data-load schemas.
* `_lapse_indices`: index name, table and key field.

Adding an endpoint is a data task: create its tables with the same naming and add rows to
`_lapse_indices` / `_lapse_fields`; no code change is needed.

## Semantics

* `match` on `.keyword` or `keyword` fields: exact, case-sensitive equality. `.keyword` sub-fields honor `ignore_above: 256`.
* `match` on `text` fields (implicit `_eq`, `_text_any`, `_text_all`): FTS5 token match, OR or AND.
* `match_phrase` (`_text_phrase`): FTS5 phrase.
* `terms` (list values): any-of; on `text` fields only a single lowercase token can match (ES does not analyze `terms`).
* `range`, `prefix` and `wildcard` (with `case_insensitive`), `bool` filter/must/should/must_not. On analyzed `text` fields (no `.keyword` in play) these work on the tokens like ES: `prefix` is an FTS5 prefix query, `wildcard` matches any token against the pattern, `range` is a term range over the tokens (bounds compared raw, tokens lowercase). On numeric, date and boolean fields `prefix` and `wildcard` raise the ES `query_shard_exception` ("Can only use prefix queries on keyword, text and wildcard fields - not on [f] which is of type [t]"), which the upstream handler reports as 500 ERR_ES.
* Value parsing follows the ES field types: a partial date spans its unit (`2010` is the whole year, `2010-06` the month; range bounds round down for `gte`/`lt` and up for `gt`/`lte` like ES date math); a fractional value on a `long`/`integer` field is a match-none term, is skipped by `terms`, and rounds a range bound inward (ES `NumberType.LONG`), never a 400; bad dates, numbers and booleans raise the same root-cause types ES does (gate 2.2 matrix in the patentref repo, `tests/compat/operator_matrix.md`).
* `nested`: `parent_key IN (SELECT _pid FROM child WHERE ...)` per criterion, so two criteria on one group match independent children, like ES.
* Unmapped fields in a query match nothing (ES behavior); unmapped sort fields and sorts on `text` fields raise the ES errors.
* Sort: missing values last for asc and desc, final tiebreak on rowid. `search_after` is expanded to `(a > x) OR (a = x AND b > y) ...` with per-direction operators and the missing-last rule.
* `total_hits`: exact `COUNT(*)` with the same WHERE clause.
* Nested groups in responses: one query per requested group for the whole page, attached as lists; groups with no children are omitted, as in ES `_source`.

## Deferred endpoints (the documented 501)

A view whose index is not in `_lapse_indices` answers:

```
HTTP 501
X-Status-Reason: Endpoint not implemented: the '<index>' data set is not in this snapshot[; <note>]
X-Status-Reason-Code: ERR_NOT_IMPLEMENTED
{"error":true}
```

The note for `publications` is `pre-grant publications are deferred (PatentRef checklist 1.5)`. Views whose
tables arrive with later data loads (claims text, attorneys, examiners and so on) answer the same 501 without
a note until their tables exist; the upstream prototype answered a 500 `ERR_ES` here. The body is the upstream
error body so clients that branch on `error: true` keep working; the status and the code header are the only
new values. Nothing in the upstream exception handler changed: `LapseNotImplemented` is a DRF `APIException`
that the handler's final branch passes to DRF, and `LapseErrorHeadersMiddleware` (registered only by
`lapse_local`) adds the two headers to that response.

## Known differences from Elasticsearch

* Tokenization: FTS5 `unicode61` versus the ES `standard` analyzer. Apostrophes, dots inside numbers and
  some scripts split differently; diacritics are kept, as in ES.
* `prefix` / `wildcard` on analyzed `text` fields (for example `_begins` on `patent_title`) run as a
  substring test on the whole value, not per token.
* Ties in the sort order are broken by rowid; ES breaks them by internal doc id. `search_after` skips
  rows that tie on every sort key, exactly like ES.
* Dates are compared as `YYYY-MM-DD` strings after the same parsing ES would accept
  (`strict_date_optional_time||epoch_millis`).
* The null check behind "missing values sort last" is cached per column, so the database is treated
  as read-only while the server runs.

## Sample database (built 2026-09-26, not committed)

Source: PatentsView PVGPATDIS bulk TSVs from the USPTO Open Data Portal. Rule: numeric part of
`patent_id` mod 94 == 0 (100,601 patents) plus the ids named in the contract examples and upstream
tests and up to 40 patents per documented example name (Whitney, Hopper, Whitener, Heath, George
Washington, first names containing "sarvo", assignees starting with "Apple", "CNH Industrial Canada,
Ltd.", withdrawn patents): 100,917 patents. Entity statistics (inventor/assignee counts, first/last
seen, yearly counts, last known location; location patent/inventor/assignee counts) are computed over
the full corpus, not the sample.

| table | rows |
|---|---|
| patents | 100,917 |
| patents__inventors | 256,737 |
| patents__assignees | 93,456 |
| patents__cpc_current | 635,040 |
| patents__application | 100,893 |
| patents__wipo | 138,129 |
| patents__figures | 94,853 |
| patents__us_term_of_grant | 55,275 |
| patents__foreign_priority | 46,564 |
| patents__pct_data | 23,929 |
| patents__gov_interest_organizations | 2,561 |
| patents__gov_interest_contract_award_numbers | 2,483 |
| patents__botanic | 231 |
| inventors / inventors__inventor_years | 218,536 / 1,997,186 |
| assignees / assignees__assignee_years | 30,113 / 326,515 |
| locations | 100,452 |
| us_patent_citations (citing patent in sample) | 1,623,218 |

Not in the sample: attorneys, examiners, applicants, cpc_at_issue, ipcr, uspc_at_issue, us_related_documents,
granted_pregrant_crosswalk nested groups; `patent_num_foreign_documents_cited`,
`patent_num_us_applications_cited`, `patent_num_total_documents_cited`, `patent_detail_desc_length` and the
two average-processing-days fields are NULL. `*_years_active` is the number of distinct grant years.

## Pointing it at the full corpus

The backend needs no change: set `LAPSE_SQLITE_PATH` to a database with the same layout.
`lapse_tools/build_sample.py --mod 1` selects every patent. At full scale expect roughly 9.5M patents,
24M inventor rows and 150M citation rows; plan for 60 to 90 GB and a few hours of build time.

Streaming build (2026-10-01): the script no longer holds any whole table in Python memory, so `--mod 1`
fits on a 64 GB box. Base `patents` rows are inserted straight from the stage join; abstract, gov interest
statement, earliest application date, term extension, citation counts and processing days are applied
afterwards with keyed `UPDATE ... WHERE patent_id=?` batches. Citation rows, the child tables, and the
entity tables stream through chunked `executemany` (a small `Batcher` lets one scan feed two tables).
The per-patent state is one dict of patent_id to position plus two int arrays for citation counts.
`pick_sample` checks existence only for the name and withdrawn extras, since the mod-rule and fixed ids
are read from the stage itself. Output is row-for-row identical to the previous script, rowids and FTS
included; verified on synthetic bulk files for mod 1, 3 and 94 and on the server against the mod-94
sample (see the patentref handoff for 2026-10-01). Before going to production add FTS5 `trigram` indexes (or equivalent) for the
`_contains` / `_begins` keyword fields: on the sample a substring scan of 256k inventor names takes
about 100 ms, which becomes seconds at 24M rows.

## Running locally

```
set DJANGO_SETTINGS_MODULE=pvapi.settings.lapse_local
python manage.py migrate
python manage.py createcachetable
python lapse_tools\create_test_key.py
python manage.py runserver 127.0.0.1:8765
```

Environment: `LAPSE_DATA_DIR` (where the Django DB, key file, usage log and the default search database live;
default `..\data`), `LAPSE_SQLITE_PATH` (search database, default `<LAPSE_DATA_DIR>\sample.db`),
`LAPSE_THROTTLE_RATE` (default upstream `45/m`), `LAPSE_USAGE_LOG` (`sqlite` default, `none`, `redis`).

## Running on Linux (patentref-us1, 2026-10-01)

```
python3 -m venv /data/api/venv
/data/api/venv/bin/pip install -r requirements/lapse.txt
export LAPSE_DATA_DIR=/data/api DJANGO_SETTINGS_MODULE=pvapi.settings.lapse_local
/data/api/venv/bin/python manage.py migrate && /data/api/venv/bin/python manage.py createcachetable
nice -n 10 /data/api/venv/bin/python lapse_tools/build_sample.py --bulk /data/lapse/patentsview \
    --out /data/api/sample.db --stage /data/api/stage.db --es-data-load /srv/api/es-data-load
/data/api/venv/bin/python lapse_tools/create_test_key.py
nohup /data/api/venv/bin/gunicorn pvapi.wsgi:application --bind 127.0.0.1:8765 --workers 4 --threads 2 \
    --timeout 180 > /data/lapse/logs/api_8765.log 2>&1 &
LAPSE_API_KEY_FILE=/data/api/test_api_key.txt LAPSE_SQLITE_PATH=/data/api/sample.db \
    /data/api/venv/bin/python lapse_tools/compat/run_contract.py --out /tmp/contract.md
```

`manage.py check --settings=pvapi.settings.lapse_local` passes with the `lapse.txt` set; the only package
dropped from `base.txt` is `mysqlclient`.

## 2026-10-02 public hostnames
- lapse_local: LAPSE_ALLOWED_HOSTS adds public hostnames to ALLOWED_HOSTS; LAPSE_BEHIND_PROXY=1 trusts X-Forwarded-Proto and X-Forwarded-Host from the reverse proxy (Caddy on patentref-us1).

## 2026-10-02/03 the remaining granted-set tables (gates 1.2 to 1.4), fork 9f0d160

`lapse_tools/build_sample.py` loads 14 more PatentsView zips and no code outside the build script changed: the
views that answered the 501 for `attorneys`, `us_application_citations`, `foreign_citations`, `other_references`,
`rel_app_text`, `cpc_groups`, `uspc_subclasses` and `ipcr` serve as soon as their rows are in `_lapse_indices`.

* New nested groups of `patents`: `cpc_at_issue` (g_cpc_at_issue), `ipcr` (g_ipc_at_issue), `uspc_at_issue`
  (g_uspc_at_issue), `examiners` (g_examiner_not_disambiguated, `examiner_id` null: the bulk file carries none),
  `applicants` (g_applicant_not_disambiguated, `location_id` null: `rawlocation_id` is not a disambiguated id),
  `us_related_documents` (g_us_rel_doc), `attorneys` (g_attorney_disambiguated through the stage).
* New indices: `attorneys` (one row per attorney id with first/last seen, patents, inventors, years active, the
  same shape as `inventors`), `us_application_citations` and `foreign_citations` (uuid `patent_id-sequence`),
  `other_references` (uuid `patent_id-sequence`, `reference_sequence` kept as text because the ES schema types it
  keyword), `rel_app_text` (uuid `patent_id-k`, k = order within the patent), `cpc_groups` (g_cpc_title, one row per
  non-empty cpc_group), `uspc_subclasses` (distinct subclass ids seen in g_uspc_at_issue, first title wins),
  `ipcr` (the lookup the upstream `/api/v1/ipc/` serves: one row per distinct section+class+subclass seen in
  g_ipc_at_issue, `ipc_id` = the three concatenated, e.g. `G01S`; es-data-load reads it from a four-column
  `ipcr` table, so it is small by design: 5,746 rows on the full corpus, with the raw pre-IPC-8 codes the file
  carries, against 25,505,642 nested `patents__ipcr` rows).
* `persistent_inventors` and `persistent_assignees`: g_persistent_inventor and g_persistent_assignee as shipped
  (every column of the header, indexed by patent_id, no endpoint; gate 1.2 names them).
* `patents.patent_num_us_applications_cited`, `patent_num_foreign_documents_cited` and
  `patent_num_total_documents_cited` are filled (total = US patents + US applications + foreign documents, as
  PatentsView-DB computes it); they were NULL before.
* Rows whose `patent_id` is not in g_patent are dropped, as for every other child table: on the 2026-10-01
  release that is 29,648 attorney rows, 84,933 persistent inventor rows and 26,987 persistent assignee rows;
  every other new table equals its zip row for row (patentref `ops/audits/2026-10-03_gates-1.2-1.4_full2_patentref-us1.txt`).
* The 15 original tables are built exactly as before: the mod-94 sample from this script is identical to
  `sample.db` on every reference table (rowids, FTS content included; only `sqlite_stat1` grows with the new
  indexes), and on the full corpus the 15 tables of `full2.db` hash equal to `full.db`.
* Still 501: the publication views (checklist 1.5), the claims and description text views (1.6),
  `cpc_classes`, `cpc_subclasses`, `uspc_mainclasses` (no bulk file; derive from g_cpc_title / g_uspc_at_issue
  when wanted), `nber_*` (dead upstream), `rel_app_text_publications`.
* Full build on patentref-us1 (62 GB RAM): 5,317 s, 141 GB (`/data/api/full2.db`); the stage file grows to
  7.5 GB with the attorney rows.

## 2026-10-05 accounts and Free-tier keys (PatentRef checklist 5.10), app `lapse_accounts/`

Supabase Auth is the identity system (PatentRef decision 2026-09-30); this app mints upstream
`APIUserKey` records from a Supabase account and meters them. Upstream key handling is untouched:
`X-Api-Key`, `HasUserAPIKey`, the 403 on a missing or revoked key, and `APIKeyThrottle` at 45/m.

* `lapse_accounts/models.py`: `AccountKey` (one-to-one with `API.APIUserKey`: `supabase_user_id`,
  `email`, `plan` = `free`, `rotated_from`) and `MonthlyUsage` (`subject`, `month` YYYY-MM UTC, `count`).
  Migration `lapse_accounts/0001_initial.py`. No upstream model or migration changed.
* `lapse_accounts/supabase_jwt.py`: verifies the Supabase access token (ES256 or RS256) against the
  project JWKS at `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`, fetched with urllib and cached in
  process and in the Django cache (re-fetched after 6 h, at most once a minute on an unknown `kid`; a
  failed fetch keeps the cached keys). Checks `exp`, `aud` = `authenticated`, `iss`, `role`, not
  anonymous, `user_metadata.email_verified` not false. Django never stores a password.
* `lapse_accounts/views.py`, `urls.py`, `root_urls.py`: routes under `/api/v1/meta/`, placed before
  upstream's `pvapi.urls` by `ROOT_URLCONF = "lapse_accounts.root_urls"` (lapse_local only).
  `GET signup/` is the account page (plain HTML, supabase-js from jsdelivr with the anon key);
  `GET|POST keys/` (Bearer) lists or mints the caller's key, `{"action": "rotate"}` revokes the old
  one and mints a new one, the key value is returned once; `GET usage/` (X-Api-Key or Bearer) returns
  `{"error": false, "plan", "key_prefix", "month", "count", "monthly_limit", "remaining",
  "per_minute_limit", "resets_at"}`. Errors keep the upstream shape: `{"error": true}` with
  `X-Status-Reason` and `X-Status-Reason-Code` (`ERR_AUTH` 401, `ERR_KEY` 403, `ERR_Q` 400).
* `lapse_accounts/middleware.py`: counts every keyed request to a data endpoint under `/api/v1/`
  (not `/api/v1/meta/`) whose status is below 500 and not 403 or 429, per `user:<id>` for account
  keys (a rotated key keeps the month's count) and per `key:<prefix>` for keys without an account.
* `lapse_accounts/throttling.py`: `PlanKeyThrottle` is upstream's `APIKeyThrottle` (same ident,
  cache key and 429) with the rate chosen per key: account keys get the plan's per-minute allowance
  (45/m for Free), keys without an account keep the `key` rate (`LAPSE_THROTTLE_RATE`, 45/m by
  default; the server runs the compat suites with a high rate on the operator key).
  `MonthlyKeyThrottle`, listed after it, denies when the month's count has reached the plan allowance; DRF answers the upstream 429 shape
  (`{"detail": "Request was throttled. Expected available in N seconds."}`, `Retry-After` = seconds
  to 00:00 UTC on the first of next month). Keys without an account have no monthly cap.
  `MetaThrottle` is a per-IP `meta` scope (30/m) on the account endpoints.
* `pvapi/settings/lapse_local.py`: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_JWKS_URL`,
  `SUPABASE_JWT_ISSUER` (the last two derived from the URL), `LAPSE_FREE_MONTHLY_LIMIT` (1000),
  `LAPSE_FREE_MINUTE_LIMIT` (45, the per-minute rate of account keys; `LAPSE_THROTTLE_RATE` stays the
  rate of keys without an account), `LAPSE_DATA_VERSION` (header of the account page; defaults to the search database's name),
  `INSTALLED_APPS += lapse_accounts`, the metering middleware, the two throttle classes. The
  service_role key is never read by this app.
* Tests: `lapse_accounts/tests/` (16, pytest, no Supabase or search corpus needed: a temporary Django
  DB, a three-row search database in the Lapse layout and a locally generated P-256 key pair standing
  in for the JWKS). Run `python -m pytest lapse_accounts/tests -q` from the fork root.

## 2026-10-06 X-Data-Version and the per-request file resolution (PatentRef checklist 4.3)

Additive only: no upstream body or header changes; one new response header and one new behavior of
the SQLite backend when the configured path is a symlink.

* `X-Data-Version` on every response (data endpoints, 400, 403, 429, 501, the account endpoints):
  the `data_version` of the search database that produced the body. Source: the row
  `_lapse_build.data_version` the refresh runner stamps into every build (`20260929.1` and so on);
  for a file built before the runner the fallback is `LAPSE_DATA_VERSION` from the environment if
  set, else the served file's name without the extension (`full2.db` serves as `full2`).
  Middleware `API.lapse_errors.DataVersionMiddleware`, outermost in `lapse_local` so throttled and
  refused requests carry it too. The value is read from the thread's cached connection after the view
  ran, so the header can never name a file other than the one the body came from.
* `API/search_sqlite.py`: `LapseSQLiteSearch` resolves the configured path (`os.path.realpath`) once
  per instance, and the view builds one instance per request, so every query of a request hits the
  same file even if the symlink moves while the request runs. A thread's cached connection is closed
  and reopened when the resolved file differs from the one it holds, so after
  `snapshot_current.db` is repointed the next request on each thread serves the new file with no
  worker restart; the schema caches are keyed by the resolved path. The swap tool still sends the
  gunicorn master a SIGHUP after the rename so the old file's handles and caches are released.
* `lapse_accounts/views.py`: the account page shows the served file's `data_version` from the same
  source as the header.
* Tests: `lapse_accounts/tests/test_data_version.py` (4): the header follows the symlink across a
  swap and a rollback with the body from the same file, every response carries exactly one header,
  a request never reopens mid-request, the unstamped-file fallback.

## 2026-10-06 latency on the full corpus (PatentRef checklist 2.10), fork b180248 to 2b89c76

Response bodies unchanged; every change below is an index, a SQL shape, a planner hint or a memo of a
value the same SQL already returned. On the full corpus (patentref `ops/audits/2026-10-06_gate-2.10_latency_patentref-us1.txt`):
contract examples p95 7.5 s to 62 to 67 ms, a 1000-row page 0.4 s cold; the index upgrade of a 141 GB file takes 19 min in place and adds 17 GB. `lapse_tools/tests/test_sql_shapes.py` (34 tests) runs every
query family through the backend with and without the new tables and compares both with a Python
evaluation of the ES semantics, pages and cursors included.

* Index set 2 (`build_sample.py`, `INDEX_SET = "2"`, row `index_set` in `_lapse_build`): child tables
  index `(column, _pid)` instead of `(column)`, so a nested criterion is answered from the index alone;
  `patents` adds `(withdrawn, patent_date)`, `(withdrawn, patent_type)`, `(withdrawn, patent_year)`,
  `(withdrawn, patent_zero_prefix)`, covering the implicit `withdrawn=false` filter of every `/patent/`
  count (a count over 9.4M rows is 120 to 300 ms from the index instead of 2.3 s through the rows), and
  `(patent_id, withdrawn)` so the count of a nested group (`patent_id IN (SELECT _pid ...)`) probes an index
  instead of reading a row per key (a CPC section: 2.9 s to 1.8 s warm).
  `build_sample.py --upgrade-indexes <db>` applies the same DDL to an existing file (drops the superseded
  single-column indexes, creates what is missing, analyzes only the new indexes, writes the row last).
* `fts_trgm_<table>`: FTS5 `trigram` tables over the string columns `_contains` and `_begins` are asked
  on (`TRIGRAM` in `build_sample.py`: titles, inventor and assignee names and cities, attorneys,
  applicants, examiners, `cpc_group_id`, `patent_id` and `patent_zero_prefix` for `_begins` on ids, the entity tables). The translator adds
  `rowid IN (SELECT rowid FROM fts_trgm_t WHERE fts_trgm_t MATCH 'col : "needle"')` in front of the
  existing predicate when the needle is ASCII and at least three characters long; the existing
  predicate (Python folding, token test) still decides, so the result set is the same. Shorter or
  non-ASCII needles run the scan as before (the Greek final sigma folds differently in SQLite and
  Python; a two-character needle has no trigram). `patent_abstract` has no trigram table (about 30 GB).
* `_lapse_value_stats` (`VALUE_STATS`): value frequencies of the low-cardinality columns (patent type,
  withdrawn, year, kind, CPC section/class/subclass/type, inventor and assignee country/state/type,
  application type, series code, WIPO field), a by-year histogram of `patent_date` (col `patent_date/4`)
  and each table's row count. The translator uses them for: `likelihood(col = ?, p)` hints on equality
  terms (SQLite's own estimate is rows / distinct values, so it took `patent_type = 'utility'` for 860k
  rows instead of 8.5M and sorted 8.5M rows for a 100-row page); `likelihood(range, p)` on a date or
  integer range from the histogram or the values (SQLite guesses a quarter of the rows for any one-sided
  range, so `patent_date >= 1900-01-01` sorted by patent_id walked the date index and sorted 9.4M rows,
  2.3 s, instead of the key index, 1 ms); `must_not` of equality on such a column when the complement is 10 percent of rows or rarer,
  written as the union of the index ranges around the excluded values (NULL included, as ES counts a
  missing value as not matching), in a `rowid IN (SELECT rowid FROM t WHERE ...)` subquery on a page so
  the planner does not scan the sort index for it (`_neq` on three patent types: 2.2 s to 10 ms);
  `EXISTS` instead of `IN` for a nested group on a page when the group's matching rows are at least
  2 percent of the parent rows (a CPC section: 1 s to 1 ms; the count keeps the `IN` form, which is
  faster for it).
* `search_after`: when no sort column holds NULLs and no cursor value is missing, the keyset is written
  in the nested form `k1 <= v1 AND (k1 < v1 OR (k2 <= v2 AND ...))`, the same rows as the OR expansion,
  with a leading range term the planner can walk the sort index from (page 2 of a date-descending
  sort: 4.6 s to 6 ms).
* `INDEXED BY` the single-column index of the first sort key when the stats say at least 5 percent of
  rows pass the filter, the sort key is not the key column, has no NULLs and there is no cursor; if
  SQLite answers "no query solution" the statement is rerun without the hint.
* `total_hits` memo (`LAPSE_COUNT_CACHE`, default `<LAPSE_DATA_DIR>/count_cache.sqlite3`, `none` to
  turn it off): a count is a pure function of (data file, query), so a count that took at least
  `LAPSE_COUNT_CACHE_MIN_MS` (20) is stored under the sha256 of (data_version, index, SQL, parameters),
  in the process and in a small shared SQLite file every worker reads, and a repeat answers in a
  millisecond on any worker and after a restart. The analogue of the Elasticsearch shard request
  cache; a swap changes the data_version and so the keys. Nothing here can change a body.
* Pragmas from settings: `LAPSE_SQLITE_CACHE_KB` (400000), `LAPSE_SQLITE_MMAP` (1 GiB; the Python
  build caps mmap at 2 GiB anyway), `temp_store=MEMORY`.
* `LAPSE_SQL_LOG=<file>`: one JSON line per executed statement (SQL, parameters, milliseconds) for
  profiling; off when unset.
