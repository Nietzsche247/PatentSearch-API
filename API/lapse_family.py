"""Family links on the record page (PatentRef checklist 1.9).

The patentref family loader (lapse/family.py in the patentref repo) puts three tables into the Lapse catalog through
the normal refresh build and swap: family_us (one row per US patent: its family_id and the Global Dossier link of its
application), family_pub (the publications outside the US in each family: office, number, kind, publication date) and
family_meta (source, license, the credit line). The catalog is ATTACHed read-only as `cat` on every search connection
(API/lapse_group.py), so this module only reads. A catalog without those tables gives None: the page shows no family
block, as before 1.9. Source: Google Patents Public Datasets on BigQuery, CC BY 4.0; the credit line goes on every page
that shows the block. No legal status of any family member is read or shown (LICENSES.md section 3).
"""
import sqlite3

CREDIT = "Family data from Google Patents Public Datasets, used under CC BY 4.0."  # patentref LICENSES.md section 3
MAX_MEMBERS = 200


def _has_tables(con):
    try:
        names = {r[0] for r in con.execute("SELECT name FROM cat.sqlite_master WHERE name IN ('family_us', 'family_pub', 'family_meta')")}
    except sqlite3.Error:
        return False
    return names == {"family_us", "family_pub", "family_meta"}


def _date(v):
    s = str(v or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() and s != "00000000" else None


def family_for(con, pid):
    """{"members": [{"office", "number", "kind", "published"}], "global_dossier_url", "credit", "source", "license",
    "retrieved_at", "more"} for one API patent id, or None when the catalog has no family tables or no row for it."""
    if not _has_tables(con):
        return None
    try:
        row = con.execute("SELECT family_id, global_dossier_url FROM cat.family_us WHERE patent_id = ?", (str(pid),)).fetchone()
        if not row:
            return None
        fam, gd = row
        members = []
        if fam:
            members = [{"office": o, "number": n, "kind": k, "published": _date(d)} for o, n, k, d in con.execute(
                "SELECT office, number, kind, pub_date FROM cat.family_pub WHERE family_id = ? ORDER BY office, pub_date, number LIMIT ?",
                (fam, MAX_MEMBERS + 1))]
        meta = dict(con.execute("SELECT k, v FROM cat.family_meta WHERE k IN ('credit', 'source', 'license', 'retrieved_at')"))
    except sqlite3.Error:
        return None
    more = len(members) > MAX_MEMBERS
    return {"members": members[:MAX_MEMBERS], "more": more, "global_dossier_url": gd or None,
            "credit": meta.get("credit") or CREDIT, "source": meta.get("source"), "license": meta.get("license"),
            "retrieved_at": (meta.get("retrieved_at") or "")[:10] or None}
