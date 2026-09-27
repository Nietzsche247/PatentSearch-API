"""Re-derive es_query_from_real_parser for every example in contract_examples.json using the real
API/queryparser.py (stubbed Django/ES imports) and endpoint_contract.json. Reports mismatches;
--write rewrites the file. (The original generator embedded the example list in code; the list now lives
in contract_examples.json, so this version reads it from there.)"""
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPOS = _HERE if _os.path.isdir(_os.path.join(_HERE, "PatentSearch-API")) else _os.path.dirname(_HERE)

import json, os, sys, types

ROOT = _HERE
sys.path.insert(0, os.path.join(REPOS, "PatentSearch-API"))
for name in ["elasticsearch", "redis", "django", "django.conf"]:
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["django.conf"].settings = None
sys.modules["elasticsearch"].ApiError = Exception
sys.modules["elasticsearch"].TransportError = Exception
from API.queryparser import QueryParser  # noqa: E402

contract = json.load(open(os.path.join(ROOT, "endpoint_contract.json"), encoding="utf-8"))["endpoints"]
by_url = {e["url"]: e for e in contract.values() if e["kind"] == "list"}


def translate(url, q, o=None):
    e = by_url[url]
    o = o or {}
    if e["view"] == "PatentList" and o.get("exclude_withdrawn", True) and '{"withdrawn": true}' not in json.dumps(q):
        q = {"_and": [{"withdrawn": False}, q]}
    p = QueryParser()
    try:
        return p.generate_search_query(q, e["nested_paths"], list(e["fields"].keys()), e["keyword_field_translations"], o.get("pad_patent_id", False))
    except Exception as ex:
        return {"__error__": f"{type(ex).__name__}: {ex}"}


path = os.path.join(ROOT, "contract_examples.json")
doc = json.load(open(path, encoding="utf-8"))
bad = 0
for ex in doc["examples"]:
    if ex.get("q") is not None and ex["endpoint"] in by_url:
        new = translate(ex["endpoint"], ex["q"], ex.get("o"))
        if new != ex.get("es_query_from_real_parser"):
            bad += 1
            print("MISMATCH", ex["id"])
        ex["es_query_from_real_parser"] = new
print(len(doc["examples"]), "examples,", bad, "mismatches")
if "--write" in sys.argv:
    json.dump(doc, open(path, "w", encoding="utf-8"), indent=1)
