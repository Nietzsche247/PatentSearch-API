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
* `range`, `prefix` and `wildcard` (with `case_insensitive`), `bool` filter/must/should/must_not.
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
