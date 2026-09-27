# Lapse SQLite backend: contract results

Run: 2026-09-26 20:28:32 against http://127.0.0.1:8765 (database C:\LapseAPI\data\sample.db)

**Summary: 28 pass / 28 applicable examples (3 not applicable); extra checks 19 / 19 pass; latency over 140 requests p50 53.8 ms, p95 383.1 ms, max 701.6 ms**

## Examples

| example id | endpoint | result | reason / notes |
|---|---|---|---|
| ref_eq_single | /api/v1/patent/ | pass | total_hits=1 count=1 |
| ref_gte_date | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=63013 count=100 |
| ref_not | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=91458 count=100 |
| ref_value_array_nested | /api/v1/patent/ | pass | page2 ok (6 rows); total_hits=106 count=100 |
| ref_and | /api/v1/patent/ | pass | total_hits=0 count=0 |
| ref_complex_1 | /api/v1/patent/ | pass | total_hits=5 count=5 |
| ref_or_text_any | /api/v1/patent/ | pass | page2 ok (25 rows); total_hits=125 count=100 |
| ref_nested_and_or_phrase | /api/v1/patent/ | pass | total_hits=0 count=0 |
| ref_sort_multi | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=63013 count=100 |
| ref_after_cursor | /api/v1/g_claim/ | n/a | endpoint not implemented in this prototype |
| ex1_id_list | /api/v1/patent/ | pass | total_hits=3 count=3 |
| ex2_date_range_post | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=4150 count=100 |
| ex3_text_all_abstract | /api/v1/patent/ | pass | page2 ok (50 rows); total_hits=282 count=50 |
| ex4_DOC_BUG_field_outside_operator | /api/v1/patent/ | pass | 500 ERR_ES as upstream |
| ex5_cpc_section | /api/v1/patent/ | pass | page2 ok (50 rows); total_hits=29363 count=50 |
| ex6_pagination_page2 | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=90845 count=100 |
| test_neq_list | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=382 count=100 |
| test_begins_nested | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=488 count=100 |
| test_contains_nested | /api/v1/patent/ | pass | total_hits=26 count=26 |
| test_text_all_title | /api/v1/patent/ | pass | total_hits=13 count=13 |
| test_text_phrase_title | /api/v1/patent/ | pass | total_hits=14 count=14 |
| test_keyword_exact_org | /api/v1/patent/ | pass | total_hits=49 count=49 |
| test_keyword_text_any_org | /api/v1/patent/ | pass | total_hits=81 count=81 |
| test_mix_text_non_text | /api/v1/patent/ | pass | total_hits=6 count=6 |
| test_pub_and_nested | /api/v1/publication/ | n/a | endpoint not implemented in this prototype |
| test_pub_text_all | /api/v1/publication/ | n/a | endpoint not implemented in this prototype |
| nb_patent_year_with_inventors_group | /api/v1/patent/ | pass | page2 ok (3 rows); total_hits=3864 count=3 |
| nb_detail_get | /api/v1/patent/D345393/ | pass | total_hits=1 count=1 |
| nb_inventor_two_names | /api/v1/inventor/ | pass | total_hits=1 count=1 |
| patent_withdrawn_override | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=252 count=100 |
| patent_pad_patent_id | /api/v1/patent/ | pass | page2 ok (100 rows); total_hits=1762 count=100 |

## Extra checks

| check | result | detail |
|---|---|---|
| missing X-Api-Key -> 403 | pass | status 403 |
| invalid X-Api-Key -> 403 | pass | status 403 |
| bad JSON in q | pass | status 400, X-Status-Reason="Invalid JSON in 'q' parameter", code=ERR_Q |
| bad JSON in f | pass | status 400, X-Status-Reason="Invalid JSON in 'f' parameter", code=ERR_Q |
| missing q | pass | status 400, X-Status-Reason='Query String is missing', code=ERR_Q |
| two-key q | pass | status 400, X-Status-Reason="Query string should have only one 'key-value' pair", code=ERR_Q |
| invalid field | pass | status 400, X-Status-Reason='Invalid field: nope', code=ERR_Q |
| non-nested dotted field | pass | status 400, X-Status-Reason='Invalid field: patent_title.x. patent_title is not a nested field', code=ERR_Q |
| offset option | pass | status 400, X-Status-Reason="'offset' has been replaces with 'after' parameter.", code=ERR_Q |
| after/sort length | pass | status 400, X-Status-Reason="Sort option had 1 elements but 'after' had 2", code=ERR_Q |
| bad pad_patent_id | pass | status 400, X-Status-Reason="'pad_patent_id' option provided with non-boolean value: maybe", code=ERR_Q |
| bad date value | pass | status 400, X-Status-Reason='Invalid date supplied in query', code=ERR_Q |
| bad number value | pass | status 400, X-Status-Reason='Invalid number supplied For input string: "abc"', code=ERR_Q |
| list where dict expected | pass | status 400, X-Status-Reason='Invalid API Query Syntax (JSON rules not violated)', code=ERR_Q |
| sort on text field | pass | status 400, X-Status-Reason='Internal Server Error', code=ERR_Q |
| POST with JSON string body | pass | status 400, 'POST method expects JSON objects in the body. Found string representation of JSON instead ' |
| detail miss -> 404 | pass | status 404 |
| o.size 1001 capped at 1000 | pass | status 200, count 1000, total_hits 100665 |
| cursor walk 3,000 patents (3 pages of 1000) | pass | pages 3, rows 3000, duplicates 0, matches DB order: True |

## Latency

140 example requests (5 rounds): p50 53.8 ms, p95 383.1 ms, mean 111.7 ms, max 701.6 ms.

Slowest examples (median ms): ex5_cpc_section 646, test_contains_nested 381, ex6_pagination_page2 250, test_begins_nested 242, test_neq_list 225
