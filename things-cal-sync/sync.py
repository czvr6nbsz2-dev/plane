#!/usr/bin/env python3
"""Things 3 <-> Calendar.app sync orchestrator."""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import config
import db
import things_bridge
import calendar_bridge
from conflict import classify_calendar_change
from models import CalEvent, SyncRecord, SyncSummary, ThingsTask
from stacking import assign_slots


log = logging.getLogger("things-cal-sync")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.TimedRotatingFileHandler(
        str(config.LOG_PATH),
        when="D", interval=1, backupCount=config.LOG_RETENTION_DAYS, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.TimedRotatingFileHandler):
            root.removeHandler(h)
    root.addHandler(handler)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    root.addHandler(stream)


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


class LockBusy(RuntimeError):
    pass


class FileLock:
    def __init__(self, path: Path, stale_seconds: int = config.LOCK_STALE_SECONDS):
        self.path = path
        self.stale_seconds = stale_seconds
        self.fd: Optional[int] = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            try:
                age = time.time() - self.path.stat().st_mtime
            except FileNotFoundError:
                age = 0
            if age > self.stale_seconds:
                log.warning("Removing stale lock file (age=%.0fs): %s", age, self.path)
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            else:
                raise LockBusy(f"Lock held: {self.path} (age={age:.0f}s)")
        os.write(self.fd, f"{os.getpid()}\n".encode())
        return self

    def __exit__(self, *a):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _date_iso(d: date) -> str:
    return d.isoformat()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_slot(target_date: date) -> tuple[datetime, datetime]:
    start = datetime(target_date.year, target_date.month, target_date.day, config.START_HOUR, 0)
    return start, start + timedelta(minutes=config.SLOT_MINUTES)


# ---------------------------------------------------------------------------
# Phase 1 helper: dedupe events sharing a things_id
# ---------------------------------------------------------------------------


def _dedupe_events(conn, events: list[CalEvent], summary: SyncSummary):
    by_things_id: dict[str, list[CalEvent]] = {}
    for ev in events:
        if ev.things_id:
            by_things_id.setdefault(ev.things_id, []).append(ev)

    keep_uids: set[str] = set()
    deleted = 0
    for things_id, group in by_things_id.items():
        if len(group) <= 1:
            keep_uids.add(group[0].cal_uid)
            continue
        record = db.get_record(conn, things_id)
        db_uid = record.cal_uid if record else None
        keeper: Optional[CalEvent] = None
        for ev in group:
            if ev.cal_uid == db_uid:
                keeper = ev
                break
        if keeper is None:
            keeper = group[-1]
        keep_uids.add(keeper.cal_uid)
        for ev in group:
            if ev.cal_uid == keeper.cal_uid:
                continue
            try:
                calendar_bridge.delete_event(ev.cal_uid)
                deleted += 1
                summary.deleted += 1
                log.info(
                    "Removed duplicate calendar event %s for things_id=%s (kept %s)",
                    ev.cal_uid, things_id, keeper.cal_uid,
                )
            except calendar_bridge.AppleScriptError as exc:
                log.warning("Could not delete duplicate event %s: %s", ev.cal_uid, exc)
                summary.errors += 1

    kept_events = [ev for ev in events if (ev.things_id is None) or (ev.cal_uid in keep_uids)]
    return kept_events, deleted


# ---------------------------------------------------------------------------
# Phase 3: Things -> Calendar
# ---------------------------------------------------------------------------


def phase_things_to_calendar(conn, active_tasks, cal_by_things_id, summary):
    for task in active_tasks.values():
        try:
            existing = db.get_record(conn, task.things_id)
            target_date = task.effective_date

            if existing is None:
                if target_date is None:
                    summary.skipped += 1
                    log.info("Skip task %s (%r): no When/Deadline and not in Today", task.things_id, task.title)
                    continue
                _create_for_task(conn, task, target_date, summary)
                continue

            cal_event = cal_by_things_id.get(task.things_id)
            new_date_iso = _date_iso(target_date) if target_date else existing.synced_date

            if target_date is not None and new_date_iso != existing.synced_date:
                log.info(
                    "Things task %s moved %s -> %s; recreating event",
                    task.things_id, existing.synced_date, new_date_iso,
                )
                summary.affected_dates.add(existing.synced_date)
                summary.affected_dates.add(new_date_iso)
                if existing.cal_uid:
                    try:
                        calendar_bridge.delete_event(existing.cal_uid)
                    except calendar_bridge.AppleScriptError as exc:
                        log.warning("Could not delete old event %s: %s", existing.cal_uid, exc)
                _create_for_task(conn, task, target_date, summary, replace=True)
                continue

            if task.title != existing.things_title and existing.cal_uid:
                try:
                    calendar_bridge.update_event_title(existing.cal_uid, task.title)
                    db.update_title(conn, task.things_id, task.title)
                    summary.updated += 1
                except calendar_bridge.AppleScriptError as exc:
                    log.warning("Could not update title for %s: %s", task.things_id, exc)
                    summary.errors += 1

            if cal_event is None and target_date is not None:
                log.info("Calendar event for %s missing; recreating", task.things_id)
                _create_for_task(conn, task, target_date, summary, replace=True)

        except Exception:
            log.exception("Error processing Things task %s", task.things_id)
            summary.errors += 1


def _create_for_task(conn, task, target_date, summary, replace=False):
    start_dt, end_dt = _default_slot(target_date)
    try:
        uid = calendar_bridge.create_event(
            title=task.title, start_dt=start_dt, end_dt=end_dt, things_id=task.things_id,
        )
    except calendar_bridge.AppleScriptError as exc:
        log.error("Failed to create event for %s: %s", task.things_id, exc)
        summary.errors += 1
        return

    record = SyncRecord(
        things_id=task.things_id, cal_uid=uid, things_title=task.title,
        synced_date=_date_iso(target_date),
        synced_start_time=_iso(start_dt), synced_end_time=_iso(end_dt),
        user_pinned=False, pinned_start_time=None, pinned_end_time=None,
        last_synced_at=_now_iso(),
        things_mod_at=task.modification_date.isoformat(timespec="seconds") if task.modification_date else None,
        creation_date=task.creation_date.isoformat(timespec="seconds") if task.creation_date else None,
    )
    db.upsert_record(conn, record)
    summary.affected_dates.add(record.synced_date)
    if replace:
        summary.updated += 1
    else:
        summary.created += 1
    log.info(
        "%s event for things_id=%s on %s (%r)",
        "Replaced" if replace else "Created",
        task.things_id, record.synced_date, task.title,
    )


# ---------------------------------------------------------------------------
# Phase 4: Calendar -> Things (detect user-driven moves)
# ---------------------------------------------------------------------------


def phase_calendar_to_things(conn, active_tasks, cal_events, summary):
    for event in cal_events:
        if not event.things_id:
            continue
        try:
            record = db.get_record(conn, event.things_id)
            if record is None:
                log.info("Deleting orphan calendar event %s (things_id=%s)", event.cal_uid, event.things_id)
                try:
                    calendar_bridge.delete_event(event.cal_uid)
                    summary.deleted += 1
                except calendar_bridge.AppleScriptError as exc:
                    log.warning("Could not delete orphan event %s: %s", event.cal_uid, exc)
                    summary.errors += 1
                continue

            change = classify_calendar_change(
                record.synced_start_time,
                record.synced_end_time,
                event.start_dt,
                event.end_dt,
            )

            if change == "no_change":
                continue

            # Skip stale snapshot events already recreated by Phase 3
            if record.cal_uid and record.cal_uid != event.cal_uid:
                log.info(
                    "Phase 4: skipping stale snapshot event %s for things_id=%s (DB now points to %s)",
                    event.cal_uid, event.things_id, record.cal_uid,
                )
                continue

            task = active_tasks.get(event.things_id)

            if change == "date_moved":
                new_date = event.start_dt.date()
                log.info(
                    "Calendar moved %s to a different day: %s -> %s; updating Things",
                    event.things_id, record.synced_date, new_date.isoformat(),
                )
                if task is not None:
                    try:
                        things_bridge.set_due_date(event.things_id, new_date)
                        summary.things_updated += 1
                    except things_bridge.AppleScriptError as exc:
                        log.error("Failed to set Things When for %s: %s", event.things_id, exc)
                        summary.errors += 1
                        continue
                db.upsert_record(
                    conn,
                    SyncRecord(
                        things_id=record.things_id, cal_uid=record.cal_uid,
                        things_title=record.things_title,
                        synced_date=new_date.isoformat(),
                        synced_start_time=_iso(event.start_dt),
                        synced_end_time=_iso(event.end_dt),
                        user_pinned=False, pinned_start_time=None, pinned_end_time=None,
                        last_synced_at=_now_iso(),
                        things_mod_at=record.things_mod_at,
                        creation_date=record.creation_date,
                    ),
                )
                summary.affected_dates.add(record.synced_date)
                summary.affected_dates.add(new_date.isoformat())

            elif change == "time_moved":
                # Same-day time OR duration change → pin, leave Things alone
                log.info(
                    "Calendar pinned %s to new slot: %s..%s -> %s..%s",
                    event.things_id,
                    record.synced_start_time, record.synced_end_time,
                    _iso(event.start_dt), _iso(event.end_dt),
                )
                db.set_pinned(conn, record.things_id, True, _iso(event.start_dt), _iso(event.end_dt))
                db.update_times(
                    conn, record.things_id,
                    event.start_dt.date().isoformat(),
                    _iso(event.start_dt), _iso(event.end_dt),
                    _now_iso(),
                )
                summary.pinned += 1
                summary.affected_dates.add(event.start_dt.date().isoformat())

        except Exception:
            log.exception("Error processing calendar event %s", event.cal_uid)
            summary.errors += 1


# ---------------------------------------------------------------------------
# Phase 5: cleanup
# ---------------------------------------------------------------------------


def phase_deletions(conn, active_tasks, summary):
    """Remove events for tasks that dropped out of Today/Upcoming for any reason."""
    for record in db.all_records(conn):
        if record.things_id in active_tasks:
            continue
        status = things_bridge.task_status(record.things_id)
        log.info(
            "Removing event for inactive Things task %s (status=%s, title=%r)",
            record.things_id, status, record.things_title,
        )
        if record.cal_uid:
            try:
                calendar_bridge.delete_event(record.cal_uid)
                summary.deleted += 1
            except calendar_bridge.AppleScriptError as exc:
                log.warning("Could not delete event %s: %s", record.cal_uid, exc)
                summary.errors += 1
        db.delete_record(conn, record.things_id)
        summary.affected_dates.add(record.synced_date)


# ---------------------------------------------------------------------------
# Phase 6: restack affected dates (with ONE bulk busy-slot fetch)
# ---------------------------------------------------------------------------


def _recurring_series(conn) -> list[tuple[str, str, str]]:
    """Cached recurring series, refreshed at most once per RECURRING_CACHE_HOURS."""
    age = db.recurring_series_age_hours(conn)
    if age is not None and age < config.RECURRING_CACHE_HOURS:
        series = db.get_recurring_series(conn)
        log.info("Recurring series: %d from cache (%.1fh old)", len(series), age)
        return series

    try:
        series = calendar_bridge.fetch_recurring_series()
    except calendar_bridge.AppleScriptError as exc:
        # Keep using whatever we had rather than losing gap-avoidance entirely.
        stale = db.get_recurring_series(conn)
        log.warning(
            "Could not refresh recurring series (%s); using %d cached",
            exc, len(stale),
        )
        return stale

    db.replace_recurring_series(conn, series)
    conn.commit()
    log.info("Recurring series: refreshed cache, %d series", len(series))
    return series


def _busy_slots(conn, start_date: date, end_date: date):
    by_date = calendar_bridge.list_oneoff_busy(start_date, end_date)
    oneoff = sum(len(v) for v in by_date.values())
    series = _recurring_series(conn)
    from_series = calendar_bridge.expand_series_into(
        by_date, series, start_date, end_date
    )
    log.info(
        "Busy slots %s..%s: %d one-off + %d from %d recurring series "
        "= %d blocks across %d dates",
        start_date, end_date, oneoff, from_series, len(series),
        oneoff + from_series, len(by_date),
    )
    return by_date


def phase_restack(conn, summary):
    if not summary.affected_dates:
        return

    valid_dates: list[date] = []
    for d_iso in summary.affected_dates:
        try:
            valid_dates.append(datetime.strptime(d_iso, "%Y-%m-%d").date())
        except ValueError:
            log.warning("Skipping invalid affected date %r", d_iso)

    if not valid_dates:
        return

    # One busy-slot fetch across all affected dates
    busy_by_date = _busy_slots(conn, min(valid_dates), max(valid_dates))

    for target_date in sorted(valid_dates):
        records = db.records_for_date(conn, target_date.isoformat())
        if not records:
            continue

        pinned_map: dict[str, tuple[datetime, datetime]] = {}
        for r in records:
            if r.user_pinned and r.pinned_start_time and r.pinned_end_time:
                try:
                    pinned_map[r.things_id] = (
                        datetime.strptime(r.pinned_start_time, "%Y-%m-%dT%H:%M:%S"),
                        datetime.strptime(r.pinned_end_time, "%Y-%m-%dT%H:%M:%S"),
                    )
                except ValueError:
                    log.warning("Bad pinned time on %s; ignoring pin", r.things_id)

        def sort_key(r):
            try:
                cd = datetime.fromisoformat(r.creation_date) if r.creation_date else datetime.max
            except ValueError:
                cd = datetime.max
            return (cd, r.things_id)

        records_sorted = sorted(records, key=sort_key)
        synthetic_tasks = [
            ThingsTask(
                things_id=r.things_id, title=r.things_title,
                when_date=target_date, deadline_date=None,
                modification_date=None,
                creation_date=(datetime.fromisoformat(r.creation_date) if r.creation_date else None),
                in_today=False,
            )
            for r in records_sorted
        ]

        external = busy_by_date.get(target_date, [])
        assignments = assign_slots(target_date, synthetic_tasks, external, pinned_map)

        for r in records_sorted:
            new_start, new_end = assignments[r.things_id]
            if (_iso(new_start) == r.synced_start_time
                    and _iso(new_end) == r.synced_end_time):
                continue
            if not r.cal_uid:
                continue
            try:
                calendar_bridge.update_event_times(r.cal_uid, new_start, new_end)
                db.update_times(
                    conn, r.things_id,
                    target_date.isoformat(), _iso(new_start), _iso(new_end),
                    _now_iso(),
                )
                summary.restacked += 1
            except calendar_bridge.AppleScriptError as exc:
                log.warning("Could not restack %s: %s", r.cal_uid, exc)
                summary.errors += 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_sync() -> int:
    summary = SyncSummary()
    db.init_db()

    try:
        calendar_bridge.activate_and_wait()
        calendar_bridge.ensure_calendar_exists()
    except calendar_bridge.AppleScriptError as exc:
        log.error("Could not activate Calendar.app: %s", exc)
        return 1

    with FileLock(config.LOCK_PATH) as _lock, db.connect() as conn:
        run_id = db.start_sync_run(conn)
        try:
            log.info("Sync run %d started", run_id)

            cal_events = calendar_bridge.list_events()
            cal_events, dup_deleted = _dedupe_events(conn, cal_events, summary)
            cal_by_things_id = {e.things_id: e for e in cal_events if e.things_id}
            log.info(
                "Calendar snapshot: %d events (%d linked to Things, %d duplicates deleted)",
                len(cal_events), len(cal_by_things_id), dup_deleted,
            )

            tasks = things_bridge.list_active_tasks()
            active_by_id = {t.things_id: t for t in tasks}
            log.info("Things snapshot: %d active tasks", len(tasks))

            phase_things_to_calendar(conn, active_by_id, cal_by_things_id, summary)
            conn.commit()

            phase_calendar_to_things(conn, active_by_id, cal_events, summary)
            conn.commit()

            phase_deletions(conn, active_by_id, summary)
            conn.commit()

            phase_restack(conn, summary)
            conn.commit()

            log.info("Sync run %d done: %s", run_id, summary.as_text())
            db.finish_sync_run(conn, run_id, "ok", summary.as_text())
            return 0 if summary.errors == 0 else 2

        except Exception:
            log.exception("Sync run %d crashed", run_id)
            db.finish_sync_run(conn, run_id, "error", "exception")
            return 1


def run_test() -> int:
    print("=== Things tasks ===", flush=True)
    try:
        for t in things_bridge.list_active_tasks():
            print(t)
    except things_bridge.AppleScriptError as exc:
        print(f"ERROR reading Things: {exc}", file=sys.stderr)
        return 1
    print("\n=== Calendar 'Things Tasks' events ===", flush=True)
    try:
        calendar_bridge.activate_and_wait()
        calendar_bridge.ensure_calendar_exists()
        for e in calendar_bridge.list_events():
            print(e)
    except calendar_bridge.AppleScriptError as exc:
        print(f"ERROR reading Calendar: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Things 3 <-> Calendar.app sync")
    parser.add_argument("--test", action="store_true", help="Read-only debug dump")
    args = parser.parse_args()

    setup_logging()

    if args.test:
        return run_test()

    try:
        return run_sync()
    except LockBusy as exc:
        log.warning("Another sync is in progress: %s", exc)
        return 0


if __name__ == "__main__":
    sys.exit(main())
