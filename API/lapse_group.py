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


_reissues = {"key": None, "map": {}}


def listed_reissues(con, cat):
    """{patent id: 'US' + reissue id} for every reissue on the USPTO 35 USC 156 list, mapped back to the patent it reissued
    (the reissue's own patents__us_related_documents row of type reissue and kind parent-grant-document: RE47739 to
    7208489). Built once per process for a list file and search file, by indexed lookups of the reissues' own rows.
    Review 2 of 2026-10-11: the expired_term sentence of such a patent names the reissue the list carries."""
    try:
        from lapse import pte156
    except ImportError:
        return {}
    try:
        main = next((r[2] for r in con.execute("PRAGMA database_list") if r[1] == "main"), None)
    except sqlite3.Error:
        main = None
    key = (str(pte156.source_path()), main, (cat or {}).get("data_version"))
    if _reissues["key"] == key:
        return _reissues["map"]
    ids = pte156.listed_ids() if hasattr(pte156, "listed_ids") else sorted(pte156._load())
    out = {}
    for cid in ids:
        if not cid.startswith("USRE"):
            continue
        try:
            rows = con.execute('SELECT related_doc_number FROM patents__us_related_documents WHERE "_pid" = ? '
                               "AND related_doc_type = 'reissue' AND related_doc_kind = 'parent-grant-document'", (cid[2:],)).fetchall()
        except sqlite3.Error:
            return {}
        for (num,) in rows:
            n = str(num or "").strip().upper()
            n = n[2:] if n.startswith("US") else n
            n = n.lstrip("0") if n.isdigit() else n  # the rows write 05767115 and 7208489 alike
            if n:
                out[n] = cid
    _reissues.update(key=key, map=out)
    return out


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
        # the earliest continuation, division or continuation-in-part parent the record lists: the 20-year term counts
        # from its filing date (35 USC 154(a)(2)); never a parent's grant date (parent-grant-document rows) or a
        # provisional (us-provisional-application rows, 35 USC 154(a)(3)). Wording review 2026-10-11, section 2.2.
        # Grants of about 2002 to 2004 file a parent's international application as related-publication,
        # parent-pct-document; it counts too (35 USC 120, 365(c), 363; independent audit 3 of 2026-10-12, finding F2;
        # patentref lapse.expiry.counts_as_term_parent holds the same rule for the catalog). The rows are read whole and
        # judged by patentref lapse.parents.resolve after the filing dates are known (independent audit 4, finding F3: a
        # date the record contradicts is never used; see _resolve_parents).
        try:
            for pid, rtype, rkind, num, pdate in con.execute(
                    'SELECT "_pid", related_doc_type, related_doc_kind, related_doc_number, related_doc_published_date '
                    "FROM patents__us_related_documents "
                    "WHERE ((related_doc_kind IN ('parent-doc', 'parent-pct-document') "
                    "AND related_doc_type IN ('continuation', 'division', 'continuation-in-part')) "
                    "OR (related_doc_type = 'related-publication' AND related_doc_kind = 'parent-pct-document')) "
                    f'AND COALESCE(related_doc_published_date, \'\') != \'\' AND "_pid" IN ({_ids_sql(len(part))})', part):
                out.setdefault(pid, {"id": "US" + pid}).setdefault("_parent_rows", []).append((rtype, rkind, num, pdate))
        except sqlite3.Error:
            pass
        # a 371 national stage files on its international filing date (35 USC 363); older PatentsView rows give the 371
        # date as the application's filing date (US 6250119: 1999-07-07, international filing 1997-01-08)
        try:
            for pid, pct in con.execute(
                    'SELECT "_pid", MIN(published_filed_date) FROM patents__pct_data '
                    "WHERE pct_doc_type = 'pct_application' AND COALESCE(pct_371_date, '') != '' "
                    "AND COALESCE(published_filed_date, '') >= '1900-01-01' "
                    f'AND "_pid" IN ({_ids_sql(len(part))}) GROUP BY "_pid"', part):
                out.setdefault(pid, {"id": "US" + pid})["pct_filing_date"] = pct
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
    _resolve_parents(con, out)
    reissued = listed_reissues(con, cat) if out else {}
    for pid, re_id in reissued.items():
        if pid in out:
            out[pid]["pte_reissue"] = re_id
    return out


_pct_dates = {}  # PCT number -> the international filing date its national stage's record gives (or None), per process


def parents_module():
    """`lapse.parents` from the patentref clone (B10), or None for a checkout before it."""
    if expiry_module() is None:
        return None
    try:
        from lapse import parents
    except ImportError:
        return None
    return parents


def _resolve_parents(con, out):
    """parent_filing_date (and parent_unsettled) for each merged row from its parent rows (independent audit 4 of
    2026-10-12, finding F3; patentref lapse/parents.py holds the rule and the catalog's term_dates step uses it too). A
    row whose date fits its number counts as before; a contradicted date is replaced by the date another record gives for
    the same application (a PCT number's international filing date in patents__pct_data, a scan cached per process; a US
    application's filing date in the record of the patent granted on it), or, when there is none and that parent could be
    the earliest, the patent gets no term end. A patentref checkout without lapse.parents keeps the old rule."""
    P = parents_module()
    for pid, d in out.items():
        rows = d.pop("_parent_rows", None)
        if not rows:
            continue
        if P is None:
            dates = [r[3] for r in rows if (r[3] or "") >= "1900-01-01"]
            if dates:
                d["parent_filing_date"] = min(dates)
            continue
        if all(P.check(r[2], r[3], r[1])[0] is None for r in rows):
            d["parent_filing_date"] = min(r[3] for r in rows)
            continue

        def pct_lookup(number):
            key = P.norm_number(number)
            if key not in _pct_dates:
                names = P.pct_spellings(number)
                try:
                    got = [r[0] for r in con.execute(
                        "SELECT published_filed_date FROM patents__pct_data WHERE pct_doc_type = 'pct_application' "
                        f"AND pct_doc_number IN ({_ids_sql(len(names))})", names) if r[0]]
                except sqlite3.Error:
                    got = []
                _pct_dates[key] = min(got) if got else None
            return _pct_dates[key]

        def us_lookup(number, pid=pid):
            want = "".join(ch for ch in str(number or "") if ch.isdigit())
            try:
                granted = [str(r[0]).strip() for r in con.execute(
                    'SELECT related_doc_number FROM patents__us_related_documents WHERE "_pid" = ? '
                    "AND related_doc_kind = 'parent-grant-document'", (pid,)) if r[0]]
                granted = [g[2:] if g.upper().startswith("US") else g for g in granted]
                granted = [g.lstrip("0") if g.isdigit() else g for g in granted]
                if not granted:
                    return None
                for app, fd in con.execute(
                        f'SELECT application_id, filing_date FROM patents__application WHERE "_pid" IN ({_ids_sql(len(granted))})',
                        granted):
                    if "".join(ch for ch in str(app or "") if ch.isdigit()) == want and fd:
                        return fd
            except sqlite3.Error:
                return None
            return None

        own = [x for x in (d.get("filing_date"), d.get("pct_filing_date")) if x]
        got = P.resolve(rows, min(own) if own else None, pct_lookup, us_lookup)
        if got["parent_filing_date"]:
            d["parent_filing_date"] = got["parent_filing_date"]
        if got["unsettled"]:
            d["parent_unsettled"] = got["unsettled"]


def record_for(expiry, cat, pid, d):
    """The status engine's record for one merged row (`d` from fetch_rows)."""
    flags = expiry.term_flags(d.get("term_extension"), d.get("term_disclaimer_raw"), d.get("disclaimer_date_raw"))
    if d.get("pta_days") is not None and "pta_days" in cat["columns"]:
        flags["pta_days"] = int(d.get("pta_days") or 0) or flags["pta_days"]
    kw = dict(reinstated_date=d.get("reinstated_date"), pta_days=flags["pta_days"],
              terminal_disclaimer=flags["terminal_disclaimer"], disclaimer_date=flags["disclaimer_date"],
              withdrawn=bool(d.get("withdrawn")), patent_id="US" + str(pid), as_of=cat.get("as_of") or date.today().isoformat())
    if "parent_filing_date" in getattr(expiry.expiry_record, "__code__", None).co_varnames:
        kw["parent_filing_date"] = d.get("parent_filing_date")  # a patentref checkout before 2026-10-11 lacks it
    if d.get("parent_unsettled") and "parent_unsettled" in expiry.expiry_record.__code__.co_varnames:
        kw["parent_unsettled"] = d["parent_unsettled"]  # B10: a contradicted parent date no other record dates
    filing = d.get("filing_date")
    if d.get("pct_filing_date") and (not filing or d["pct_filing_date"] < filing):
        filing = d["pct_filing_date"]  # the international filing date of a 371 national stage (35 USC 363)
    rec = expiry.expiry_record(filing, d.get("grant_date"), d.get("fee_status"), d.get("lapse_date"), **kw)
    if d.get("pte_reissue"):
        rec["pte_reissue"] = d["pte_reissue"]  # wording only (review 2): the listed reissue of this patent, never in an answer
    return rec


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
    got = verdict_with_engine(con, cat, pid, data_version, base_url)
    return got[0] if got else None


def verdict_with_engine(con, cat, pid, data_version, base_url):
    """(the check_patent_status record, the engine record) or None. The engine record carries the wording-only keys
    (term_from_parent, disclaimer_applied, pte_156, record_term_end, pte_list_through, pte_reissue) that the sentence and
    the record page's Basis row need and the JSON answer does not carry (wording reviews of 2026-10-11)."""
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
    return out, rec
