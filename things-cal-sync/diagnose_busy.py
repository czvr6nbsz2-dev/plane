#!/usr/bin/env python3
"""One-off diagnostic: what does Calendar.app actually report as busy on a day?

Usage:
    python3 diagnose_busy.py 2026-09-23

Prints every event Calendar.app returns for that date from all calendars
except the Things one, including whether it is part of a recurring series.
Compare the output against what you see in Calendar.app for that day: any
meeting visible in the app but missing here is a meeting the stacker cannot
see, and will happily schedule tasks on top of.
"""
import sys
from datetime import datetime

import config
import calendar_bridge


_RAW_TEMPLATE = r'''
on isoDateTime(d)
    if d is missing value then return ""
    set y to year of d as integer
    set m to month of d as integer
    set dy to day of d as integer
    set h to hours of d
    set mn to minutes of d
    set yStr to y as string
    set mStr to text -2 thru -1 of ("0" & m as string)
    set dStr to text -2 thru -1 of ("0" & dy as string)
    set hStr to text -2 thru -1 of ("0" & h as string)
    set nStr to text -2 thru -1 of ("0" & mn as string)
    return yStr & "-" & mStr & "-" & dStr & " " & hStr & ":" & nStr
end isoDateTime

set startWindow to current date
set year of startWindow to {sy}
set month of startWindow to {sm}
set day of startWindow to {sd}
set time of startWindow to 0

set endWindow to current date
set year of endWindow to {sy}
set month of endWindow to {sm}
set day of endWindow to {sd}
set time of endWindow to 23 * 3600 + 59 * 60 + 59

set output to ""
tell application "Calendar"
    repeat with c in calendars
        try
            set cname to name of c
        on error
            set cname to "?"
        end try
        if cname is not "{cal}" then
            try
                set theEvents to (every event of c whose start date is greater than or equal to startWindow and start date is less than or equal to endWindow)
                repeat with e in theEvents
                    try
                        set esum to summary of e
                        set sStr to my isoDateTime(start date of e)
                        set eStr to my isoDateTime(end date of e)
                        set rec to ""
                        try
                            if recurrence of e is not missing value then set rec to recurrence of e as string
                        end try
                        set output to output & cname & tab & esum & tab & sStr & tab & eStr & tab & rec & linefeed
                    end try
                end repeat
            end try
        end if
    end repeat
end tell
return output
'''


def main() -> int:
    if len(sys.argv) < 2:
        print("Gebruik: python3 diagnose_busy.py YYYY-MM-DD")
        return 1
    try:
        target = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
    except ValueError:
        print("Datum moet als YYYY-MM-DD, bijv. 2026-09-23")
        return 1

    calendar_bridge.activate_and_wait()

    print(f"\n=== Wat Agenda.app teruggeeft voor {target} ===\n")
    script = _RAW_TEMPLATE.format(
        sy=target.year, sm=target.month, sd=target.day,
        cal=calendar_bridge._escape(config.CALENDAR_NAME),
    )
    try:
        raw = calendar_bridge._run_osascript(script, timeout=90)
    except calendar_bridge.AppleScriptError as exc:
        print(f"FOUT: {exc}")
        return 1

    rows = [ln for ln in raw.splitlines() if ln.strip()]
    if not rows:
        print("  (niets gevonden)")
    for line in rows:
        parts = line.split("\t")
        while len(parts) < 5:
            parts.append("")
        cal_name, summary, start, end, recurrence = parts[:5]
        herhaal = f"   [HERHALEND: {recurrence}]" if recurrence else ""
        print(f"  {start} - {end[-5:]}  {summary}")
        print(f"      agenda: {cal_name}{herhaal}")

    print(f"\n=== Wat het sync-script als 'bezet' ziet op {target} ===\n")
    busy = calendar_bridge.list_external_busy_slots_range(target, target)
    slots = busy.get(target, [])
    if not slots:
        print("  GEEN bezette slots -> taken worden vanaf 08:00 ingepland,")
        print("  dwars over eventuele vergaderingen heen.")
    for s, e in sorted(slots):
        print(f"  {s.strftime('%H:%M')} - {e.strftime('%H:%M')}")

    print(f"\n  Totaal: {len(rows)} events gevonden, {len(slots)} als bezet geteld.")
    print("\nVergelijk dit met wat je in Agenda.app op die dag ziet staan.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
