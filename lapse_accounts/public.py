"""Agent plumbing at the site root and the build facts endpoint (PatentRef gate 2.11).

  GET /llms.txt, /llms-full.txt, /openapi.json, /LICENSES.md
      Static files served from the patentref repo clone on the box (LAPSE_PUBLIC_DIR, default
      /srv/lapse/public, and LAPSE_LICENSES_PATH, default /srv/lapse/LICENSES.md). The source of
      truth is that repo (public/ and tools/gen_openapi.py); this app only serves the files, so no
      Caddy change and no second copy. 404 when a file is absent. No key needed.
  GET /docs/errors/ and /docs/errors/<CODE>
      The RFC 9457 problem "type" targets: one plain-text explanation per X-Status-Reason-Code.
  GET /api/v1/meta/
      Build facts of the snapshot in service: data_version (one per build), as_of (the newest
      release date among the build inputs), refreshed_at (when the build went into service),
      built_at, snapshot_sha256, previous_version, superseded_versions, the table delta the build
      recorded (counts current against next) and the fee value delta, attribution and the license
      register URL. Read from the served file's _lapse_build rows and the runner's sidecars
      (/data/api/snapshot_current.json, /data/lapse/refresh_versions.json). No key needed.

The page footers of the sign-up and status pages are generated from LICENSES.md (checklist 1.11):
`footer_lines()` returns the lines of its "Attribution lines" section ("## 5. Attribution lines" in the
register; the number is optional), with a fixed fallback when the
file is not on the box, so the attribution shown is always the register's.
"""
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET

PUBLIC_FILES = {
    "llms.txt": "text/plain; charset=utf-8",
    "llms-full.txt": "text/plain; charset=utf-8",
    "openapi.json": "application/json",
}
LICENSES_NAME = "LICENSES.md"
CACHE = "public, max-age=300"
FALLBACK_FOOTER = ["Patent data from PatentsView and the USPTO, used under CC BY 4.0. PatentRef is an independent service and is not affiliated with the USPTO."]

ERROR_CODES = {
    "ERR_Q": "400 Bad Request. The q, f, s or o parameter is invalid: malformed JSON, more than one top-level key in q, an unknown field, a field "
             "outside the endpoint, a sort on a text field, 'offset' in o, a query over the cost cap (100 units, 64 criteria, depth 8), q over 16 KB "
             "or a body over 64 KB. The X-Status-Reason header carries the upstream text.",
    "ERR_KEY": "403 Forbidden. No X-Api-Key header, or the key is unknown or revoked. Get a Free key at /api/v1/meta/signup/. "
               "A data endpoint answers the upstream body {\"detail\": \"You do not have permission to perform this action.\"} with no "
               "X-Status-Reason-Code header; the code appears as that header only on the account endpoints, and in the problem details body.",
    "ERR_ES": "500 Internal Server Error. The search backend raised an error or a timeout (20 s a statement, 30 s a request), "
              "or an operator was used on a field type that rejects it (prefix or wildcard on numeric, date and boolean fields).",
    "ERR_NOT_IMPLEMENTED": "501 Not Implemented. The endpoint's data set is not in this snapshot: pre-grant publications are deferred "
                           "(PatentRef checklist 1.5), the NBER routes are dead upstream, and the long-text endpoints answer it only when no "
                           "long-text file is in service (GET /api/v1/meta/health/ shows text_data_version). The X-Status-Reason header names the data set.",
    "ERR_AUTH": "401 Unauthorized. The account endpoints need Authorization: Bearer <Supabase access token>; the token is missing, expired or not from the PatentRef project.",
    "ERR_SIGNUP_LIMIT": "429 Too Many Requests. More than the daily number of new accounts from one email domain; try after the Retry-After seconds.",
    "THROTTLED": "429 Too Many Requests. Over the per-minute allowance (45 for a Free key, a fixed UTC clock minute) or the monthly allowance (1,000). "
                 "Retry-After says when the window resets; the body is DRF's 'Request was throttled. Expected available in N seconds.'",
    "NOT_FOUND": "404 Not Found. A detail route with a key that has no record ({\"detail\": \"Not found.\"}), or a path that is not routed.",
}


def public_dir():
    return Path(getattr(settings, "LAPSE_PUBLIC_DIR", "/srv/lapse/public"))


def licenses_path():
    return Path(getattr(settings, "LAPSE_LICENSES_PATH", "/srv/lapse/LICENSES.md"))


def _serve(path, content_type):
    try:
        data = path.read_bytes()
    except OSError:
        resp = HttpResponse("not published yet\n", status=404, content_type="text/plain; charset=utf-8")
        resp["Cache-Control"] = "no-store"
        return resp
    resp = HttpResponse(data, content_type=content_type)
    resp["Cache-Control"] = CACHE
    resp["Access-Control-Allow-Origin"] = "*"
    return resp


@require_GET
def public_file(request, name):
    if name == LICENSES_NAME:
        return _serve(licenses_path(), "text/markdown; charset=utf-8")
    ct = PUBLIC_FILES.get(name)
    if not ct:
        return HttpResponse("not found\n", status=404, content_type="text/plain; charset=utf-8")
    return _serve(public_dir() / name, ct)


@require_GET
def error_docs(request, code=""):
    code = (code or "").upper().strip("/")
    if not code:
        body = "PatentRef API error codes (X-Status-Reason-Code; the RFC 9457 problem type is https://patentref.io/docs/errors/<CODE>)\n\n"
        body += "\n".join(f"{k}\n  {v}\n" for k, v in ERROR_CODES.items())
        body += "\nSend Accept: application/problem+json on any request to receive errors as problem details objects; the upstream body "
        body += "{\"error\": true} and the headers X-Status-Reason and X-Status-Reason-Code are the default. Full guide: https://patentref.io/llms-full.txt\n"
        resp = HttpResponse(body, content_type="text/plain; charset=utf-8")
    elif code in ERROR_CODES:
        resp = HttpResponse(f"{code}\n{ERROR_CODES[code]}\nAll codes: https://patentref.io/docs/errors/\n", content_type="text/plain; charset=utf-8")
    else:
        resp = HttpResponse(f"unknown code {code}; see https://patentref.io/docs/errors/\n", status=404, content_type="text/plain; charset=utf-8")
    resp["Cache-Control"] = CACHE
    return resp


# ---------------------------------------------------------------- footer from LICENSES.md

_footer_cache = {"at": 0.0, "lines": None, "mtime": None}

# The register numbers its sections ("## 5. Attribution lines"); an unnumbered heading is accepted too.
# Gate 1.11 re-audit 2026-10-08: the first form matched only the unnumbered heading, so every page
# showed the fallback. The section runs to the next level-2 heading or the end of the file.
ATTRIBUTION_HEADING = re.compile(r"^##[ \t]+(?:\d+(?:\.\d+)*\.?[ \t]+)?Attribution lines[ \t]*$(.*?)(?=^##[ \t]|\Z)", re.M | re.S)


def footer_lines(now=None):
    """The attribution lines of LICENSES.md (its "Attribution lines" section, numbered or not, one bullet
    per line), re-read when the file changes (checked at most once a minute). Fallback: the fixed
    PatentsView line."""
    now = now or time.time()
    p = licenses_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return list(FALLBACK_FOOTER)
    if _footer_cache["lines"] is not None and _footer_cache["mtime"] == mtime and now - _footer_cache["at"] < 60:
        return _footer_cache["lines"]
    lines = []
    try:
        text = p.read_text(encoding="utf-8")
        m = ATTRIBUTION_HEADING.search(text)
        if m:
            for ln in m.group(1).splitlines():
                ln = ln.strip()
                if ln.startswith("- "):
                    lines.append(ln[2:].strip())
    except OSError:
        lines = []
    if not lines:
        lines = list(FALLBACK_FOOTER)
    _footer_cache.update({"at": now, "lines": lines, "mtime": mtime})
    return lines


# ---------------------------------------------------------------- /api/v1/meta/

def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _build_rows():
    try:
        from API.search_sqlite import LapseSQLiteSearch

        s = LapseSQLiteSearch.from_django_settings()
        version = s.data_version()
    except Exception:
        return {}, getattr(settings, "LAPSE_DATA_VERSION", "") or ""
    try:
        rows = dict(s.connection().execute("SELECT k, v FROM _lapse_build").fetchall())
    except Exception:  # a file built before the runner has no stamp table
        rows = {}
    return rows, version


def _weekly_grants(rows):
    """PatentRef 4.7: what the served file carries beyond the PatentsView release (the weekly grant overlay of the
    patentref runner, lapse/grants.py), from its _lapse_build rows; None for a file without it."""
    try:
        weeks = json.loads(rows.get("grants") or "[]")
    except ValueError:
        return None
    if not weeks:
        return None
    return {"patentsview_through": rows.get("pv_through"), "weekly_grants_through": rows.get("grants_through"),
            "weeks": len(weeks), "patents": sum(int(w.get("patents") or 0) for w in weeks),
            "source": "USPTO Patent Grant Full Text Data, weekly (ODP product PTGRXML)",
            "names": "inventor, assignee and attorney names of these patents are as printed, with no ids and "
                     "disambiguated: false, until a PatentsView release covers them"}


def _version_key(v):
    m = re.fullmatch(r"([0-9]{8})\.([0-9]+)", str(v or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def served_versions(api_dir):
    """Every data_version the swap journal says was put in service (a swap or a rollback to it)."""
    out = set()
    try:
        with open(os.path.join(api_dir, "swap_journal.jsonl"), encoding="utf-8") as fh:
            for line in fh:
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if j.get("op") in ("swap", "rollback") and j.get("data_version"):
                    out.add(str(j["data_version"]))
    except OSError:
        pass
    return out


def superseded_versions(builds, version, served=()):
    """The READY builds other than the one in service, except a newer build that was never served: that one is
    the next build waiting for its swap, not a superseded one (gate 2.7: build 20261006.4 was listed as
    superseded while 20261006.2 served, so `?v=20261006.4` would have shown the superseded banner before it
    ever served). A newer build the journal shows in service once (a swap later rolled back) stays superseded.
    When either version is not of the form YYYYMMDD.N every other READY build counts, as before."""
    cur = _version_key(version)
    out = []
    for b in builds or []:
        v = b.get("data_version")
        if b.get("status") not in ("READY", "READY_WITH_FLAGS") or not v or v == version:
            continue
        k = _version_key(v)
        if cur is not None and k is not None and k > cur and v not in served:
            continue
        out.append(v)
    return sorted(out)


def meta_facts():
    p = getattr(settings, "LAPSE_STATUS_PATHS", {})
    api_dir = p.get("api_dir", "/data/api")
    lapse_data = p.get("lapse_data", "/data/lapse")
    rows, version = _build_rows()
    version = version or rows.get("data_version") or "unversioned"
    side = _read_json(p.get("current_sidecar", f"{api_dir}/snapshot_current.json")) or {}
    if side.get("data_version") not in (None, version):
        side = {}  # the sidecar describes another file; show only what the served file says
    versions = _read_json(f"{lapse_data}/refresh_versions.json") or {"builds": []}
    release = side.get("release") or rows.get("release") or (version.split(".")[0] if version[:8].isdigit() else "")
    as_of = f"{release[:4]}-{release[4:6]}-{release[6:8]}" if len(release) == 8 and release.isdigit() else None
    superseded = superseded_versions(versions.get("builds", []), version, served_versions(api_dir))
    delta = []
    for row in (side.get("counts") or {}).get("api", []):
        try:
            table, cur, nxt, verdict = row[0], row[1], row[2], row[3]
        except (IndexError, TypeError):
            continue
        cur_i = int(cur) if cur is not None else None
        nxt_i = int(nxt) if nxt is not None else None
        d = (nxt_i - cur_i) if cur_i is not None and nxt_i is not None else None
        delta.append({"table": table, "current": cur_i, "next": nxt_i,
                      "added": max(d, 0) if d is not None else None, "removed": max(-d, 0) if d is not None else None, "verdict": verdict})
    weekly = _weekly_grants(rows)
    sha = (side.get("sha256") or {}).get("api") if isinstance(side.get("sha256"), dict) else side.get("sha256")
    prev = side.get("previous")
    prev_version = None
    if prev:
        m = re.search(r"snapshot_([0-9]{8}\.[0-9]+)\.db", str(prev))
        prev_version = m.group(1) if m else os.path.splitext(os.path.basename(str(prev)))[0]
    return {
        "service": "PatentRef API", "base": "https://patentref.io/api/v1/",
        "data_version": version, "build_id": side.get("build_id") or rows.get("build_id"),
        "release": release or None, "as_of": as_of,
        "refreshed_at": side.get("swapped_at"), "built_at": side.get("built_at"),
        "snapshot_sha256": sha, "index_set": rows.get("index_set"),
        "previous_version": prev_version, "superseded_versions": sorted(superseded),
        "delta": delta, "delta_note": "rows per table, the build in service against the one it replaced (the runner's count_deltas gate); empty for a file built before the runner",
        "fee": side.get("fee") or None,
        "weekly_grants": weekly,
        "deferred": ["publications (pre-grant set, checklist 1.5)"],
        "attribution": footer_lines()[0],
        "licenses_url": "https://patentref.io/LICENSES.md",
        "guide": "https://patentref.io/llms-full.txt", "openapi": "https://patentref.io/openapi.json",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@require_GET
def meta_index(request):
    body = meta_facts()
    resp = JsonResponse(body, json_dumps_params={"indent": 1})
    resp["Cache-Control"] = "public, max-age=60"
    resp["Access-Control-Allow-Origin"] = "*"
    resp["X-Data-Version"] = body["data_version"]
    return resp
