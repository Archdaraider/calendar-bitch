"""Deterministic recurrence-rule math. Gemini extracts the semantic pieces (an
interval like "every"/"every other", and an approximate "until" reference month/date)
-- exact calendar arithmetic (e.g. figuring out precisely which date is "the last
Monday of December") is computed here in plain Python rather than trusted to an LLM,
which is unreliable at exact date arithmetic.
"""

import calendar as _calendar_module
from datetime import date, datetime, timedelta

RRULE_BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
WEEKDAY_NAMES = {
    "MO": "Monday", "TU": "Tuesday", "WE": "Wednesday", "TH": "Thursday",
    "FR": "Friday", "SA": "Saturday", "SU": "Sunday",
}


def build_weekly_rrule(start: datetime, interval: int, until_hint: str | None) -> str:
    """`start`'s weekday determines BYDAY. `until_hint`, if given, is any date
    (YYYY-MM-DD) within the intended final month -- the recurrence's actual UNTIL is
    computed as the last occurrence of `start`'s weekday on or before the end of that
    month (e.g. "until December" -> the last Monday of December)."""
    byday = RRULE_BYDAY[start.weekday()]
    interval = max(1, interval or 1)
    rrule = f"RRULE:FREQ=WEEKLY;INTERVAL={interval};BYDAY={byday}"

    if not until_hint:
        return rrule
    try:
        until_date = datetime.strptime(until_hint, "%Y-%m-%d").date()
    except ValueError:
        return rrule

    last_day_num = _calendar_module.monthrange(until_date.year, until_date.month)[1]
    month_end = date(until_date.year, until_date.month, last_day_num)
    days_back = (month_end.weekday() - start.weekday()) % 7
    last_occurrence = month_end - timedelta(days=days_back)
    return f"{rrule};UNTIL={last_occurrence.strftime('%Y%m%d')}T235959Z"


def truncate_rrule(rrule: str, last_occurrence_date: date) -> str:
    """Rewrites an existing RRULE's UNTIL so the series stops after
    `last_occurrence_date` (inclusive) -- used by bulk-delete's "only remove
    today-onward occurrences" truncation, which leaves past occurrences alone since
    they're just history at that point, not something to retroactively edit."""
    parts = [p for p in rrule.split(";") if not p.startswith("UNTIL=")]
    parts.append(f"UNTIL={last_occurrence_date.strftime('%Y%m%d')}T235959Z")
    return ";".join(parts)


def describe_rrule(rrule: str) -> str:
    """Human-readable summary for confirmation messages, e.g.
    'every other Monday, until 25 Jan 2027'."""
    parts = dict(p.split("=", 1) for p in rrule.removeprefix("RRULE:").split(";") if "=" in p)
    interval = int(parts.get("INTERVAL", 1))
    byday = parts.get("BYDAY", "")
    weekday_name = WEEKDAY_NAMES.get(byday, byday)

    if interval == 1:
        freq_desc = f"every {weekday_name}"
    elif interval == 2:
        freq_desc = f"every other {weekday_name}"
    else:
        freq_desc = f"every {interval} weeks on {weekday_name}"

    until = parts.get("UNTIL")
    if until:
        until_date = datetime.strptime(until[:8], "%Y%m%d").date()
        return f"{freq_desc}, until {until_date.strftime('%d %b %Y')}"
    return freq_desc
