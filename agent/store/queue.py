"""
The local, offline-durable queue the spec calls for: "store events locally
so temporary internet loss does not lose data." Every aggregated interval
(see monitor.aggregator) is written here the moment it closes, regardless
of whether the network is up -- sync.sync_service is what later reads
unsynced rows and uploads them, independently of when they were recorded.
"""
import logging
import sqlite3
import threading
from contextlib import contextmanager

logger = logging.getLogger("agent.store")

_lock = threading.Lock()


@contextmanager
def _connect(db_path):
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path):
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attendance_id TEXT NOT NULL,
                activity_type TEXT NOT NULL,
                application TEXT,
                application_display_name TEXT,
                window_title TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                synced INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_synced ON events (synced)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_attendance ON events (attendance_id)")


def enqueue_event(db_path, attendance_id, interval: dict):
    """`interval` is exactly the shape monitor.aggregator produces --
    activity_type, application, application_display_name, window_title,
    started_at, ended_at, duration_seconds (all already ISO-8601 /
    plain-number, ready to serialize)."""
    with _lock, _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO events (
                attendance_id, activity_type, application, application_display_name,
                window_title, started_at, ended_at, duration_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attendance_id,
                interval["activity_type"],
                interval.get("application"),
                interval.get("application_display_name"),
                interval.get("window_title"),
                interval["started_at"],
                interval["ended_at"],
                interval["duration_seconds"],
            ),
        )


def get_unsynced_batch(db_path, attendance_id, limit=500):
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE attendance_id = ? AND synced = 0 ORDER BY started_at ASC LIMIT ?",
            (attendance_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def has_unsynced(db_path, attendance_id) -> bool:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM events WHERE attendance_id = ? AND synced = 0 LIMIT 1", (attendance_id,)
        ).fetchone()
        return row is not None


def mark_synced(db_path, ids: list):
    if not ids:
        return
    with _lock, _connect(db_path) as conn:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE events SET synced = 1 WHERE id IN ({placeholders})", ids)


def to_api_payload(row: dict) -> dict:
    """Row -> exactly the shape agent.validation.js's `activityEvent`
    schema expects on the server. Optional fields are OMITTED entirely
    when unset, not sent as JSON null -- an IDLE interval has no
    application/window title at all (see monitor/aggregator.py's own
    "no app identity while idle" comment), which is a completely normal,
    expected case, but the server's own zod schema marks these fields
    `.optional()` -- which only ever means "the key can be missing," not
    "the key can be null". Sending null was being rejected as a
    validation error on every single batch that included even one idle
    interval, which is most of them."""
    payload = {
        "activityType": row["activity_type"],
        "startedAt": row["started_at"],
        "endedAt": row["ended_at"],
        "durationSeconds": row["duration_seconds"],
    }
    for local_key, api_key in (
        ("application", "application"),
        ("application_display_name", "applicationDisplayName"),
        ("window_title", "windowTitle"),
    ):
        value = row.get(local_key)
        if value is not None:
            payload[api_key] = value
    return payload


def purge_synced_older_than(db_path, days=14):
    """Housekeeping only -- synced rows have already made it to the
    server and serve no purpose staying in the local queue forever. Never
    touches unsynced rows regardless of age."""
    with _lock, _connect(db_path) as conn:
        conn.execute(
            "DELETE FROM events WHERE synced = 1 AND created_at < datetime('now', ?)",
            (f"-{days} days",),
        )
