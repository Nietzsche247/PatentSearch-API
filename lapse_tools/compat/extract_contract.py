"""AST-based extractor: endpoint configs -> endpoint_contract.json (no Django needed)."""
import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
REPOS = _HERE if _os.path.isdir(_os.path.join(_HERE, "PatentSearch-API")) else _os.path.dirname(_HERE)

import ast, json, glob, os, re

ROOT = os.path.dirname(os.path.abspath(__file__))
API = os.path.join(REPOS, "PatentSearch-API", "API")
EP = os.path.join(API, "endpoints")

def lit(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return ast.unparse(node)

def field_spec(call, serializers_in_module):
    """Return dict describing a DRF field assignment RHS."""
    if not isinstance(call, ast.Call):
        return {"type": ast.unparse(call)}
    fn = ast.unparse(call.func)
    kw = {k.arg: k.value for k in call.keywords}
    spec = {}
    if fn == "generate_serializer":
        inner = ast.unparse(call.args[0])
        spec["type"] = inner.replace("serializers.", "")
        if spec["type"] == "ListField" and "child" in kw:
            child = ast.unparse(kw["child"].func) if isinstance(kw["child"], ast.Call) else ast.unparse(kw["child"])
            spec["type"] = "ListField"
            spec["child"] = child
        for k, v in kw.items():
            if k in ("max_length", "required", "read_only"):
                spec[k] = lit(v)
        spec.setdefault("required", False)  # generate_serializer default
    else:
        spec["type"] = fn.replace("serializers.", "")
        for k, v in kw.items():
            if k in ("max_length", "required", "read_only", "view_name", "many"):
                spec[k] = lit(v)
        if spec["type"] == "ListField" and "child" in kw:
            spec["child"] = ast.unparse(kw["child"].func) if isinstance(kw["child"], ast.Call) else ast.unparse(kw["child"])
    return spec

def parse_module(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    serializers = {}   # class name -> {field: spec}
    endpoints = {}     # endpoint class name -> attrs
    views = {}         # view class -> (bases, extra attrs)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            bases = [ast.unparse(b) for b in node.bases]
            if "PVAPIDocumentSerializer" in bases:
                fields = {}
                for st in node.body:
                    if isinstance(st, ast.Assign) and len(st.targets) == 1:
                        fields[st.targets[0].id] = field_spec(st.value, serializers)
                serializers[node.name] = fields
            elif node.name.endswith("Endpoint") and not bases:
                attrs = {}
                for st in ast.walk(node):
                    if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Attribute):
                        name = st.targets[0].attr
                        if name == "field_list":
                            m = re.search(r"(\w+)\.__dict__", ast.unparse(st.value))
                            if m:
                                attrs["serializer"] = m.group(1)
                            else:
                                attrs["explicit_field_list"] = lit(st.value)
                        elif name == "response_encoder":
                            attrs["response_encoder"] = ast.unparse(st.value)
                        else:
                            attrs[name] = lit(st.value)
                endpoints[node.name] = attrs
            elif any(b in ("PVAPIListView", "PVAPIDetailView") for b in bases):
                extra = {}
                for st in ast.walk(node):
                    if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Attribute):
                        extra[st.targets[0].attr] = lit(st.value)
                views[node.name] = {"bases": bases, **extra}
            elif "APIResponseDocument" in bases:
                # capture response key: the attribute assigned in __init__ (self.<key> = ...)
                for st in ast.walk(node):
                    if isinstance(st, ast.Assign) and isinstance(st.targets[0], ast.Attribute) and st.targets[0].value.id == "self":
                        serializers.setdefault("__respkeys__", {})[node.name] = st.targets[0].attr
    return serializers, endpoints, views

# url routes
urls_src = open(os.path.join(API, "urls.py"), encoding="utf-8").read()
routes = {}
for m in re.finditer(r'(re_path|path)\(\s*"([^"]+)"\s*,\s*(\w+)\.as_view\(\)', urls_src):
    kind, pat, view = m.groups()
    routes[view] = "/api/v1/" + pat.replace("/?$", "/")

contract = {}
all_serializers = {"YearSerializer": {"year": {"type": "IntegerField", "required": False}, "num_patents": {"type": "IntegerField", "required": False}}}  # API/serializers/reusable_serializers.py
for path in sorted(glob.glob(os.path.join(EP, "*_endpoint_configuration.py"))):
    sers, eps, views = parse_module(path)
    respkeys = sers.pop("__respkeys__", {})
    all_serializers.update(sers)
    for vname, v in views.items():
        epcls = [b for b in v["bases"] if b.endswith("Endpoint")][0]
        ep = eps[epcls]
        ser = ep.get("serializer")
        if ser is None:
            # explicit field_list: pick the serializer in this module with max overlap
            fl = set(ep["explicit_field_list"])
            ser = max(sers, key=lambda n: len(fl & set(sers[n])))
            fields = {k: sp for k, sp in sers[ser].items() if k in fl}
            # keep IDHyperlinker fields that response_remapper produces
            for k, sp in sers[ser].items():
                if sp.get("type") == "IDHyperlinker": fields[k] = sp
        else:
            fields = sers[ser]
        top_fields = {}
        for fname, spec in fields.items():
            if spec.get("type") == "ListField" and spec.get("child") in all_serializers:
                top_fields[fname] = {"type": "nested_list", "child_serializer": spec["child"],
                                     "fields": all_serializers[spec["child"]]}
            else:
                top_fields[fname] = spec
        # sortable: any top-level field that is not nested (validate_request_parameters requires s field in field_list exactly, no dots)
        sortable = [f for f, sp in top_fields.items() if sp.get("type") != "nested_list" and sp.get("type") != "IDHyperlinker"]
        entry = {
            "view": vname,
            "kind": "detail" if "PVAPIDetailView" in v["bases"] else "list",
            "url": routes.get(vname),
            "es_index": ep.get("index"),
            "response_key": respkeys.get(ep.get("response_encoder"), ep.get("index")),
            "serializer": ser,
            "default_f": ep.get("f") if isinstance(ep.get("f"), list) else list(fields.keys()),
            "default_f_is_all_fields": not isinstance(ep.get("f"), list),
            "default_s": ep.get("s"),
            "default_s_effective_INFERRED": [{list(d.keys())[0]: list(d.values())[0]} for d in ep.get("s", [])],
            "default_o": ep.get("o", {}),
            "nested_paths": ep.get("nested_paths", {}),
            "keyword_field_translations": ep.get("keyword_field_translations", []),
            "operator_translations_UNUSED": ep.get("operator_translations", {}),
            "response_remapper": ep.get("response_remapper", []),
            "pk_field": v.get("pk_field"),
            "lookup_no_q_required": v.get("lookup", ep.get("lookup", False)),
            "explicit_field_list": ep.get("explicit_field_list"),
            "sortable_fields": sortable,
            "fields": top_fields,
            "source_file": os.path.relpath(path, ROOT),
        }
        contract[vname] = entry

json.dump({"routes_from_urls_py": routes, "endpoints": contract}, open(os.path.join(ROOT, "endpoint_contract.json"), "w"), indent=1)
print(len(contract), "views")
for k, e in contract.items():
    print(f'{str(e["url"]):55s} idx={str(e["es_index"]):28s} key={str(e["response_key"]):28s} nfields={len(e["fields"])} nested={len(e["nested_paths"])} pk={e["pk_field"]}')
