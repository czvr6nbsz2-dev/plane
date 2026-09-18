#!/usr/bin/env python3
"""Diagnostic 2: can we retrieve every recurring series from Calendar.app?

Diagnostic 1 showed that a recurring meeting comes back as a single master
event carrying its RRULE, dated at the first occurrence -- and that at least
one recurring meeting did not come back at all. Before building an RRULE
expander we need to know whether a query with no date filter surfaces every
series, and how slow that is.

Usage:
    python3 diagnose_recurring.py
"""
import sys
import time

import config
import calendar_bridge


_RECURRING_TEMPLATE = r'''
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

set output to ""
tell application "Calendar"
    repeat with c in calendars
        try
            set cname to name of c
        on error
            set cname to "?"
        end try
        if cname is not "{cal}" then
            set total to 0
            try
                set total to count of events of c
            end try
            set output to output & "CAL" & tab & cname & tab & (total as string) & linefeed
            try
                set recEvents to (every event of c whose recurrence is not missing value)
                repeat with e in recEvents
                    try
                        set esum to summary of e
                        set sStr to my isoDateTime(start date of e)
                        set eStr to my isoDateTime(end date of e)
                        set rec to recurrence of e as string
                        set stat to ""
                        try
                            set stat to (status of e) as string
                        end try
                        set output to output & "REC" & tab & cname & tab & esum & tab & sStr & tab & eStr & tab & rec & tab & stat & linefeed
                    end try
                end repeat
            on error errMsg
                set output to output & "ERR" & tab & cname & tab & errMsg & linefeed
            end try
        end if
    end repeat
end tell
return output
'''


def main() -> int:
    calendar_bridge.activate_and_wait()

    script = _RECURRING_TEMPLATE.format(
        cal=calendar_bridge._escape(config.CALENDAR_NAME)
    )

    print("\nAgenda.app uitvragen (kan even duren)...\n")
    started = time.monotonic()
    try:
        raw = calendar_bridge._run_osascript(script, timeout=300)
    except calendar_bridge.AppleScriptError as exc:
        elapsed = time.monotonic() - started
        print(f"MISLUKT na {elapsed:.0f}s: {exc}")
        print("\n-> Alles in een keer ophalen is te traag; we hebben een")
        print("   andere aanpak nodig dan AppleScript.")
        return 1
    elapsed = time.monotonic() - started

    calendars: list[tuple[str, str]] = []
    recurring: list[list[str]] = []
    errors: list[tuple[str, str]] = []

    for line in raw.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        kind = parts[0]
        if kind == "CAL" and len(parts) >= 3:
            calendars.append((parts[1], parts[2]))
        elif kind == "REC" and len(parts) >= 6:
            recurring.append(parts[1:])
        elif kind == "ERR" and len(parts) >= 3:
            errors.append((parts[1], parts[2]))

    print(f"=== Klaar in {elapsed:.0f} seconden ===\n")

    print("Agenda's en hun totale aantal afspraken:")
    for name, total in calendars:
        print(f"  {total:>6}  {name}")
    print()

    if errors:
        print("Agenda's die een fout gaven (worden dus overgeslagen):")
        for name, msg in errors:
            print(f"  {name}: {msg}")
        print()

    print(f"Herhalende series gevonden: {len(recurring)}\n")
    for row in recurring:
        while len(row) < 6:
            row.append("")
        cal_name, summary, start, _end, rule, status = row[:6]
        extra = f"  (status: {status})" if status and status != "none" else ""
        print(f"  {summary}{extra}")
        print(f"      eerste: {start}   agenda: {cal_name}")
        print(f"      regel : {rule}")
    print()

    total_events = sum(int(t) for _n, t in calendars if t.isdigit())
    print(f"Totaal {total_events} afspraken over {len(calendars)} agenda's.")
    print("\nControleer: staat je 2-wekelijkse woensdagafspraak hierboven?\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
