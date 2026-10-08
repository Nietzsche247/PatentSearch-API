"""The `lapse` group and the verdict record (PatentRef gates 2.9 and 2.12; docs/SCAFFOLD.md 5.1 and 5.2).

Where the fields come from (decision 2026-10-08, patentref `decisions.md`): the Lapse catalog
(`/data/lapse/snapshot_current.db`, the loader layout with fee_status, lapse_date, reinstated_date, the
citation-timing columns and the Lapse scores) is ATTACHed read-only to the search connection as schema
`cat` at connection open, keyed by patent id ("US" + patent_id), and the term-of-grant flags (PTA days,
terminal disclaimer) and the withdrawn flag come from the search snapshot's own tables. No new table in the
API layout, so the group needs no snapshot rebuild and the weekly catalog refresh keeps it current; the
runner swaps both files under the same data_version, and this module reports the catalog's own
`data_version` in every group so a reader can tell when the two differ.

Status, basis and `reinstatement_possible` are computed by the one status engine, `lapse.expiry.expiry_record`
in the patentref repo (LAPSE_REPO_DIR, /srv/lapse on the box), never here: the catalog loader, the expiry
audit and this API read the same function.

Group shape (one object in a list, like every nested group of /patent/): expiry_status, expiry_date_estimated,
expiry_basis, reinstatement_possible, fee_status, lapse_date, reinstated_date, prior, prior_pct, fwd_early,
fwd_late, sleeper_flag, as_of, data_version. Never carried: too_good_kinds, any crackpot field, any confidence.
"""
import os
import sqlite3
import sys
import threading
from datetime import date

from django.conf import settings

LAPSE_PATH = "lapse"
LAPSE_FIELDS = ["expiry_status", "expiry_date_estimated", "expiry_basis", "reinstatement_possible", "fee_status", "lapse_date",
                "reinstated_date", "prior", "prior_pct", "fwd_early", "fwd_late", "sleeper_flag", "as_of", "data_version"]
SLEEPER_MAX_CITES = 5     # the asleep pool of the Lapse backtest (lapse/backtest.py POOL_SQL): quiet patents
SLEEPER_MAX_RECENT = 1

_local = threading.local()


def repo_dir():
    return getattr(settings, "LAPSE_REPO_DIR", None) or os.environ.get("LAPSE_REPO_DIR", "/srv/lapse")


def expiry_module():
    """`lapse.expiry` from the patentref clone; None (with the reason cached) when it is not importable."""
    mod = getattr(_local, "expiry", None)
    if mod is not None:
        return mod
    try:
        from lapse import expiry  # noqa: F401  (already on the path: tests, or a box with the package installed)
    except ImportError:
        d = repo_dir()
        if d and d not in sys.path and os.path.isdir(d):
            sys.path.insert(0, d)
        try:
            from lapse import expiry
        except ImportError as e:
            _local.expiry_error = f"lapse.expiry not importable from {d}: {e}"
            return None
    _local.expiry = expiry
    return expiry


def catalog_path():
    return getattr(settings, "LAPSE_CATALOG_PATH", None) or os.environ.get("LAPSE_CATALOG_PATH", "/data/lapse/snapshot_current.db")


def catalog_real():
    p = catalog_path()
    return os.path.realpath(p) if p and os.path.exists(p) else None


def attach(con, real):
    """ATTACH the catalog read-only as `cat` and read its meta. Returns the catalog facts dict or None."""
    if not real:
        return None
    try:
        con.execute("ATTACH DATABASE ? AS cat", ("file:" + real.replace("\\", "/") + "?mode=ro",))
        meta = dict(con.execute("SELECT k, v FROM cat.meta").fetchall())
        cols = {r[1] for r in con.execute("PRAGMA cat.table_info(catalog)")}
    except sqlite3.Error:
        return None
    release = meta.get("release") or ""
    as_of = f"{release[:4]}-{release[4:6]}-{release[6:]}" if len(release) == 8 and release.isdigit() else None
    return {"real": real, "data_version": meta.get("data_version"), "release": release or None, "as_of": as_of,
            "fee_rule": meta.get("fee_rule"), "columns": cols}


def _ids_sql(n):
    return ",".join("?" * n)


def fetch_rows(con, cat, ids):
    """Catalog and term-of-grant rows for a list of API patent ids. Returns {patent_id: merged dict}."""
    out = {}
    if not ids:
        return out
    cat_ids = ["US" + str(i) for i in ids]
    cols = cat["columns"]
    extra = [c for c in ("reinstated_date", "pta_days", "term_disclaimer", "disclaimer_date") if c in cols]
    sel = "id, filing_date, grant_date, fee_status, lapse_date, prior, prior_pct, fwd_early, fwd_late, fwd_cites, fwd_recent"
    sel += "".join(", " + c for c in extra)
    for chunk in range(0, len(cat_ids), 500):
        part = cat_ids[chunk:chunk + 500]
        for row in con.execute(f"SELECT {sel} FROM cat.catalog WHERE id IN ({_ids_sql(len(part))})", part):
            d = dict(zip(sel.split(", "), row))
            out[d["id"][2:]] = d
    # the term-of-grant flags and the withdrawn flag from the search snapshot itself
    for chunk in range(0, len(ids), 500):
        part = [str(i) for i in ids[chunk:chunk + 500]]
        try:
            for pid, ext, td, dd in con.execute(
                    f'SELECT "_pid", term_extension, term_disclaimer, disclaimer_date FROM patents__us_term_of_grant '
                    f'WHERE "_pid" IN ({_ids_sql(len(part))})', part):
                out.setdefault(pid, {"id": "US" + pid}).update({"term_extension": ext, "term_disclaimer_raw": td, "disclaimer_date_raw": dd})
        except sqlite3.Error:
            pass
        try:
            for pid, pdate, withdrawn, fdate in con.execute(
                    f'SELECT p.patent_id, p.patent_date, p.withdrawn, (SELECT filing_date FROM patents__application a '
                    f'WHERE a."_pid" = p.patent_id ORDER BY a."_ord" LIMIT 1) FROM patents p WHERE p.patent_id IN ({_ids_sql(len(part))})', part):
                d = out.setdefault(pid, {"id": "US" + pid})
                d["withdrawn"] = bool(withdrawn)
                d.setdefault("grant_date", pdate)
                if not d.get("grant_date"):
                    d["grant_date"] = pdate
                if not d.get("filing_date"):
                    d["filing_date"] = fdate
        except sqlite3.Error:
            pass
    return out


def record_for(expiry, cat, pid, d):
    """The status engine's record for one merged row (`d` from fetch_rows)."""
    flags = expiry.term_flags(d.get("term_extension"), d.get("term_disclaimer_raw"), d.get("disclaimer_date_raw"))
    if d.get("pta_days") is not None and "pta_days" in cat["columns"]:
        flags["pta_days"] = int(d.get("pta_days") or 0) or flags["pta_days"]
    return expiry.expiry_record(d.get("filing_date"), d.get("grant_date"), d.get("fee_status"), d.get("lapse_date"),
                                reinstated_date=d.get("reinstated_date"), pta_days=flags["pta_days"],
                                terminal_disclaimer=flags["terminal_disclaimer"], disclaimer_date=flags["disclaimer_date"],
                                withdrawn=bool(d.get("withdrawn")), patent_id="US" + str(pid),
                                as_of=cat.get("as_of") or date.today().isoformat())


def group_object(expiry, cat, pid, d):
    rec = record_for(expiry, cat, pid, d)
    expired = rec["status"] in (expiry.STATUS_EXPIRED_TERM, expiry.STATUS_EXPIRED_FEE)
    quiet = (d.get("fwd_cites") or 0) <= SLEEPER_MAX_CITES and (d.get("fwd_recent") or 0) <= SLEEPER_MAX_RECENT
    return {
        "expiry_status": rec["status"], "expiry_date_estimated": rec["expiry_date_estimated"], "expiry_basis": rec["basis"],
        "reinstatement_possible": rec["reinstatement_possible"], "fee_status": d.get("fee_status"),
        "lapse_date": d.get("lapse_date"), "reinstated_date": d.get("reinstated_date"),
        "prior": d.get("prior"), "prior_pct": d.get("prior_pct"), "fwd_early": d.get("fwd_early"), "fwd_late": d.get("fwd_late"),
        "sleeper_flag": bool(expired and quiet and "id" in d and d.get("grant_date")),
        "as_of": rec["as_of"], "data_version": cat.get("data_version"),
    }


def groups_for(con, cat, ids):
    """{patent_id: [group object]} for the ids present in the catalog or the snapshot; absent ids get no group."""
    expiry = expiry_module()
    if expiry is None or cat is None:
        return {}
    rows = fetch_rows(con, cat, ids)
    return {pid: [group_object(expiry, cat, pid, d)] for pid, d in rows.items()}


def verdict(con, cat, pid, data_version, base_url):
    """The check_patent_status record (scaffold 5.2) for one API patent id, or None when the patent is in
    neither the catalog nor the snapshot."""
    from API import lapse_wording

    expiry = expiry_module()
    if expiry is None:
        raise RuntimeError(getattr(_local, "expiry_error", "status engine unavailable"))
    rows = fetch_rows(con, cat, [pid]) if cat else {}
    d = rows.get(str(pid))
    if d is None:
        return None
    rec = record_for(expiry, cat, pid, d)
    pid_s = str(pid)
    out = {
        "patent_id": pid_s,
        "status": rec["status"],
        "basis": rec["basis"],
        "reinstatement_possible": rec["reinstatement_possible"],
        "reinstatement_basis": rec["reinstatement_basis"],
        "expiry_date_estimated": rec["expiry_date_estimated"],
        "lapse_date": rec["lapse_date"],
        "reinstated_date": rec["reinstated_date"],
        "as_of": rec["as_of"],
        "data_version": data_version,
        "catalog_data_version": cat.get("data_version") if cat else None,
        "permalink": f"{base_url}/p/{pid_s}?v={data_version}",
        "plain": lapse_wording.plain(rec, "US" + pid_s, data_version),
        "next_steps": [
            {"kind": "record", "url": f"{base_url}/p/{pid_s}?v={data_version}", "audience": "human"},
            {"kind": "patent", "url": f"{base_url}/api/v1/patent/{pid_s}/", "audience": "agent"},
            {"kind": "fee_events", "tool": "uspto_patent_center", "url": f"https://patentcenter.uspto.gov/applications?patentNumber={pid_s}",
             "audience": "agent"},
        ],
    }
    return out
