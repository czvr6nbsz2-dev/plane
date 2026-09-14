"""AppleScript wrappers for Calendar.app."""
import logging
import re
import subprocess
from datetime import date, datetime, timedelta
from typing import Optional

import config
from models import CalEvent

log = logging.getLogger(__name__)


class AppleScriptError(RuntimeError):
    pass


def _run_osascript(script: str, timeout: Optional[int] = None) -> str:
    t = timeout if timeout is not None else config.OSASCRIPT_TIMEOUT
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=t,
        )
    except subprocess.TimeoutExpired as exc:
        raise AppleScriptError(f"osascript timed out after {t}s") from exc
    if proc.returncode != 0:
        raise AppleScriptError(
            f"osascript failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.rstrip("\n")


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------------------
# Setup / activation
# ---------------------------------------------------------------------------


def activate_and_wait() -> None:
    """Launch Calendar.app in the background and wait 1 second so subsequent scripts work.

    Uses `open -gj -a Calendar` (background, hidden) rather than AppleScript's
    `launch` because the latter races: when Calendar isn't running at all,
    sending a `tell application "Calendar" to launch` event errors with
    "App is niet actief" (-600) before the app has come up.
    """
    import time
    proc = subprocess.run(
        ["open", "-gj", "-a", "Calendar"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if proc.returncode != 0:
        raise AppleScriptError(
            f"`open -gj -a Calendar` failed (rc={proc.returncode}): {proc.stderr.strip()}"
        )
    time.sleep(1)


def ensure_calendar_exists(name: str = config.CALENDAR_NAME) -> None:
    script = f'''
tell application "Calendar"
    if not (exists calendar "{_escape(name)}") then
        make new calendar with properties {{name:"{_escape(name)}"}}
    end if
end tell
'''
    _run_osascript(script)
    log.info("Ensured calendar %r exists", name)


# ---------------------------------------------------------------------------
# Read events from the Things Tasks calendar
# ---------------------------------------------------------------------------

_LIST_EVENTS_SCRIPT_TEMPLATE = r'''
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

set startWindow to current date
set year of startWindow to {sy}
set month of startWindow to {sm}
set day of startWindow to {sd}
set time of startWindow to 0

set endWindow to current date
set year of endWindow to {ey}
set month of endWindow to {em}
set day of endWindow to {ed}
set time of endWindow to 23 * 3600 + 59 * 60 + 59

set output to ""
tell application "Calendar"
    tell calendar "{cal}"
        set theEvents to (every event whose start date is greater than or equal to startWindow and start date is less than or equal to endWindow)
        repeat with e in theEvents
            try
                set euid to uid of e
                set esum to summary of e
                set sStr to my isoDateTime(start date of e)
                set eStr to my isoDateTime(end date of e)
                set edesc to ""
                try
                    set edesc to description of e
                end try
                if edesc is missing value then set edesc to ""
                set AppleScript's text item delimiters to (ASCII character 9)
                set parts to text items of edesc
                set AppleScript's text item delimiters to " "
                set edesc to parts as string
                set AppleScript's text item delimiters to (ASCII character 10)
                set parts to text items of edesc
                set AppleScript's text item delimiters to "|LF|"
                set edesc to parts as string
                set AppleScript's text item delimiters to (ASCII character 13)
                set parts to text items of edesc
                set AppleScript's text item delimiters to "|LF|"
                set edesc to parts as string
                set AppleScript's text item delimiters to ""
                set output to output & euid & tab & esum & tab & sStr & tab & eStr & tab & edesc & linefeed
            end try
        end repeat
    end tell
end tell
return output
'''


def list_events(
    lookback_days: int = config.LOOKBACK_DAYS,
    lookahead_days: int = config.LOOKAHEAD_DAYS,
    calendar_name: str = config.CALENDAR_NAME,
) -> list[CalEvent]:
    today = date.today()
    start = today - timedelta(days=lookback_days)
    end = today + timedelta(days=lookahead_days)
    script = _LIST_EVENTS_SCRIPT_TEMPLATE.format(
        sy=start.year, sm=start.month, sd=start.day,
        ey=end.year, em=end.month, ed=end.day,
        cal=_escape(calendar_name),
    )
    # Generous timeout: with a 2-year lookahead and a calendar that may have
    # accumulated duplicates, the AppleScript query can take a while.
    raw = _run_osascript(script, timeout=180)
    events: list[CalEvent] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            log.warning("Skipping malformed Calendar row: %r", line)
            continue
        cal_uid = parts[0]
        title = parts[1]
        start_iso = parts[2]
        end_iso = parts[3]
        desc = parts[4] if len(parts) >= 5 else ""
        desc = desc.replace("|LF|", "\n")
        try:
            start_dt = datetime.strptime(start_iso, "%Y-%m-%dT%H:%M:%S")
            end_dt = datetime.strptime(end_iso, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            log.warning("Bad datetime in Calendar row %r", line)
            continue
        events.append(
            CalEvent(
                cal_uid=cal_uid,
                title=title,
                start_dt=start_dt,
                end_dt=end_dt,
                description=desc,
                things_id=parse_things_id(desc),
            )
        )
    return events


_THINGS_ID_RE = re.compile(
    rf"^\s*{re.escape(config.THINGS_ID_MARKER)}\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def parse_things_id(description: str) -> Optional[str]:
    if not description:
        return None
    match = _THINGS_ID_RE.search(description)
    if match:
        return match.group(1)
    return None


def make_description(things_id: str, extra: str = "") -> str:
    line = f"{config.THINGS_ID_MARKER} {things_id}"
    if extra:
        return f"{line}\n{extra}"
    return line


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------

_CREATE_EVENT_SCRIPT_TEMPLATE = r'''
tell application "Calendar"
    tell calendar "{cal}"
        set sd to current date
        set year of sd to {sy}
        set month of sd to {sm}
        set day of sd to {sdd}
        set time of sd to {sh} * 3600 + {smn} * 60

        set ed to current date
        set year of ed to {ey}
        set month of ed to {em}
        set day of ed to {edd}
        set time of ed to {eh} * 3600 + {emn} * 60

        set newEvent to make new event with properties {{summary:"{title}", start date:sd, end date:ed, description:"{desc}"}}
        return uid of newEvent
    end tell
end tell
'''


def create_event(title, start_dt, end_dt, things_id, calendar_name=config.CALENDAR_NAME):
    desc = make_description(things_id)
    script = _CREATE_EVENT_SCRIPT_TEMPLATE.format(
        cal=_escape(calendar_name),
        sy=start_dt.year, sm=start_dt.month, sdd=start_dt.day,
        sh=start_dt.hour, smn=start_dt.minute,
        ey=end_dt.year, em=end_dt.month, edd=end_dt.day,
        eh=end_dt.hour, emn=end_dt.minute,
        title=_escape(title),
        desc=_escape(desc),
    )
    uid = _run_osascript(script).strip()
    log.info("Calendar: created event %s for things_id=%s", uid, things_id)
    return uid


_UPDATE_TIMES_SCRIPT_TEMPLATE = r'''
tell application "Calendar"
    tell calendar "{cal}"
        set theEvent to first event whose uid is "{uid}"
        set sd to current date
        set year of sd to {sy}
        set month of sd to {sm}
        set day of sd to {sdd}
        set time of sd to {sh} * 3600 + {smn} * 60

        set ed to current date
        set year of ed to {ey}
        set month of ed to {em}
        set day of ed to {edd}
        set time of ed to {eh} * 3600 + {emn} * 60

        set oldEnd to end date of theEvent
        if sd > oldEnd then
            set end date of theEvent to ed
            set start date of theEvent to sd
        else
            set start date of theEvent to sd
            set end date of theEvent to ed
        end if
    end tell
end tell
'''


def update_event_times(cal_uid, start_dt, end_dt, calendar_name=config.CALENDAR_NAME):
    script = _UPDATE_TIMES_SCRIPT_TEMPLATE.format(
        cal=_escape(calendar_name),
        uid=_escape(cal_uid),
        sy=start_dt.year, sm=start_dt.month, sdd=start_dt.day,
        sh=start_dt.hour, smn=start_dt.minute,
        ey=end_dt.year, em=end_dt.month, edd=end_dt.day,
        eh=end_dt.hour, emn=end_dt.minute,
    )
    _run_osascript(script)
    log.info("Calendar: updated event %s -> %s..%s", cal_uid, start_dt, end_dt)


_UPDATE_TITLE_SCRIPT_TEMPLATE = r'''
tell application "Calendar"
    tell calendar "{cal}"
        set theEvent to first event whose uid is "{uid}"
        set summary of theEvent to "{title}"
    end tell
end tell
'''


def update_event_title(cal_uid, title, calendar_name=config.CALENDAR_NAME):
    script = _UPDATE_TITLE_SCRIPT_TEMPLATE.format(
        cal=_escape(calendar_name),
        uid=_escape(cal_uid),
        title=_escape(title),
    )
    _run_osascript(script)
    log.info("Calendar: updated title of %s -> %r", cal_uid, title)


_DELETE_EVENT_SCRIPT_TEMPLATE = r'''
tell application "Calendar"
    tell calendar "{cal}"
        try
            delete (first event whose uid is "{uid}")
        end try
    end tell
end tell
'''


def delete_event(cal_uid, calendar_name=config.CALENDAR_NAME):
    script = _DELETE_EVENT_SCRIPT_TEMPLATE.format(
        cal=_escape(calendar_name),
        uid=_escape(cal_uid),
    )
    _run_osascript(script)
    log.info("Calendar: deleted event %s", cal_uid)


# ---------------------------------------------------------------------------
# Read external busy slots (all calendars EXCEPT the Things Tasks one)
# ---------------------------------------------------------------------------
#
# Called at the start of Phase 6 with the full set of affected dates. Doing
# one AppleScript call across the whole range is dramatically faster than
# per-date calls on a heavy Calendar setup (Exchange + iCloud + personal
# calendars), which was timing out constantly.

_LIST_BUSY_RANGE_TEMPLATE = r'''
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

set startWindow to current date
set year of startWindow to {sy}
set month of startWindow to {sm}
set day of startWindow to {sd}
set time of startWindow to 0

set endWindow to current date
set year of endWindow to {ey}
set month of endWindow to {em}
set day of endWindow to {ed}
set time of endWindow to 23 * 3600 + 59 * 60 + 59

set output to ""
tell application "Calendar"
    repeat with c in calendars
        try
            set cname to name of c
        on error
            set cname to ""
        end try
        if cname is not "{cal}" then
            try
                set theEvents to (every event of c whose start date is greater than or equal to startWindow and start date is less than or equal to endWindow)
                repeat with e in theEvents
                    try
                        set sStr to my isoDateTime(start date of e)
                        set eStr to my isoDateTime(end date of e)
                        set output to output & sStr & tab & eStr & linefeed
                    end try
                end repeat
            end try
        end if
    end repeat
end tell
return output
'''


def list_external_busy_slots_range(
    start_date: date,
    end_date: date,
    calendar_name: str = config.CALENDAR_NAME,
) -> dict[date, list[tuple[datetime, datetime]]]:
    """Return {date: [(start_dt, end_dt), ...]} for all events in any calendar
    other than the Things Tasks one, between start_date and end_date (inclusive).

    One AppleScript call for the whole range — much faster than one-per-date.
    Returns an empty dict on timeout/failure so the sync continues (tasks will
    stack from START_HOUR without gap-avoidance, but nothing crashes).
    """
    script = _LIST_BUSY_RANGE_TEMPLATE.format(
        sy=start_date.year, sm=start_date.month, sd=start_date.day,
        ey=end_date.year, em=end_date.month, ed=end_date.day,
        cal=_escape(calendar_name),
    )
    try:
        # 90s covers a heavy Exchange + iCloud + personal-calendar setup
        # scanning several months of events.
        raw = _run_osascript(script, timeout=90)
    except AppleScriptError as exc:
        log.warning(
            "Could not read external busy slots (%s..%s): %s",
            start_date, end_date, exc,
        )
        return {}

    by_date: dict[date, list[tuple[datetime, datetime]]] = {}
    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        try:
            sdt = datetime.strptime(parts[0], "%Y-%m-%dT%H:%M:%S")
            edt = datetime.strptime(parts[1], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
        by_date.setdefault(sdt.date(), []).append((sdt, edt))
    log.info(
        "External busy slots: %d events across %s..%s (%d dates)",
        sum(len(v) for v in by_date.values()), start_date, end_date, len(by_date),
    )
    return by_date


# Kept for callers that still want a per-date lookup (unused by sync.py now).
def list_external_busy_slots(target_date: date, calendar_name: str = config.CALENDAR_NAME):
    return list_external_busy_slots_range(target_date, target_date, calendar_name).get(target_date, [])
