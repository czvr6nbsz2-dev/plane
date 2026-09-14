"""Conflict-resolution helpers used by sync.py."""
from datetime import datetime
from typing import Literal


ChangeKind = Literal["no_change", "time_moved", "date_moved"]


def _parse_iso(s: str) -> datetime:
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return datetime.fromisoformat(s)


def classify_calendar_change(
    synced_start_iso: str,
    synced_end_iso: str,
    current_start_dt: datetime,
    current_end_dt: datetime,
) -> ChangeKind:
    """Compare stored start/end against what Calendar.app currently reports.

    Returns:
        "date_moved"  - event is on a different day than we last synced
        "time_moved"  - same day, but user changed the start time or duration
        "no_change"   - within tolerance of what we wrote
    """
    synced_start = _parse_iso(synced_start_iso)
    synced_end = _parse_iso(synced_end_iso)
    if synced_start.date() != current_start_dt.date():
        return "date_moved"

    start_changed = (
        synced_start.hour != current_start_dt.hour
        or synced_start.minute != current_start_dt.minute
    )
    synced_duration = (synced_end - synced_start).total_seconds()
    current_duration = (current_end_dt - current_start_dt).total_seconds()
    # 60 second tolerance for sub-second drift when Calendar round-trips events
    duration_changed = abs(current_duration - synced_duration) > 60

    if start_changed or duration_changed:
        return "time_moved"
    return "no_change"
