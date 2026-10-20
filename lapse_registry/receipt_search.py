"""The snapshot search of a corpus receipt (PatentRef checklist 10.3), on one pinned snapshot connection (10.12).

similar(text, n) runs the query of /api/v1/lapse/similar (API/lapse_views.py: the input's distinct tokens, the rarest
16 kept by document frequency, FTS5 bm25 over title and abstract with weights 0, 1, 1, withdrawn patents left out, ties
by patent_id ascending) and returns the neighbors with their abstracts, the data version and the as-of date.

Pinned: the snapshot path (settings.LAPSE_SQLITE["path"], /data/api/snapshot_current.db, a symlink a swap moves) is
resolved once, a read-only connection is opened on that one file, and the term selection, the query, the data version
and the as-of date all come from that connection, which is closed at the end. A swap during the receipt moves the
symlink, not the open file, so every receipt describes exactly one data version: the one it names.
"""
import os
import sqlite3

from django.conf import settings

from API.lapse_views import SIMILAR_MAX_N, SIMILAR_MAX_TEXT, SIMILAR_MAX_TOKENS, select_terms
from API.search_sqlite import fts_quote, read_data_version, tokens

METHOD = "lapse_similar_bm25_title_abstract"
SQL = ("SELECT p.patent_id, p.patent_title, p.patent_abstract, p.patent_date, bm25(fts_patents, 0.0, 1.0, 1.0) AS s "
       "FROM fts_patents JOIN patents p ON p.rowid = fts_patents.rowid "
       "WHERE fts_patents MATCH ? AND p.withdrawn = 0 ORDER BY s ASC, p.patent_id ASC LIMIT ?")


def snapshot_path():
    cfg = getattr(settings, "LAPSE_SQLITE", None) or {}
    return cfg.get("path") or os.environ.get("LAPSE_SQLITE_PATH", "/data/api/snapshot_current.db")


def _as_of(con):
    try:
        rows = dict(con.execute("SELECT k, v FROM _lapse_build").fetchall())
    except sqlite3.Error:
        return ""
    rel = (rows.get("release") or "").replace("-", "")
    if len(rel) == 8 and rel.isdigit():
        return f"{rel[:4]}-{rel[4:6]}-{rel[6:]}"
    return (rows.get("refreshed_at") or "")[:10]


def similar(text, n, path=None, hold=None):
    """The receipt's search on one connection to one snapshot file. `hold` (tests) runs between the term selection and
    the query, where a swap could fall."""
    text = (text or "")[:SIMILAR_MAX_TEXT]
    n = max(1, min(int(n), SIMILAR_MAX_N))
    real = os.path.realpath(path or snapshot_path())
    con = sqlite3.connect("file:" + real.replace("\\", "/") + "?mode=ro", uri=True, check_same_thread=False, timeout=60)
    try:
        con.execute("PRAGMA temp_store=MEMORY")
        try:
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp.fts_patents_vocab USING fts5vocab('main', 'fts_patents', 'row')")
        except sqlite3.Error:
            pass
        con.execute("PRAGMA query_only=ON")
        data_version = read_data_version(con, real)
        as_of = _as_of(con)
        terms = []
        for t in tokens(text):
            if len(t) > 1 and t not in terms:
                terms.append(t)
            if len(terms) >= SIMILAR_MAX_TOKENS:
                break
        keep = select_terms(con, terms)
        if hold:
            hold()
        rows = []
        if keep:
            match = "{patent_title patent_abstract}: " + " OR ".join(fts_quote(t) for t in keep)
            rows = con.execute(SQL, (match, n)).fetchall()
        neighbors = [{"rank": i + 1, "patent_id": r[0], "patent_title": r[1] or "", "patent_abstract": r[2] or "",
                      "patent_date": r[3] or "", "score": round(-float(r[4]), 4)} for i, r in enumerate(rows)]
        return {"data_version": data_version, "as_of": as_of, "method": METHOD, "terms": keep, "neighbors": neighbors,
                "snapshot": os.path.basename(real)}
    finally:
        con.close()
