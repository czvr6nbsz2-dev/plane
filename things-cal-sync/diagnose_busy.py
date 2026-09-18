#!/usr/bin/env python3
"""Measure and inspect the busy-slot lookup for one date.

Times the two halves separately, because they have very different costs:
the one-off query is date-filtered and cheap, while finding recurring series
has to scan every event in the busy calendars.

Running this also fills the recurring-series cache, so the next real sync
picks it up instead of paying for the scan again.

Usage:
    python3 diagnose_busy.py 2026-09-23
"""
import sys
import time
from datetime import datetime

import calendar_bridge
import config
import db


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
    db.init_db()

    print(f"\nBezet-agenda's: {', '.join(config.BUSY_CALENDARS)}\n")

    # --- losse afspraken -----------------------------------------------
    print("1. Losse afspraken ophalen (datumgefilterd)...", flush=True)
    started = time.monotonic()
    by_date = calendar_bridge.list_oneoff_busy(target, target)
    oneoff_seconds = time.monotonic() - started
    oneoff = by_date.get(target, [])
    print(f"   {len(oneoff)} gevonden in {oneoff_seconds:.0f}s")
    for s, e in sorted(oneoff):
        print(f"      {s.strftime('%H:%M')} - {e.strftime('%H:%M')}")

    # --- herhalende series ---------------------------------------------
    with db.connect() as conn:
        age = db.recurring_series_age_hours(conn)
        if age is not None and age < config.RECURRING_CACHE_HOURS:
            series = db.get_recurring_series(conn)
            print(f"\n2. Herhalende series: {len(series)} uit cache "
                  f"({age:.1f} uur oud), niets opgehaald")
            series_seconds = 0.0
        else:
            reason = "cache leeg" if age is None else f"cache {age:.0f} uur oud"
            print(f"\n2. Herhalende series ophalen ({reason})...", flush=True)
            print("   Dit is de dure stap; hij scant alle afspraken.", flush=True)
            started = time.monotonic()
            try:
                series = calendar_bridge.fetch_recurring_series()
            except calendar_bridge.AppleScriptError as exc:
                series_seconds = time.monotonic() - started
                print(f"   MISLUKT na {series_seconds:.0f}s: {exc}")
                print("\n   -> AppleScript blijft te traag. Laat dit zien,")
                print("      dan lezen we de agendabestanden direct van schijf.")
                return 1
            series_seconds = time.monotonic() - started
            db.replace_recurring_series(conn, series)
            conn.commit()
            print(f"   {len(series)} series in {series_seconds:.0f}s, opgeslagen in cache")

        for master_start, _master_end, rule in series:
            print(f"      {master_start[:10]}  {rule}")

    # --- samengevoegd ----------------------------------------------------
    added = calendar_bridge.expand_series_into(by_date, series, target, target)
    print(f"\n3. Daarvan vallen er {added} op {target}")

    slots = sorted(by_date.get(target, []))
    print(f"\n=== Wat het sync-script als bezet ziet op {target} ===\n")
    if not slots:
        print("   NIETS -> taken worden vanaf 08:00 ingepland,")
        print("   dwars over eventuele vergaderingen heen.")
    for s, e in slots:
        print(f"   {s.strftime('%H:%M')} - {e.strftime('%H:%M')}")

    print(f"\nTotaal {oneoff_seconds + series_seconds:.0f}s.")
    print("Vergelijk deze tijden met wat je in Agenda.app op die dag ziet.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
