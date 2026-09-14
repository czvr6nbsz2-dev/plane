"""AppleScript wrappers for talking to Things 3."""
import logging
import subprocess
from datetime import date, datetime
from typing import Optional

import config
from models import ThingsTask

log = logging.getLogger(__name__)


class AppleScriptError(RuntimeError):
    pass


def _run_osascript(script: str) -> str:
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=config.OSASCRIPT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppleScriptError(f"osascript timed out after {config.OSASCRIPT_TIMEOUT}s") from exc
    if proc.returncode != 0:
        raise AppleScriptError(
            f"osascript failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.rstrip("\n")


_LIST_TASKS_SCRIPT = r'''
on isoDate(d)
    if d is missing value then return ""
    set y to year of d as integer
    set m to month of d as integer
    set dy to day of d as integer
    set yStr to y as string
    set mStr to text -2 thru -1 of ("0" & m as string)
    set dStr to text -2 thru -1 of ("0" & dy as string)
    return yStr & "-" & mStr & "-" & dStr
end isoDate

on isoDateTime(d)
    if d is missing value then return ""
    set y to year of d as integer
    set m to month of d as integer
    set dy to day of d as integer
    set h to hours of d
    set mn to minutes of d
    set sc to seconds of d
    set yStr to y as string
    set mStr to text -2 thru -1 of ("0" & m as string)
    set dStr to text -2 thru -1 of ("0" & dy as string)
    set hStr to text -2 thru -1 of ("0" & h as string)
    set nStr to text -2 thru -1 of ("0" & mn as string)
    set sStr to text -2 thru -1 of ("0" & sc as string)
    return yStr & "-" & mStr & "-" & dStr & "T" & hStr & ":" & nStr & ":" & sStr
end isoDateTime

set output to ""
set seenIds to {}

tell application "Things3"
    try
        set todayTodos to to dos of list "Today"
    on error
        set todayTodos to {}
    end try
    repeat with t in todayTodos
        try
            if status of t is open then
                set tid to id of t
                if tid is not in seenIds then
                    set end of seenIds to tid
                    set tname to name of t
                    set wdate to ""
                    try
                        if activation date of t is not missing value then
                            set wdate to my isoDate(activation date of t)
                        end if
                    end try
                    set ddate to ""
                    try
                        if due date of t is not missing value then
                            set ddate to my isoDate(due date of t)
                        end if
                    end try
                    set mdate to ""
                    try
                        if modification date of t is not missing value then
                            set mdate to my isoDateTime(modification date of t)
                        end if
                    end try
                    set cdate to ""
                    try
                        if creation date of t is not missing value then
                            set cdate to my isoDateTime(creation date of t)
                        end if
                    end try
                    set output to output & tid & tab & tname & tab & wdate & tab & ddate & tab & mdate & tab & cdate & tab & "1" & linefeed
                end if
            end if
        end try
    end repeat

    try
        set upcomingTodos to to dos of list "Upcoming"
    on error
        set upcomingTodos to {}
    end try
    repeat with t in upcomingTodos
        try
            if status of t is open then
                set tid to id of t
                if tid is not in seenIds then
                    set hasSchedule to false
                    try
                        if activation date of t is not missing value then set hasSchedule to true
                    end try
                    try
                        if due date of t is not missing value then set hasSchedule to true
                    end try
                    if hasSchedule then
                        set end of seenIds to tid
                        set tname to name of t
                        set wdate to ""
                        try
                            if activation date of t is not missing value then
                                set wdate to my isoDate(activation date of t)
                            end if
                        end try
                        set ddate to ""
                        try
                            if due date of t is not missing value then
                                set ddate to my isoDate(due date of t)
                            end if
                        end try
                        set mdate to ""
                        try
                            if modification date of t is not missing value then
                                set mdate to my isoDateTime(modification date of t)
                            end if
                        end try
                        set cdate to ""
                        try
                            if creation date of t is not missing value then
                                set cdate to my isoDateTime(creation date of t)
                            end if
                        end try
                        set output to output & tid & tab & tname & tab & wdate & tab & ddate & tab & mdate & tab & cdate & tab & "0" & linefeed
                    end if
                end if
            end if
        end try
    end repeat
end tell

return output
'''


_SCHEDULE_SCRIPT_TEMPLATE = r'''
tell application "Things3"
    set theTodo to first to do whose id is "{tid}"
    set d to current date
    set year of d to {y}
    set month of d to {m}
    set day of d to {dy}
    set time of d to 12 * 3600
    schedule theTodo for d
end tell
'''


_CLEAR_DUE_DATE_SCRIPT_TEMPLATE = r'''
tell application "Things3"
    set theTodo to first to do whose id is "{tid}"
    set due date of theTodo to missing value
end tell
'''


_TASK_STATUS_SCRIPT_TEMPLATE = r'''
tell application "Things3"
    try
        set theTodo to first to do whose id is "{tid}"
        return (status of theTodo) as string
    on error
        return "missing"
    end try
end tell
'''


def _parse_iso_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        log.warning("Could not parse date %r from Things", s)
        return None


def _parse_iso_datetime(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        log.warning("Could not parse datetime %r from Things", s)
        return None


def list_active_tasks() -> list[ThingsTask]:
    raw = _run_osascript(_LIST_TASKS_SCRIPT)
    tasks: list[ThingsTask] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            log.warning("Skipping malformed Things row: %r", line)
            continue
        tid, name, wdate, ddate, mdate, cdate, in_today = parts[:7]
        tasks.append(
            ThingsTask(
                things_id=tid,
                title=name,
                when_date=_parse_iso_date(wdate),
                deadline_date=_parse_iso_date(ddate),
                modification_date=_parse_iso_datetime(mdate),
                creation_date=_parse_iso_datetime(cdate),
                in_today=(in_today == "1"),
            )
        )
    return tasks


def set_due_date(things_id: str, target: date) -> None:
    """Set Things' 'When' (activation date) via the schedule command.

    Kept named set_due_date for backwards compat with callers, but this
    updates the When date (schedule), not the Deadline.
    """
    script = _SCHEDULE_SCRIPT_TEMPLATE.format(
        tid=_escape(things_id), y=target.year, m=target.month, dy=target.day
    )
    _run_osascript(script)
    log.info("Things: scheduled %s for %s", things_id, target.isoformat())


def clear_due_date(things_id):
    script = _CLEAR_DUE_DATE_SCRIPT_TEMPLATE.format(tid=_escape(things_id))
    _run_osascript(script)
    log.info("Things: cleared due date of %s", things_id)


def task_status(things_id: str) -> str:
    script = _TASK_STATUS_SCRIPT_TEMPLATE.format(tid=_escape(things_id))
    try:
        result = _run_osascript(script).strip()
    except AppleScriptError as exc:
        log.warning("task_status(%s) failed: %s", things_id, exc)
        return "missing"
    return result or "missing"


def _escape(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')
