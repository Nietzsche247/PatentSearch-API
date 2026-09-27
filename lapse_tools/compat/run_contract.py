"""Contract runner for the Lapse SQLite backend of PatentSearch-API.

  C:\\LapseAPI\\venv\\Scripts\\python.exe run_contract.py [--base http://127.0.0.1:8765] [--key-file ...]

Fires every request in contract_examples.json that targets an implemented endpoint and checks: HTTP status,
response key, count/total_hits consistency, returned field set vs requested (or default) f, sort order,
and after-cursor paging (page 2 from the last sort values of page 1). Then runs extra checks (auth, error
strings, size cap, a 3,000-patent cursor walk against the database) and reports latency.
Writes contract_results.md next to this file.
"""
import argparse
import json
import sqlite3
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPLEMENTED = ["/api/v1/patent/us_patent_citation/", "/api/v1/patent/", "/api/v1/inventor/", "/api/v1/assignee/",
               "/api/v1/location/"]
NOT_IMPLEMENTED_PREFIX = ["/api/v1/patent/attorney/", "/api/v1/patent/us_application_citation/",
                          "/api/v1/patent/foreign_citation/", "/api/v1/patent/other_reference/",
                          "/api/v1/patent/rel_app_text/"]
REMAPPED = {"patents": {"assignees": ["assignee"], "inventors": ["inventor"], "wipo": ["wipo_field"],
                        "uspc_at_issue": ["uspc_mainclass", "uspc_subclass"],
                        "cpc_current": ["cpc_class", "cpc_subclass", "cpc_group"]}}
LAT = []


def call(base, key, method, path, params=None, body=None, raw_body=None):
    url = base + path
    headers = {"Accept": "application/json", "User-Agent": "lapse-contract-runner"}
    if key:
        headers["X-Api-Key"] = key
    data = None
    if method == "GET" and params:
        url += "?" + urllib.parse.urlencode({k: (v if isinstance(v, str) else json.dumps(v)) for k, v in params.items()})
    if method == "POST":
        headers["Content-Type"] = "application/json"
        data = raw_body.encode() if raw_body is not None else json.dumps(body or {}).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, hdrs, text = r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        status, hdrs, text = e.code, dict(e.headers), e.read().decode(errors="replace")
    ms = (time.perf_counter() - t0) * 1000
    try:
        js = json.loads(text)
    except ValueError:
        js = None
    return status, hdrs, js, ms


def implemented(endpoint):
    if any(endpoint.startswith(p) for p in NOT_IMPLEMENTED_PREFIX):
        return False
    return any(endpoint.startswith(p) for p in IMPLEMENTED)


def cmp_vals(a, b):
    if a == b:
        return 0
    try:
        return -1 if a < b else 1
    except TypeError:
        return -1 if str(a) < str(b) else 1


def sort_violation(records, sort):
    """Return None if records honor sort (missing values last, ES semantics) else a description."""
    fields = [(list(s.keys())[0], list(s.values())[0]) for s in sort]
    for i in range(1, len(records)):
        a, b = records[i - 1], records[i]
        for f, d in fields:
            va, vb = a.get(f), b.get(f)
            if va == vb:
                continue
            if va is None:
                return f"row {i}: missing {f} sorted before a present value"
            if vb is None:
                break
            c = cmp_vals(va, vb)
            if (d == "asc" and c > 0) or (d == "desc" and c < 0):
                return f"row {i}: {f} {va!r} then {vb!r} violates {d}"
            break
    return None


def build_request(ex, o_override=None):
    parts = {}
    if ex.get("q") is not None:
        parts["q"] = ex["q"]
    for k in ("f", "s", "o"):
        if ex.get(k) is not None:
            parts[k] = ex[k]
    if o_override is not None:
        parts["o"] = o_override
    return parts


def send(base, key, ex, o_override=None):
    parts = build_request(ex, o_override)
    if ex["method"] == "POST":
        return call(base, key, "POST", ex["endpoint"], body=parts)
    return call(base, key, "GET", ex["endpoint"], params=parts)


def expected_status(ex):
    if "DOC_BUG" in ex["id"]:
        return 500, "ERR_ES"
    return 200, None


DEFAULT_F = {  # upstream endpoint defaults, used for detail views (examples carry effective_f for list views)
    "/api/v1/patent/us_patent_citation/": ["patent_id", "citation_patent_id", "citation_date"],
    "/api/v1/patent/": ["patent_id", "patent_title", "patent_date"],
    "/api/v1/inventor/": ["inventor_id", "inventor_name_first", "inventor_name_last"],
    "/api/v1/assignee/": ["assignee_id", "assignee_individual_name_first", "assignee_individual_name_last",
                          "assignee_organization"],
    "/api/v1/location/": ["location_id", "location_name", "location_state", "location_country"],
}


def default_f(endpoint):
    for prefix, f in DEFAULT_F.items():
        if endpoint.startswith(prefix):
            return f
    return []


def check_fields(ex, rec, rkey):
    eff = ex.get("effective_f") or ex.get("f") or default_f(ex["endpoint"])
    top = {f.split(".", 1)[0] for f in eff}
    extra = set(rec) - top
    if extra:
        return f"unexpected fields {sorted(extra)}"
    groups = {f.split(".", 1)[0] for f in eff if "." in f} | {f for f in eff if f in REMAPPED.get(rkey, {})}
    missing = [f for f in top if f not in rec and f not in groups]
    if missing:
        return f"missing fields {sorted(missing)}"
    for g in groups:
        if g in eff or g not in rec:
            continue
        subs = {f.split(".", 1)[1] for f in eff if f.startswith(g + ".")} | set(REMAPPED.get(rkey, {}).get(g, []))
        for obj in rec[g]:
            bad = set(obj) - subs
            if bad:
                return f"nested {g} has unrequested {sorted(bad)}"
    return None


def run_example(base, key, ex):
    reasons, notes = [], []
    status, hdrs, js, ms = send(base, key, ex)
    LAT.append(ms)
    want, want_code = expected_status(ex)
    if status != want:
        reasons.append(f"status {status} (want {want}; X-Status-Reason={hdrs.get('X-Status-Reason')})")
        return reasons, notes, None
    if want != 200:
        if want_code and hdrs.get("X-Status-Reason-Code") != want_code:
            reasons.append(f"X-Status-Reason-Code {hdrs.get('X-Status-Reason-Code')} (want {want_code})")
        notes.append(f"{status} {hdrs.get('X-Status-Reason-Code')} as upstream")
        return reasons, notes, None
    is_detail = ex["endpoint"].rstrip("/").split("/")[-1] not in ("patent", "inventor", "assignee", "location",
                                                                   "us_patent_citation")
    rkey = ex.get("response_key") or ("patents" if "/patent/" in ex["endpoint"] else None)
    if js is None or js.get("error") is not False:
        reasons.append("body is not a success document")
        return reasons, notes, None
    if rkey not in js:
        reasons.append(f"response key {rkey} missing (keys {sorted(js)})")
        return reasons, notes, None
    recs = js[rkey]
    size = ex.get("effective_size", 100)
    if js.get("count") != len(recs):
        reasons.append(f"count {js.get('count')} != len {len(recs)}")
    if js.get("total_hits", -1) < js.get("count", 0):
        reasons.append("total_hits < count")
    if not is_detail and js.get("total_hits", 0) > len(recs) and len(recs) != size:
        reasons.append(f"short page: {len(recs)} of size {size} with total_hits {js['total_hits']}")
    if is_detail and js.get("total_hits") != 1:
        notes.append(f"detail total_hits={js.get('total_hits')}")
    for r in recs:
        why = check_fields(ex, r, rkey)
        if why:
            reasons.append(why)
            break
    sort = ex.get("effective_s") or []
    returned = all(list(s)[0] in (recs[0] if recs else {}) for s in sort)
    if recs and sort and returned:
        v = sort_violation(recs, sort)
        if v:
            reasons.append("sort: " + v)
    elif recs and sort:
        notes.append("sort fields not in f; order not checked")
    if ex.get("expected_response") is not None and js != ex["expected_response"]:
        reasons.append("differs from documented expected_response")
    # after-cursor paging
    if not is_detail and recs and sort and returned and js["total_hits"] > len(recs):
        o = dict(ex.get("o") or {})
        o["after"] = [recs[-1].get(list(s)[0]) for s in sort]
        s2, h2, j2, ms2 = send(base, key, ex, o_override=o)
        LAT.append(ms2)
        if s2 != 200 or not j2 or rkey not in j2:
            reasons.append(f"page2 status {s2} {h2.get('X-Status-Reason')}")
        else:
            p2 = j2[rkey]
            idf = list(sort[-1])[0]
            ids1 = {json.dumps([r.get(list(s)[0]) for s in sort]) for r in recs}
            dup = [r for r in p2 if json.dumps([r.get(list(s)[0]) for s in sort]) in ids1]
            if dup:
                reasons.append(f"page2 repeats {len(dup)} rows of page1")
            v = sort_violation([recs[-1]] + p2[:1], sort) if p2 else None
            if v:
                reasons.append("page2 boundary: " + v)
            if j2["total_hits"] != js["total_hits"]:
                reasons.append("page2 total_hits differs")
            notes.append(f"page2 ok ({len(p2)} rows)" if not dup and not v else "")
    notes.append(f"total_hits={js.get('total_hits')} count={js.get('count')}")
    return reasons, notes, js


def extra_checks(base, key, db_path):
    out = []

    def rec(name, ok, detail):
        out.append((name, "pass" if ok else "FAIL", detail))

    P = "/api/v1/patent/"
    s, h, j, _ = call(base, None, "GET", P, params={"q": {"patent_id": "7861317"}})
    rec("missing X-Api-Key -> 403", s == 403, f"status {s}")
    s, h, j, _ = call(base, "bogus.key", "GET", P, params={"q": {"patent_id": "7861317"}})
    rec("invalid X-Api-Key -> 403", s == 403, f"status {s}")
    cases = [
        ("bad JSON in q", {"q": "{patent_id: 1"}, 400, "Invalid JSON in 'q' parameter"),
        ("bad JSON in f", {"q": {"patent_id": "1"}, "f": "[x"}, 400, "Invalid JSON in 'f' parameter"),
        ("missing q", {}, 400, "Query String is missing"),
        ("two-key q", {"q": {"a": 1, "b": 2}}, 400, "Query string should have only one 'key-value' pair"),
        ("invalid field", {"q": {"nope": 1}}, 400, "Invalid field: nope"),
        ("non-nested dotted field", {"q": {"patent_title.x": 1}}, 400, "Invalid field: patent_title.x. patent_title is not a nested field"),
        ("offset option", {"q": {"patent_id": "1"}, "o": {"offset": 10}}, 400, "'offset' has been replaces with 'after' parameter."),
        ("after/sort length", {"q": {"patent_id": "1"}, "o": {"after": ["1", "2"]}}, 400, "Sort option had 1 elements but 'after' had 2"),
        ("bad pad_patent_id", {"q": {"patent_id": "1"}, "o": {"pad_patent_id": "maybe"}}, 400, "'pad_patent_id' option provided with non-boolean value: maybe"),
        ("bad date value", {"q": {"_gte": {"patent_date": "2020-13-45"}}}, 400, "Invalid date supplied in query"),
        ("bad number value", {"q": {"patent_year": "abc"}}, 400, 'Invalid number supplied For input string: "abc"'),
        ("list where dict expected", {"q": {"_and": [[{"patent_id": "1"}]]}}, 400, "Invalid API Query Syntax (JSON rules not violated)"),
        ("sort on text field", {"q": {"patent_id": "1"}, "s": [{"patent_title": "asc"}]}, 400, "Internal Server Error"),
    ]
    for name, params, want, reason in cases:
        s, h, j, _ = call(base, key, "GET", P, params=params)
        ok = s == want and h.get("X-Status-Reason") == reason and j == {"error": True}
        rec(name, ok, f"status {s}, X-Status-Reason={h.get('X-Status-Reason')!r}, code={h.get('X-Status-Reason-Code')}")
    s, h, j, _ = call(base, key, "POST", P, raw_body=json.dumps(json.dumps({"q": {"patent_id": "1"}})))
    rec("POST with JSON string body", s == 400 and (h.get("X-Status-Reason") or "").startswith("POST method expects JSON objects"),
        f"status {s}, {h.get('X-Status-Reason')!r}")
    s, h, j, _ = call(base, key, "GET", "/api/v1/patent/NOTAPATENT/")
    rec("detail miss -> 404", s == 404, f"status {s}")
    s, h, j, _ = call(base, key, "GET", P, params={"q": {"_gte": {"patent_date": "1976-01-01"}}, "o": {"size": 1001}})
    rec("o.size 1001 capped at 1000", s == 200 and j["count"] == 1000 and j["total_hits"] > 1000,
        f"status {s}, count {j and j.get('count')}, total_hits {j and j.get('total_hits')}")
    # cursor walk of 3,000 patents by patent_id versus the database
    con = sqlite3.connect("file:" + str(db_path).replace("\\", "/") + "?mode=ro", uri=True)
    truth = [r[0] for r in con.execute("SELECT patent_id FROM patents WHERE withdrawn=0 ORDER BY patent_id LIMIT 3000")]
    got, after, pages = [], None, 0
    while len(got) < 3000:
        o = {"size": 1000}
        if after is not None:
            o["after"] = [after]
        s, h, j, ms = call(base, key, "GET", P, params={"q": {"_gte": {"patent_date": "1900-01-01"}}, "f": ["patent_id"],
                                                       "s": [{"patent_id": "asc"}], "o": o})
        LAT.append(ms)
        pages += 1
        if s != 200 or not j["patents"]:
            break
        got += [r["patent_id"] for r in j["patents"]]
        after = j["patents"][-1]["patent_id"]
    dups = len(got) - len(set(got))
    rec("cursor walk 3,000 patents (3 pages of 1000)", got[:3000] == truth and dups == 0,
        f"pages {pages}, rows {len(got)}, duplicates {dups}, matches DB order: {got[:3000] == truth}")
    return out


def pct(vals, p):
    vals = sorted(vals)
    if not vals:
        return 0.0
    k = (len(vals) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--key-file", default=r"C:\LapseAPI\data\test_api_key.txt")
    ap.add_argument("--db", default=r"C:\LapseAPI\data\sample.db")
    ap.add_argument("--latency-rounds", type=int, default=5)
    a = ap.parse_args()
    key = Path(a.key_file).read_text().strip()
    examples = json.load(open(HERE / "contract_examples.json", encoding="utf-8"))["examples"]
    rows, npass, napp = [], 0, 0
    for ex in examples:
        if not implemented(ex["endpoint"]):
            rows.append((ex["id"], ex["endpoint"], "n/a", "endpoint not implemented in this prototype"))
            continue
        napp += 1
        try:
            reasons, notes, _ = run_example(a.base, key, ex)
        except Exception as e:  # noqa: BLE001
            reasons, notes = [f"runner exception {type(e).__name__}: {e}"], []
        ok = not reasons
        npass += ok
        rows.append((ex["id"], ex["endpoint"], "pass" if ok else "FAIL",
                     "; ".join(reasons) if reasons else "; ".join(n for n in notes if n)))
    # latency: repeat the applicable examples
    lat, per = [], {}
    for _ in range(a.latency_rounds):
        for ex in examples:
            if implemented(ex["endpoint"]):
                ms = send(a.base, key, ex)[3]
                lat.append(ms)
                per.setdefault(ex["id"], []).append(ms)
    slow = sorted(((statistics.median(v), k) for k, v in per.items()), reverse=True)[:5]
    extras = extra_checks(a.base, key, a.db)
    xpass = sum(1 for e in extras if e[1] == "pass")
    lines = ["# Lapse SQLite backend: contract results", "",
             f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')} against {a.base} (database {a.db})", "",
             f"**Summary: {npass} pass / {napp} applicable examples ({len(examples) - napp} not applicable); "
             f"extra checks {xpass} / {len(extras)} pass; latency over {len(lat)} requests "
             f"p50 {pct(lat, 0.5):.1f} ms, p95 {pct(lat, 0.95):.1f} ms, max {max(lat or [0]):.1f} ms**", "",
             "## Examples", "", "| example id | endpoint | result | reason / notes |", "|---|---|---|---|"]
    for r in rows:
        lines.append("| " + " | ".join(str(x).replace("|", "/") for x in r) + " |")
    lines += ["", "## Extra checks", "", "| check | result | detail |", "|---|---|---|"]
    for e in extras:
        lines.append("| " + " | ".join(str(x).replace("|", "/") for x in e) + " |")
    lines += ["", "## Latency", "",
              f"{len(lat)} example requests ({a.latency_rounds} rounds): p50 {pct(lat, 0.5):.1f} ms, "
              f"p95 {pct(lat, 0.95):.1f} ms, mean {statistics.mean(lat or [0]):.1f} ms, max {max(lat or [0]):.1f} ms.",
              "", "Slowest examples (median ms): " + ", ".join(f"{k} {m:.0f}" for m, k in slow), ""]
    (HERE / "contract_results.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
