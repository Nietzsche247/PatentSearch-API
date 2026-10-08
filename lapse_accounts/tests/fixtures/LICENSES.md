# License register (checklist 1.11)

What every table of the snapshot, every family source and every rules citation quote is licensed under, with the attribution line each one requires. The page footer of every PatentRef page is generated from the "Attribution lines" section at the end of this file (fork `lapse_accounts/public.py`, `footer_lines()`); edit the line here, never in a template. The file is served at https://patentref.io/LICENSES.md.

Rule: nothing goes into the snapshot, a page or a response unless its row is here. A new table, source or quote adds a row in the same commit.

## 1. The code

| Component | License | Where |
|---|---|---|
| PatentSearch-API (the API server PatentRef forked, USPTO, published 2026-04-21) | GPL-3.0 | github.com/PatentsView/PatentSearch-API, fork branch `sqlite-backend`; our changes are GPL-3.0 too and the fork stays public at gate 7.4 |
| Lapse (the `lapse` package, loaders, scoring, refresh runner) | GPL-3.0 (decision 2026-09-25: tool free on GitHub) | github.com/patentref/patentref |
| es-data-load, PatentsView-DB, PatentsView-Disambiguation (read for schemas, the table map and the 4.6 fallback; not shipped) | GPL-3.0 (LICENSE file of each repo, checked 2026-10-08) | `compat/UPSTREAM.md` pins |

## 2. The snapshot: data tables

Source for every table below: USPTO PatentsView bulk data files (PVGPATDIS, PVGPATTXT) and the USPTO Open Data Portal maintenance-fee events (PTMNFEE2), downloaded from https://data.uspto.gov. License: Creative Commons Attribution 4.0 International (CC BY 4.0), as stated on the PatentsView data download pages; the underlying patent records are United States government works. Required attribution (PatentsView's wording): "PatentsView, USPTO". Our attribution line is in section 5. Redistribution of the snapshot (gate 5.7) carries this file and the same line.

Table names are the API layout (`API/search_sqlite.py` naming: `<index>` and `<index>__<group>`); the Lapse catalog columns derived from the same files are listed at the end of the table.

| Snapshot table | Bulk file | License | Notes |
|---|---|---|---|
| patents | g_patent, g_patent_abstract, g_application (earliest application date), g_us_term_of_grant (term extension), g_gov_interest (statement); citation counts from g_us_patent_citation, g_us_application_citation, g_foreign_citation | CC BY 4.0 | withdrawn flag from g_patent |
| patents__application | g_application | CC BY 4.0 | |
| patents__applicants | g_applicant_not_disambiguated | CC BY 4.0 | names as printed on the grant |
| patents__assignees | g_assignee_disambiguated, g_location_disambiguated | CC BY 4.0 | disambiguated ids are PatentsView's |
| patents__attorneys | g_attorney_disambiguated | CC BY 4.0 | |
| patents__botanic | g_botanic | CC BY 4.0 | |
| patents__cpc_at_issue | g_cpc_at_issue, g_cpc_title | CC BY 4.0 | CPC scheme itself: published by the EPO and USPTO for public reproduction with the scheme notice |
| patents__cpc_current | g_cpc_current, g_cpc_title | CC BY 4.0 | |
| patents__examiners | g_examiner_not_disambiguated | CC BY 4.0 | |
| patents__figures | g_figures | CC BY 4.0 | |
| patents__foreign_priority | g_foreign_priority | CC BY 4.0 | |
| patents__gov_interest_contract_award_numbers | g_gov_interest_contracts | CC BY 4.0 | |
| patents__gov_interest_organizations | g_gov_interest_org | CC BY 4.0 | |
| patents__granted_pregrant_crosswalk | pg_granted_pgpubs_crosswalk | CC BY 4.0 | empty until the pre-grant set loads (1.5 deferred) |
| patents__inventors | g_inventor_disambiguated, g_location_disambiguated | CC BY 4.0 | |
| patents__ipcr | g_ipc_at_issue | CC BY 4.0 | IPC scheme: published by WIPO for public reproduction |
| patents__pct_data | g_pct_data | CC BY 4.0 | |
| patents__us_related_documents | g_us_rel_doc | CC BY 4.0 | |
| patents__us_term_of_grant | g_us_term_of_grant | CC BY 4.0 | |
| patents__uspc_at_issue | g_uspc_at_issue | CC BY 4.0 | USPC titles are not in any bulk file (table_map.md view 27); when loaded they come from the USPTO USPC schedule, a US government work |
| patents__wipo | g_wipo_technology | CC BY 4.0 | WIPO technology field names: WIPO IPC-technology concordance, reproduced with credit to WIPO |
| inventors, inventors__inventor_years | g_inventor_disambiguated, g_persistent_inventor, g_location_disambiguated, g_patent (derived counts and years) | CC BY 4.0 | |
| assignees, assignees__assignee_years | g_assignee_disambiguated, g_persistent_assignee, g_location_disambiguated, g_patent | CC BY 4.0 | |
| locations | g_location_disambiguated plus counts from g_inventor_disambiguated and g_assignee_disambiguated | CC BY 4.0 | |
| attorneys | g_attorney_disambiguated plus counts from g_patent, g_inventor_disambiguated, g_assignee_disambiguated | CC BY 4.0 | |
| us_patent_citations | g_us_patent_citation | CC BY 4.0 | |
| us_application_citations | g_us_application_citation | CC BY 4.0 | |
| foreign_citations | g_foreign_citation | CC BY 4.0 | |
| other_references | g_other_reference | CC BY 4.0 | reference text as printed on the grant |
| rel_app_texts | g_rel_app_text | CC BY 4.0 | |
| cpc_classes, cpc_subclasses, cpc_groups | g_cpc_title plus counts from g_cpc_current, g_patent, g_inventor_disambiguated, g_assignee_disambiguated | CC BY 4.0 | |
| uspc_mainclasses, uspc_subclasses | ids from g_uspc_at_issue; titles from the USPTO USPC schedule when loaded | CC BY 4.0; USPC schedule: US government work | |
| ipcr | distinct section, class, subclass from g_ipc_at_issue | CC BY 4.0 | |
| wipo | g_wipo_technology | CC BY 4.0 | |
| nber_categories, nber_subcategories | none (dead upstream routes, no data set; answer 501) | n/a | |
| g_claims | g_claims_YYYY (PVGPATTXT) | CC BY 4.0 | claim text as granted; loads with 1.6 |
| g_brf_sum_texts | g_brf_sum_text_YYYY (PVGPATTXT) | CC BY 4.0 | loads with 1.6 |
| g_detail_desc_texts | g_detail_desc_text_YYYY (PVGPATTXT) | CC BY 4.0 | 2005 on per the 1.6 decision |
| g_draw_desc_texts | g_draw_desc_text_YYYY (PVGPATTXT) | CC BY 4.0 | loads with 1.6 |
| publications and its groups, pg_claims, pg_brf_sum_texts, pg_detail_desc_texts, pg_draw_desc_texts, rel_app_texts (publication) | PVPGPUBDIS, PVPGPUBTXT | CC BY 4.0 | deferred at 1.5; the rows stay here so the register already covers the day they load |
| _lapse_build, _lapse_indices, _lapse_fields, _lapse_value_stats, fts_* | derived by our build from the tables above | CC BY 4.0 (derived data) | schema copied from es-data-load (GPL-3.0 code; field names are facts) |

Lapse catalog (`/data/lapse/snapshot_current.db`, the `catalog` table and its FTS index): id, title, abstract, dates, first inventor and assignee, CPC, claim 1, forward-citation aggregates, fee status and lapse date are derived from the same PatentsView files and from PTMNFEE2 (maintenance-fee events, USPTO Open Data Portal, US government work). Scores and flags (prior, sleeper, Too Good) are our own derived values and are internal (never-regress item 8).

Every table in `compat/table_map.md` (45 views, 20 patent groups, 13 publication groups, 2 entity year groups) is covered by the rows above.

## 3. Family sources (gate 1.9)

| Source | What we take | License | Attribution and limits |
|---|---|---|---|
| Google Patents Public Datasets on BigQuery (`patents-public-data.patents.publications`) | foreign family members of each US patent: office, number, kind | CC BY 4.0 | credit line: "Family data from Google Patents Public Datasets, used under CC BY 4.0." Query cost guarded by `maximum_bytes_billed` (gate 1.10). No legal-status fields are taken. |
| USPTO Global Dossier | one deep link per family, nothing copied | public US government site | link only |
| EPO Open Patent Services, Lens | nothing | not used (license terms forbid redistribution of legal status; decision in checklist 1.9) | never queried |

## 4. Rules citation quotes (Phase 11)

Each rules row carries a `citation_url` and a short `citation_quote` taken verbatim from the cited page (skill `rules-row`). The quotes are short excerpts reproduced for identification of the rule; the sources and their terms:

| Source | Used for | License or terms | Attribution line |
|---|---|---|---|
| United States Code and Code of Federal Regulations (law.cornell.edu, ecfr.gov, govinfo.gov) | US rows: 35 USC 41, 102, 119; 37 CFR 1.16, 1.20, 1.31, 1.33, 1.52, 1.366, 1.495, 1.705 | US government works, public domain; LII's page framing is not copied, only the statute or rule text | "United States Code and 37 CFR, via the Legal Information Institute and govinfo.gov." |
| Federal Register notices (govinfo.gov), USPTO bulletins (govdelivery.com), uspto.gov fee schedule and PPH pages | US rows: identity, disclosure, pph, fee | US government works, public domain | "USPTO notices and fee schedules, uspto.gov." |
| WIPO PCT Applicant's Guide, PCT Articles, Rules and fee tables (wipo.int) | PCT rows: fee, translation, window | WIPO publications are free to reproduce for non-commercial purposes with credit (wipo.int terms of use); short quotes for identification with a link to the source, the page itself never copied | "PCT texts and fee tables, WIPO (wipo.int)." |
| EPO, CNIPA, JPO, KIPO, IP Australia official pages (not yet seeded) | EP, CN, JP, KR, AU rows | added with their row at 11.2 after reading each site's terms; a source whose terms forbid quoting gets a link and a paraphrase in our words, no quote | added with the row |

## 5. Attribution lines

The footer of every page is generated from the bullets below, in this order. One sentence per bullet, plain English, no em-dashes.

- Patent data from PatentsView and the USPTO, used under CC BY 4.0. PatentRef is an independent service and is not affiliated with the USPTO.
- Family data, when shown, from Google Patents Public Datasets under CC BY 4.0. Rules cite their source page; the law and fee texts are the offices' own.
