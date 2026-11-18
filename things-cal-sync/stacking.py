"""Time-slot assignment.

Lay out the day's tasks into 30-minute slots between START_HOUR and END_HOUR,
skipping slots that overlap pinned tasks or external (non-Things) calendar
events. Tasks that no longer fit in the working window are queued after
END_HOUR, and never cross midnight into the next day.
"""
from datetime import date, datetime, timedelta

import config
from models import ThingsTask


def _overlaps(a: tuple[datetime, datetime], b: tuple[datetime, datetime]) -> bool:
    return a[0] < b[1] and a[1] > b[0]


def _conflicts(slot, existing) -> bool:
    return any(_overlaps(slot, ex) for ex in existing)


def assign_slots(
    target_date: date,
    tasks_for_date: list[ThingsTask],
    existing_slots: list[tuple[datetime, datetime]],
    pinned_map: dict[str, tuple[datetime, datetime]],
    start_hour: int = config.START_HOUR,
    slot_minutes: int = config.SLOT_MINUTES,
    end_hour: int = config.END_HOUR,
) -> dict[str, tuple[datetime, datetime]]:
    """Place tasks in the order given; earlier in the list means earlier in the day.

    Each task takes its own duration_minutes (default slot_minutes).
    """
    blocked = list(existing_slots)
    for _tid, slot in pinned_map.items():
        if not any(_overlaps(slot, b) for b in blocked):
            blocked.append(slot)

    step = timedelta(minutes=slot_minutes)
    day = datetime(target_date.year, target_date.month, target_date.day)
    work_start = day.replace(hour=start_hour)
    work_end = day.replace(hour=end_hour)
    midnight = day + timedelta(days=1)

    cursor = work_start
    overflow_cursor = work_end

    assignments: dict[str, tuple[datetime, datetime]] = {}

    for task in tasks_for_date:
        if task.things_id in pinned_map:
            assignments[task.things_id] = pinned_map[task.things_id]
            continue

        duration = timedelta(minutes=getattr(task, "duration_minutes", None) or slot_minutes)
        # Probe in steps of the slot length, or the task's own length if shorter,
        # so a 15-minute task can sit in the quarter hour after another one.
        probe_step = min(step, duration)

        slot = None
        probe = cursor
        while probe + duration <= work_end:
            candidate = (probe, probe + duration)
            if not _conflicts(candidate, blocked):
                slot = candidate
                cursor = probe + duration
                break
            probe += probe_step

        if slot is None:
            # Working window is full. Queue after END_HOUR rather than hunting
            # for gaps at night, and never spill past midnight -- an event on
            # the following calendar day would contradict its synced_date.
            start = min(overflow_cursor, midnight - duration)
            slot = (start, start + duration)
            overflow_cursor = min(start + duration, midnight - step)

        assignments[task.things_id] = slot
        blocked.append(slot)

    return assignments
