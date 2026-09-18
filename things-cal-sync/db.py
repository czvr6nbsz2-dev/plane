"""SQLite schema, migrations, CRUD for the sync state DB."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

import config
from models import SyncRecord


SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS task_map (
        things_id         TEXT PRIMARY KEY,
        cal_uid           TEXT UNIQUE,
        things_title      TEXT NOT NULL,
        synced_date       TEXT NOT NULL,
        synced_start_time TEXT NOT NULL,
        synced_end_time   TEXT NOT NULL,
        user_pinned       INTEGER NOT NULL DEFAULT 0,
        pinned_start_time TEXT,
        pinned_end_time   TEXT,
        last_synced_at    TEXT NOT NULL,
        things_mod_at     TEXT,
        creation_date     TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sync_runs (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        status      TEXT,
        notes       TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS recurring_series (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        master_start TEXT NOT NULL,
        master_end   TEXT NOT NULL,
        rule         TEXT NOT NULL,
        fetched_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_map_synced_date ON task_map(synced_date)",
    "CREATE INDEX IF NOT EXISTS idx_task_map_cal_uid ON task_map(cal_uid)",
]


def init_db(db_path: Path = config.DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        conn.commit()


@contextmanager
def connect(db_path: Path = config.DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _row_to_record(row: sqlite3.Row) -> SyncRecord:
    return SyncRecord(
        things_id=row["things_id"],
        cal_uid=row["cal_uid"],
        things_title=row["things_title"],
        synced_date=row["synced_date"],
        synced_start_time=row["synced_start_time"],
        synced_end_time=row["synced_end_time"],
        user_pinned=bool(row["user_pinned"]),
        pinned_start_time=row["pinned_start_time"],
        pinned_end_time=row["pinned_end_time"],
        last_synced_at=row["last_synced_at"],
        things_mod_at=row["things_mod_at"],
        creation_date=row["creation_date"],
    )


def get_record(conn, things_id: str) -> Optional[SyncRecord]:
    cur = conn.execute("SELECT * FROM task_map WHERE things_id = ?", (things_id,))
    row = cur.fetchone()
    return _row_to_record(row) if row else None


def get_record_by_uid(conn, cal_uid: str) -> Optional[SyncRecord]:
    cur = conn.execute("SELECT * FROM task_map WHERE cal_uid = ?", (cal_uid,))
    row = cur.fetchone()
    return _row_to_record(row) if row else None


def all_records(conn) -> list[SyncRecord]:
    cur = conn.execute("SELECT * FROM task_map")
    return [_row_to_record(r) for r in cur.fetchall()]


def records_for_date(conn, date_iso: str) -> list[SyncRecord]:
    cur = conn.execute(
        "SELECT * FROM task_map WHERE synced_date = ? ORDER BY creation_date ASC, things_id ASC",
        (date_iso,),
    )
    return [_row_to_record(r) for r in cur.fetchall()]


def upsert_record(conn, record: SyncRecord) -> None:
    conn.execute(
        """
        INSERT INTO task_map (
            things_id, cal_uid, things_title, synced_date,
            synced_start_time, synced_end_time, user_pinned,
            pinned_start_time, pinned_end_time, last_synced_at,
            things_mod_at, creation_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(things_id) DO UPDATE SET
            cal_uid = excluded.cal_uid,
            things_title = excluded.things_title,
            synced_date = excluded.synced_date,
            synced_start_time = excluded.synced_start_time,
            synced_end_time = excluded.synced_end_time,
            user_pinned = excluded.user_pinned,
            pinned_start_time = excluded.pinned_start_time,
            pinned_end_time = excluded.pinned_end_time,
            last_synced_at = excluded.last_synced_at,
            things_mod_at = excluded.things_mod_at,
            creation_date = excluded.creation_date
        """,
        (
            record.things_id,
            record.cal_uid,
            record.things_title,
            record.synced_date,
            record.synced_start_time,
            record.synced_end_time,
            1 if record.user_pinned else 0,
            record.pinned_start_time,
            record.pinned_end_time,
            record.last_synced_at,
            record.things_mod_at,
            record.creation_date,
        ),
    )


def update_times(conn, things_id, synced_date, synced_start_time, synced_end_time, last_synced_at):
    conn.execute(
        """
        UPDATE task_map
           SET synced_date = ?, synced_start_time = ?, synced_end_time = ?,
               last_synced_at = ?
         WHERE things_id = ?
        """,
        (synced_date, synced_start_time, synced_end_time, last_synced_at, things_id),
    )


def set_pinned(conn, things_id, pinned, pinned_start_time, pinned_end_time):
    conn.execute(
        """
        UPDATE task_map
           SET user_pinned = ?, pinned_start_time = ?, pinned_end_time = ?
         WHERE things_id = ?
        """,
        (1 if pinned else 0, pinned_start_time, pinned_end_time, things_id),
    )


def update_title(conn, things_id, title):
    conn.execute("UPDATE task_map SET things_title = ? WHERE things_id = ?", (title, things_id))


def delete_record(conn, things_id):
    conn.execute("DELETE FROM task_map WHERE things_id = ?", (things_id,))


def recurring_series_age_hours(conn) -> Optional[float]:
    """Hours since the recurring-series cache was filled, or None if empty."""
    cur = conn.execute("SELECT MAX(fetched_at) AS ts FROM recurring_series")
    row = cur.fetchone()
    if row is None or row["ts"] is None:
        return None
    try:
        fetched = datetime.fromisoformat(row["ts"])
    except ValueError:
        return None
    return (datetime.now() - fetched).total_seconds() / 3600.0


def get_recurring_series(conn) -> list[tuple[str, str, str]]:
    cur = conn.execute(
        "SELECT master_start, master_end, rule FROM recurring_series ORDER BY id"
    )
    return [(r["master_start"], r["master_end"], r["rule"]) for r in cur.fetchall()]


def replace_recurring_series(conn, rows: list[tuple[str, str, str]]) -> None:
    """Swap the whole cache for a freshly fetched set."""
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM recurring_series")
    conn.executemany(
        "INSERT INTO recurring_series (master_start, master_end, rule, fetched_at)"
        " VALUES (?, ?, ?, ?)",
        [(s, e, rule, now) for s, e, rule in rows],
    )


def start_sync_run(conn) -> int:
    cur = conn.execute(
        "INSERT INTO sync_runs (started_at, status) VALUES (?, ?)",
        (datetime.now().isoformat(timespec="seconds"), "running"),
    )
    conn.commit()
    return cur.lastrowid


def finish_sync_run(conn, run_id, status, notes=None):
    conn.execute(
        "UPDATE sync_runs SET finished_at = ?, status = ?, notes = ? WHERE id = ?",
        (datetime.now().isoformat(timespec="seconds"), status, notes, run_id),
    )
    conn.commit()
