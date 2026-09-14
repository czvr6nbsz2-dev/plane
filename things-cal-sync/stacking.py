"""Time-slot assignment.

Lay out the day's tasks into 30-minute slots starting at START_HOUR, skipping
slots that overlap pinned tasks or external (non-Things) calendar events.
"""
from datetime import date, datetime, timedelta
from typing import Iterable

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
) -> dict[str, tuple[datetime, datetime]]:
    blocked = list(existing_slots)
    for tid, slot in pinned_map.items():
        if not any(_overlaps(slot, b) for b in blocked):
            blocked.append(slot)

    cursor = datetime(target_date.year, target_date.month, target_date.day, start_hour, 0)
    step = timedelta(minutes=slot_minutes)
    day_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59)

    assignments: dict[str, tuple[datetime, datetime]] = {}

    for task in tasks_for_date:
        if task.things_id in pinned_map:
            assignments[task.things_id] = pinned_map[task.things_id]
            continue

        while True:
            slot = (cursor, cursor + step)
            if slot[1] > day_end:
                break
            if not _conflicts(slot, blocked):
                break
            cursor += step

        slot = (cursor, cursor + step)
        assignments[task.things_id] = slot
        blocked.append(slot)
        cursor += step

    return assignments
