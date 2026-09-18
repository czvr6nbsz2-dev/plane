"""Minimal RRULE expansion.

Calendar.app hands a recurring series back as a single master event dated at
its first occurrence, carrying the rule as text:

    FREQ=MONTHLY;INTERVAL=3;UNTIL=20270121T225959Z;BYDAY=3WE

It never expands the occurrences itself, so the stacker would think every
later date is free. This module turns such a master into the concrete
occurrences that fall inside a window.

Covers what real meetings use: DAILY / WEEKLY / MONTHLY / YEARLY with
INTERVAL, COUNT, UNTIL, BYDAY (including ordinals like 3WE) and BYMONTHDAY.
Anything it cannot parse yields no occurrences rather than wrong ones.
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Optional

WEEKDAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

# Safety valve: stop expanding after this many candidate periods.
MAX_PERIODS = 2000


def parse_rule(text: str) -> dict:
    """Parse an RRULE string into a dict. Unknown parts are kept as strings."""
    rule: dict = {}
    if not text:
        return rule
    body = text.strip()
    if body.upper().startswith("RRULE:"):
        body = body[6:]
    for chunk in body.split(";"):
        if "=" not in chunk:
            continue
        key, _, value = chunk.partition("=")
        rule[key.strip().upper()] = value.strip()
    return rule


def _parse_until(value: str) -> Optional[datetime]:
    value = value.strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _parse_byday(value: str) -> list[tuple[Optional[int], int]]:
    """"3WE,1MO" -> [(3, 2), (1, 0)];  "MO,WE" -> [(None, 0), (None, 2)]."""
    out: list[tuple[Optional[int], int]] = []
    for part in value.split(","):
        part = part.strip().upper()
        if not part:
            continue
        code = part[-2:]
        if code not in WEEKDAYS:
            continue
        prefix = part[:-2]
        ordinal: Optional[int] = None
        if prefix:
            try:
                ordinal = int(prefix)
            except ValueError:
                continue
        out.append((ordinal, WEEKDAYS[code]))
    return out


def _add_months(d: date, months: int) -> date:
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def _nth_weekday(year: int, month: int, ordinal: int, weekday: int) -> Optional[date]:
    """3rd Wednesday of a month; negative ordinal counts from the end."""
    days_in_month = monthrange(year, month)[1]
    matches = [
        date(year, month, day)
        for day in range(1, days_in_month + 1)
        if date(year, month, day).weekday() == weekday
    ]
    if not matches:
        return None
    index = ordinal - 1 if ordinal > 0 else ordinal
    try:
        return matches[index]
    except IndexError:
        return None


def expand(
    master_start: datetime,
    master_end: datetime,
    rule_text: str,
    window_start: date,
    window_end: date,
    excluded: frozenset = frozenset(),
) -> list[tuple[datetime, datetime]]:
    """Return every occurrence overlapping [window_start, window_end].

    `excluded` holds dates (datetime.date) whose occurrence was deleted.
    """
    rule = parse_rule(rule_text)
    freq = rule.get("FREQ", "").upper()
    if freq not in ("DAILY", "WEEKLY", "MONTHLY", "YEARLY"):
        return []

    try:
        interval = max(1, int(rule.get("INTERVAL", "1")))
    except ValueError:
        interval = 1

    count: Optional[int] = None
    if "COUNT" in rule:
        try:
            count = int(rule["COUNT"])
        except ValueError:
            count = None

    until = _parse_until(rule["UNTIL"]) if "UNTIL" in rule else None
    bydays = _parse_byday(rule["BYDAY"]) if "BYDAY" in rule else []

    bymonthdays: list[int] = []
    if "BYMONTHDAY" in rule:
        for part in rule["BYMONTHDAY"].split(","):
            try:
                bymonthdays.append(int(part))
            except ValueError:
                pass

    duration = master_end - master_start
    if duration <= timedelta(0):
        duration = timedelta(minutes=30)
    clock = master_start.time()
    series_start = master_start.date()

    occurrences: list[tuple[datetime, datetime]] = []
    emitted = 0

    def emit(day: date) -> bool:
        """Record an occurrence. Returns False when the series must stop."""
        nonlocal emitted
        if day < series_start:
            return True
        start_dt = datetime.combine(day, clock)
        if until is not None and start_dt > until:
            return False
        emitted += 1
        if count is not None and emitted > count:
            return False
        if window_start <= day <= window_end and day not in excluded:
            occurrences.append((start_dt, start_dt + duration))
        return True

    period = 0
    while period < MAX_PERIODS:
        stop = False

        if freq == "DAILY":
            day = series_start + timedelta(days=period * interval)
            if day > window_end:
                break
            if not emit(day):
                stop = True

        elif freq == "WEEKLY":
            week_start = series_start - timedelta(days=series_start.weekday())
            week_start += timedelta(weeks=period * interval)
            if week_start > window_end:
                break
            targets = [wd for _ord, wd in bydays] or [series_start.weekday()]
            for weekday in sorted(set(targets)):
                day = week_start + timedelta(days=weekday)
                if not emit(day):
                    stop = True
                    break

        elif freq == "MONTHLY":
            anchor = _add_months(series_start.replace(day=1), period * interval)
            if anchor > window_end.replace(day=1) and anchor.year >= window_end.year:
                if anchor > window_end:
                    break
            days: list[date] = []
            if bydays:
                for ordinal, weekday in bydays:
                    if ordinal is None:
                        days.extend(
                            date(anchor.year, anchor.month, d)
                            for d in range(1, monthrange(anchor.year, anchor.month)[1] + 1)
                            if date(anchor.year, anchor.month, d).weekday() == weekday
                        )
                    else:
                        found = _nth_weekday(anchor.year, anchor.month, ordinal, weekday)
                        if found:
                            days.append(found)
            elif bymonthdays:
                last = monthrange(anchor.year, anchor.month)[1]
                for md in bymonthdays:
                    day_num = md if md > 0 else last + md + 1
                    if 1 <= day_num <= last:
                        days.append(date(anchor.year, anchor.month, day_num))
            else:
                last = monthrange(anchor.year, anchor.month)[1]
                days.append(date(anchor.year, anchor.month, min(series_start.day, last)))
            for day in sorted(days):
                if not emit(day):
                    stop = True
                    break

        else:  # YEARLY
            year = series_start.year + period * interval
            if year > window_end.year:
                break
            try:
                day = series_start.replace(year=year)
            except ValueError:  # 29 February on a non-leap year
                day = date(year, 2, 28)
            if not emit(day):
                stop = True

        if stop:
            break
        period += 1

    return occurrences
