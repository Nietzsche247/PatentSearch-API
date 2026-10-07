"""Exact cross-process counters for the throttles (PatentRef gate 5.10, throttle fix of 2026-10-06).

Why this exists. DRF's `SimpleRateThrottle` keeps a list of timestamps in the Django cache and does
`get`, append, `set` on every request. On this box the cache is `DatabaseCache` on the SQLite Django DB,
and that leaked in two ways under concurrent traffic: the `set` (a SELECT then an UPDATE inside a deferred
transaction) got SQLITE_BUSY at the lock upgrade when another worker held the write lock and Django
dropped the write silently (`_base_set` returns False on DatabaseError), and the `get`/`set` pair is not
atomic across the eight gunicorn workers, so parallel requests read the same history and overwrite each
other's writes. A 60-request parallel burst from one Free key got 60 x 200 (audit
`ops/audits/2026-10-06_gate-5.10_throttle-fix_patentref-us1.txt`).

What this does instead. One row per (subject, window) in `lapse_accounts_windowcount` (per-minute
windows) or `lapse_accounts_monthlyusage` (the month), bumped with a single conditional
`INSERT ... ON CONFLICT DO UPDATE ... WHERE count < limit RETURNING count`. SQLite runs a single statement
atomically under its one-writer lock, the busy timeout makes a contending writer wait instead of fail, and
the statement either increments and returns the new count or does nothing and returns no row. That is
exact across processes and threads with no explicit transaction.

Windows are fixed: a per-minute window is the UTC minute `int(now // 60)`, so "45 per minute" means 45 in
a clock minute; the 46th request in that minute gets 429 with `Retry-After` counting to the next minute.
A burst that straddles a minute boundary can therefore get up to 45 in each minute.
"""
import time

from django.db import connection

WINDOW_TABLE = "lapse_accounts_windowcount"
MONTH_TABLE = "lapse_accounts_monthlyusage"
UNLIMITED = 2**31 - 1


def bump(table, subject, window_col, window, limit, now=None):
    """Atomically add one to the (subject, window) row of `table` if its count is below `limit`.

    Returns the new count, or None when the row is already at the limit (nothing changed).
    `limit` None means no limit. The row is created at 1 when absent."""
    if limit is None:
        limit = UNLIMITED
    sql = (
        f"INSERT INTO {table} (subject, {window_col}, count) VALUES (%s, %s, 1) "
        f"ON CONFLICT(subject, {window_col}) DO UPDATE SET count = count + 1 "
        f"WHERE {table}.count < %s RETURNING count"
    )
    with connection.cursor() as cur:
        cur.execute(sql, [subject, window, limit])
        row = cur.fetchone()
    return row[0] if row else None


def refund(table, subject, window_col, window):
    """Take one back from a row (a reserved request that must not count). Never goes below zero."""
    with connection.cursor() as cur:
        cur.execute(
            f"UPDATE {table} SET count = count - 1 WHERE subject = %s AND {window_col} = %s AND count > 0",
            [subject, window],
        )
        return cur.rowcount


def drop_old_windows(subject, window):
    """Remove this subject's earlier windows of the same scope (called when a new window row is created,
    so the table holds about one row per active subject and scope). The scope is the part of the window
    id before the first colon; windows of other scopes on the same subject (an address counted under
    `nokey`, `ip`, `inflight` and `meta` at once) are left alone."""
    scope = window.split(":", 1)[0] + ":"
    with connection.cursor() as cur:
        cur.execute(
            f"DELETE FROM {WINDOW_TABLE} WHERE subject = %s AND win < %s AND substr(win, 1, %s) = %s",
            [subject, window, len(scope), scope],
        )


def minute_window(scope, duration, now=None):
    """(window id, seconds until it ends) for a fixed window of `duration` seconds aligned to the epoch."""
    now = time.time() if now is None else now
    n = int(now // duration)
    # zero-padded so string comparison in drop_old_windows orders windows in time
    return f"{scope}:{duration}:{n:012d}", max(1, int((n + 1) * duration - now) + 1)
