"""The registry's local read cache (PatentRef checklist 5.15): during a Supabase outage the public reads are answered from
a copy on the box, and writes answer 503 with Retry-After.

What it holds: the public answers of the read routes, exactly as they were last served from the database: a record's
JSON (/r/<id>.json and /registry/disclosures/<id> without a token), its proof object, its file, and the sitemap; plus
each public record's title and abstract so /registry/search can still answer from the copy. Only answers anyone may
read are stored (never an owner's view, a list of one account's records, or anything behind a token). It is filled two
ways: every anonymous read that the database answers writes its answer through (when it changed or is older than five
minutes), and `manage.py registry_cache_warm` (patentref-registry-cache.timer, every 10 minutes) copies every visible
record, so a record nobody has read yet is still there in an outage. A record the database no longer shows (hidden by a
takedown, checklist 10.9) leaves the copy at its next live read or the next warm run.

SQLite in WAL mode at REGISTRY_READ_CACHE (default <LAPSE_DATA_DIR>/read_cache.sqlite3, /data/registry on the box),
one connection per thread. A cache that cannot be opened or written never fails a live answer; it only means an outage
answers 503 for what it does not hold.
"""
import json
import logging
import os
import sqlite3
import threading
import time

from django.conf import settings

log = logging.getLogger("lapse_registry")
_local = threading.local()
REFRESH_SECONDS = 300
SCHEMA = """
create table if not exists answers (key text primary key, ctype text not null, body blob not null, stored real not null);
create table if not exists records (id text primary key, created text not null, title text not null,
                                    abstract text not null, stored real not null);
create table if not exists files (id text primary key, name text not null, media_type text not null, content blob not null,
                                  stored real not null);
"""


def path():
    p = getattr(settings, "REGISTRY_READ_CACHE", "") or os.environ.get("REGISTRY_READ_CACHE", "")
    if p:
        return p
    return os.path.join(os.environ.get("LAPSE_DATA_DIR", "/data/registry"), "read_cache.sqlite3")


def _conn():
    p = path()
    con = getattr(_local, "con", None)
    if con is not None and getattr(_local, "path", None) == p:
        return con
    con = sqlite3.connect(p, timeout=2, isolation_level=None, check_same_thread=False)
    con.execute("pragma journal_mode=wal")
    con.execute("pragma synchronous=normal")
    con.execute("pragma busy_timeout=2000")
    con.executescript(SCHEMA)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    _local.con, _local.path = con, p
    return con


def reset():
    con = getattr(_local, "con", None)
    _local.con = None
    if con is not None:
        try:
            con.close()
        except Exception:
            pass


def _safe(fn, default=None):
    try:
        return fn()
    except (sqlite3.Error, OSError) as e:
        log.warning("registry read cache: %s", e.__class__.__name__)
        reset()
        return default


# ------------------------------------------------------------------ answers


def put(key, body, ctype, force=False):
    if isinstance(body, str):
        body = body.encode("utf-8")

    def run():
        con = _conn()
        if not force:
            row = con.execute("select body, stored from answers where key = ?", (key,)).fetchone()
            if row and bytes(row[0]) == body and time.time() - row[1] < REFRESH_SECONDS:
                return False
        con.execute("insert into answers (key, ctype, body, stored) values (?, ?, ?, ?) on conflict (key) do update set "
                    "ctype = excluded.ctype, body = excluded.body, stored = excluded.stored", (key, ctype, body, time.time()))
        return True
    return _safe(run, False)


def get(key):
    """(body bytes, content type, seconds since stored) or None."""
    def run():
        row = _conn().execute("select body, ctype, stored from answers where key = ?", (key,)).fetchone()
        return (bytes(row[0]), row[1], max(0, int(time.time() - row[2]))) if row else None
    return _safe(run)


def put_record(rec, force=False):
    """A record's public JSON (record_json without the owner block), and its title and abstract for search."""
    if not isinstance(rec, dict) or "owner" in rec or not rec.get("id"):
        return False
    rid = rec["id"]
    body = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    changed = put(f"record:{rid}", body, "application/json", force=force)
    uc = rec.get("user_content") or {}

    def run():
        con = _conn()
        if rec.get("visibility") == "public":
            con.execute("insert into records (id, created, title, abstract, stored) values (?, ?, ?, ?, ?) on conflict (id) "
                        "do update set created = excluded.created, title = excluded.title, abstract = excluded.abstract, "
                        "stored = excluded.stored", (rid, rec.get("server_time") or "", uc.get("title") or "",
                                                     uc.get("abstract") or "", time.time()))
        else:
            con.execute("delete from records where id = ?", (rid,))
    _safe(run)
    return changed


def drop_record(rid):
    """Forget a record the database no longer shows (hidden, or never there)."""
    def run():
        con = _conn()
        for key in (f"record:{rid}", f"proof:{rid}"):
            con.execute("delete from answers where key = ?", (key,))
        con.execute("delete from records where id = ?", (rid,))
        con.execute("delete from files where id = ?", (rid,))
    _safe(run)


def record(rid):
    """The cached public JSON of a record, its `noindex` recomputed for now, or None."""
    got = get(f"record:{rid}")
    if not got:
        return None
    rec = json.loads(got[0].decode("utf-8"))
    after = rec.get("indexable_after")
    if isinstance(after, str):
        rec["noindex"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) < after[:19]
    return rec, got[2]


def search(q, base_url, limit=20):
    """Public records whose title and abstract hold every word of q (case-insensitive), newest first, from the copy.
    Simpler than the database's full-text search: an outage answer, marked as such by the view."""
    words = [w for w in "".join(c.lower() if c.isalnum() else " " for c in (q or "")[:200]).split() if w]
    if not words:
        return []

    def run():
        con = _conn()
        rows = con.execute("select id, title, abstract from records order by created desc").fetchall()
        out = []
        for rid, title, abstract in rows:
            text = f"{title} {abstract}".lower()
            if all(w in text for w in words):
                got = record(rid)
                if got:
                    out.append(got[0])
            if len(out) >= limit:
                break
        return out
    return _safe(run, [])


def put_file(rid, name, media_type, content):
    cap = int(getattr(settings, "REGISTRY_READ_CACHE_FILE_MAX", 20 * 1024 * 1024))
    if len(content) > cap:
        return False

    def run():
        con = _conn()
        row = con.execute("select length(content), stored from files where id = ?", (rid,)).fetchone()
        if row and row[0] == len(content) and time.time() - row[1] < REFRESH_SECONDS:
            return False
        con.execute("insert into files (id, name, media_type, content, stored) values (?, ?, ?, ?, ?) on conflict (id) do "
                    "update set name = excluded.name, media_type = excluded.media_type, content = excluded.content, "
                    "stored = excluded.stored", (rid, name, media_type, content, time.time()))
        return True
    return _safe(run, False)


def get_file(rid):
    def run():
        row = _conn().execute("select name, media_type, content from files where id = ?", (rid,)).fetchone()
        return (row[0], row[1], bytes(row[2])) if row else None
    return _safe(run)


def figures():
    """Counts and the newest copy's age, for /registry/health."""
    def run():
        con = _conn()
        n = con.execute("select count(*) from answers where key like 'record:%'").fetchone()[0]
        newest = con.execute("select max(stored) from answers").fetchone()[0]
        return {"records": n, "age_seconds": int(time.time() - newest) if newest else None}
    return _safe(run, {"records": 0, "age_seconds": None, "error": "unreadable"})


# ------------------------------------------------------------------ the warm job


def warm(conn, service, base_url, now=None):
    """Copy every record the database shows to a reader (public and sealed; a sealed record holds no content), its proof,
    its file when public, and the sitemap; forget cached records it no longer shows. Returns counts."""
    with service.transaction(conn, "registry_reader") as cur:
        cur.execute("select id from registry.disclosures order by created_at desc")
        ids = [r["id"] for r in cur.fetchall()]
    seen = set()
    files = 0
    for rid in ids:
        rec = service.get_record(conn, rid, base_url)
        if rec is None:
            continue
        seen.add(rid)
        put_record(rec, force=True)
        proof = service.get_proof(conn, rid)
        if proof is not None:
            put(f"proof:{rid}", json.dumps(proof, ensure_ascii=False, separators=(",", ":")), "application/json", force=True)
        if rec.get("file"):
            got = service.get_file(conn, rid)
            if got is not None and put_file(rid, *got):
                files += 1
    put("sitemap", service.sitemap_xml(conn, base_url), "application/xml; charset=utf-8", force=True)

    def forget():
        con = _conn()
        cached = [r[0][len("record:"):] for r in con.execute("select key from answers where key like 'record:%'").fetchall()]
        gone = [rid for rid in cached if rid not in seen]
        for rid in gone:
            drop_record(rid)
        return len(gone)
    dropped = _safe(forget, 0)
    return {"records": len(seen), "files_copied": files, "forgotten": dropped}
