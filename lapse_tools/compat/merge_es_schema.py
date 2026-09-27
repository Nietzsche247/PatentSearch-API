"""Merge ES schema types (es-data-load) and MySQL source tables (mappings) into endpoint_contract.json."""
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPOS = _HERE if _os.path.isdir(_os.path.join(_HERE, "PatentSearch-API")) else _os.path.dirname(_HERE)

import json, glob, os, re

ROOT = os.path.dirname(os.path.abspath(__file__))
SCH = os.path.join(REPOS, "es-data-load", "src", "es_data_load", "pv", "schemas")
MAP = os.path.join(REPOS, "es-data-load", "src", "es_data_load", "pv", "mappings", "production")

INDEX_BY_SCHEMA = {
    ("granted", "assignees_fields.json"): "assignees", ("granted", "attorneys_fields.json"): "attorneys",
    ("granted", "cpc_classes_fields.json"): "cpc_classes", ("granted", "cpc_groups_fields.json"): "cpc_groups",
    ("granted", "cpc_subclasses_fields.json"): "cpc_subclasses", ("granted", "foreign_citations_fields.json"): "foreign_citations",
    ("granted", "inventors_fields.json"): "inventors", ("granted", "ipcr_fields.json"): "ipcr",
    ("granted", "locations_fields.json"): "locations", ("granted", "other_references_fields.json"): "other_references",
    ("granted", "patents_fields.json"): "patents", ("granted", "rel_app_text_fields.json"): "rel_app_text",
    ("granted", "us_application_citations_fields.json"): "us_application_citations",
    ("granted", "us_patent_citations_fields.json"): "us_patent_citations",
    ("granted", "uspc_mainclasses_fields.json"): "uspc_mainclasses", ("granted", "uspc_subclasses_fields.json"): "uspc_subclasses",
    ("granted", "wipo_fields.json"): "wipo", ("granted", "brf_sum_text.json"): "g_brf_sum_texts",
    ("granted", "claim.json"): "g_claims", ("granted", "detail_desc_text.json"): "g_detail_desc_texts",
    ("granted", "draw_desc_text.json"): "g_draw_desc_texts",
    ("pregrant", "publication_fields.json"): "publications", ("pregrant", "rel_app_text_publications_fields.json"): "rel_app_text_publications",
    ("pregrant", "brf_sum_text.json"): "pg_brf_sum_texts", ("pregrant", "claim.json"): "pg_claims",
    ("pregrant", "detail_desc_text.json"): "pg_detail_desc_texts", ("pregrant", "draw_desc_text.json"): "pg_draw_desc_texts",
}

es_schema = {}
for (kind, fname), idx in INDEX_BY_SCHEMA.items():
    p = os.path.join(SCH, kind, fname)
    if not os.path.exists(p):
        continue
    props = json.load(open(p, encoding="utf-8"))
    flat = {}
    def walk(p, prefix=""):
        for k, v in p.items():
            if "properties" in v:
                flat[prefix + k] = {"type": v.get("type", "object")}
                walk(v["properties"], prefix + k + ".")
            else:
                flat[prefix + k] = {"type": v.get("type"), "keyword_subfield": "keyword" in v.get("fields", {}),
                                    **({"format": v["format"]} if "format" in v else {})}
    walk(props)
    es_schema[idx] = {"schema_file": os.path.relpath(os.path.join(SCH, kind, fname), ROOT), "fields": flat}

# MySQL sources
mysql = {}
for mp in glob.glob(os.path.join(MAP, "*", "*.json")):
    m = json.load(open(mp, encoding="utf-8"))
    idx = m["target_setting"]["index"]
    src = m["source_setting"]
    tables = sorted(set(re.findall(r"\{elastic_production_source\}\.(\w+)", src.get("source", ""))))
    nested = {}
    for k, v in src.get("nested_fields", {}).items():
        nested[k] = sorted(set(re.findall(r"\{elastic_production_source\}\.(\w+)", v["source"])))
    mysql[idx] = {"mapping_file": os.path.relpath(mp, ROOT), "main_tables": tables, "key_field": src.get("key_field"),
                  "id_field": m["target_setting"]["id_field"], "indexing_batch_size": m["target_setting"]["indexing_batch_size"],
                  "chunksize": src.get("chunksize"), "nested_tables": nested, "main_sql": src.get("source")}

c = json.load(open(os.path.join(ROOT, "endpoint_contract.json"), encoding="utf-8"))
missing = []
for vname, e in c["endpoints"].items():
    idx = e["es_index"]
    es = es_schema.get(idx, {}).get("fields", {})
    e["es_schema_file"] = es_schema.get(idx, {}).get("schema_file")
    e["mysql_source"] = {k: v for k, v in mysql.get(idx, {}).items() if k != "main_sql"}
    text_searchable, sortable_ok, sortable_bad = [], [], []
    def annotate(fname, spec, prefix=""):
        full = prefix + fname
        est = es.get(full)
        if est is None:
            spec["es_type"] = None
            if spec.get("type") != "IDHyperlinker":
                missing.append((idx, full))
        else:
            spec["es_type"] = est["type"]
            if est.get("keyword_subfield"): spec["es_keyword_subfield"] = True
            if est["type"] == "text": text_searchable.append(full)
    for fname, spec in e["fields"].items():
        annotate(fname, spec)
        if spec.get("type") == "nested_list":
            for cf, cs in spec["fields"].items():
                annotate(cf, cs, prefix=fname + ".")
    for f in e["sortable_fields"]:
        sp = e["fields"][f]
        if sp.get("es_type") == "text" and f not in e["keyword_field_translations"]:
            sortable_bad.append(f)
        elif sp.get("es_type") is None:
            sortable_bad.append(f)
        else:
            sortable_ok.append(f)
    e["text_searchable_fields"] = text_searchable
    e["sortable_fields"] = sortable_ok
    e["sortable_by_validation_but_es_would_reject_INFERRED"] = sortable_bad
c["es_indices"] = es_schema
c["mysql_sources"] = mysql
c["_notes"] = {
    "sortable_fields": "s accepts any top-level field in field_list (validate_request_parameters); ES rejects sort on text without .keyword. keyword_field_translations get .keyword appended.",
    "text_searchable_fields": "fields typed text in ES schema; _text_all/_text_any/_text_phrase target these. On keyword fields match still works but as exact-term match.",
    "es_type": "from es-data-load schemas; None means the API field name has no ES field (query on it -> 0 results or ES error).",
}
json.dump(c, open(os.path.join(ROOT, "endpoint_contract.json"), "w", encoding="utf-8"), indent=1)
print("API fields with no ES field:", missing)
for idx, s in es_schema.items():
    t = [k for k, v in s["fields"].items() if v["type"] == "text"]
    print(idx, len(s["fields"]), "fields;", len(t), "text:", t[:8])
