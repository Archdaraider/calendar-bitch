"""Gemini-based event parsing, falling back to the local dateparser-based parser if
every Gemini key fails or none are configured."""

import logging
from datetime import datetime, timedelta

from app.categories import CATEGORY_CODES
from app.nlp import gemini_client
from app.nlp.event_parser import ParsedEvent, parse_event_text
from app.nlp.recurrence import build_weekly_rrule

logger = logging.getLogger(__name__)

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING", "enum": ["add", "cancel", "bulk_delete", "bulk_edit_time"]},
        "title": {"type": "STRING"},
        "description": {"type": "STRING"},
        "start": {"type": "STRING", "nullable": True},
        "duration_minutes": {"type": "INTEGER"},
        "time_was_guessed": {"type": "BOOLEAN"},
        "recurrence_frequency": {"type": "STRING", "enum": ["NONE", "WEEKLY"]},
        "recurrence_interval": {"type": "INTEGER"},
        "recurrence_until": {"type": "STRING", "nullable": True},
        "cancel_target_date": {"type": "STRING", "nullable": True},
        "cancel_target_keyword": {"type": "STRING"},
        "bulk_delete_category": {"type": "STRING", "nullable": True},
        "bulk_delete_keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "bulk_edit_category": {"type": "STRING", "nullable": True},
        "bulk_edit_keywords": {"type": "ARRAY", "items": {"type": "STRING"}},
        "bulk_edit_new_time": {"type": "STRING", "nullable": True},
    },
    "required": ["intent"],
}

MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 24 * 60


def _build_prompt(text: str, now: datetime) -> str:
    return (
        f"Now: {now.strftime('%Y-%m-%dT%H:%M:%S')} ({now.strftime('%A')}). "
        f'Message: "{text}". Fix obvious typos when extracting text. '
        "First decide intent. Use 'cancel' if the message says a SINGLE existing event "
        "is cancelled/removed/off for one specific date (e.g. \"X is cancelled for 9th "
        "October\") -- then set cancel_target_date (YYYY-MM-DD) and "
        "cancel_target_keyword (a short distinctive phrase from the event's name to "
        "search for, e.g. 'BTM480'); leave the add-related fields at their defaults. "
        "Use 'bulk_delete' if the message asks to delete/remove MULTIPLE upcoming "
        "events at once by category and/or keyword(s), not a single dated occurrence "
        "(e.g. \"delete all BTM and COMP registered in [SCH] events coming up\", "
        "\"remove every MEET event\") -- then set bulk_delete_category to one of "
        f"{CATEGORY_CODES} if a category is named/implied (e.g. '[SCH]' or 'school "
        "events'), else null; and bulk_delete_keywords to the distinctive title "
        "keyword(s) mentioned (e.g. ['BTM', 'COMP']), or an empty array if none were "
        "given (category alone is enough to match). Leave the add/cancel-related "
        "fields at their defaults. "
        "Use 'bulk_edit_time' if the message asks to MOVE/CHANGE THE TIME of multiple "
        "upcoming events/series at once by category and/or keyword(s) (e.g. \"move all "
        "BTM [SCH] classes to 6pm\", \"change all COMP472 events to 19:00\") -- then set "
        "bulk_edit_category and bulk_edit_keywords the same way as bulk_delete_category/"
        "bulk_delete_keywords, and bulk_edit_new_time to the new clock time in 24h "
        "HH:MM format (e.g. '6pm' -> '18:00'). Leave all other fields at their defaults. "
        "Otherwise use 'add': extract title, start (local ISO 8601 datetime, no "
        "timezone offset, in the future relative to Now -- for a recurring event this "
        "is the date/time of the FIRST occurrence), duration_minutes (default 60), "
        "description (short extra context that isn't the title, empty string if none), "
        "time_was_guessed (true and default the time to 09:00 if no time of day was "
        "stated). If the message describes a repeating schedule (e.g. 'every Monday', "
        "'every other Tuesday'), set recurrence_frequency='WEEKLY', "
        "recurrence_interval=1 for 'every <day>' or 2 for 'every other <day>' (or the "
        "matching number for other phrasing), and recurrence_until to any one date "
        "(YYYY-MM-DD) within the month the recurrence should run through (e.g. 'until "
        "December' -> a date in December of the correct year). If it's not recurring, "
        "set recurrence_frequency='NONE'. If you can't find any date/time reference at "
        "all for an 'add', set start to null."
    )


def _result_to_parsed_event(result: dict, start_dt: datetime) -> ParsedEvent:
    duration = result.get("duration_minutes") or 60
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 60
    duration = max(MIN_DURATION_MINUTES, min(duration, MAX_DURATION_MINUTES))

    title = (result.get("title") or "Event").strip() or "Event"
    description = (result.get("description") or "").strip()
    end_dt = start_dt + timedelta(minutes=duration)

    recurrence_rrule = None
    if result.get("recurrence_frequency") == "WEEKLY":
        interval = result.get("recurrence_interval") or 1
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = 1
        recurrence_rrule = build_weekly_rrule(start_dt, interval, result.get("recurrence_until"))

    return ParsedEvent(
        title=title,
        start=start_dt,
        end=end_dt,
        time_was_guessed=bool(result.get("time_was_guessed", False)),
        description=description,
        intent="add",
        recurrence_rrule=recurrence_rrule,
    )


async def parse_event_with_ai(text: str, now: datetime | None = None) -> ParsedEvent | None:
    now = now or datetime.now()

    result = await gemini_client.generate_json(_build_prompt(text, now), RESPONSE_SCHEMA)
    if result is None:
        logger.info("Gemini unavailable, falling back to local parser")
        return parse_event_text(text, now=now)

    if result.get("intent") == "cancel":
        return ParsedEvent(
            # start/end are inert placeholders here -- the cancel flow never reads them.
            title="", start=now, end=now, time_was_guessed=False,
            intent="cancel",
            cancel_target_date=result.get("cancel_target_date") or "",
            cancel_target_keyword=result.get("cancel_target_keyword") or "",
        )

    if result.get("intent") == "bulk_delete":
        category = (result.get("bulk_delete_category") or "").strip().upper()
        if category not in CATEGORY_CODES:
            category = ""
        keywords = [k.strip() for k in (result.get("bulk_delete_keywords") or []) if k and k.strip()]
        return ParsedEvent(
            # start/end are inert placeholders here -- the bulk-delete flow never reads them.
            title="", start=now, end=now, time_was_guessed=False,
            intent="bulk_delete",
            bulk_delete_category=category,
            bulk_delete_keywords=keywords,
        )

    if result.get("intent") == "bulk_edit_time":
        category = (result.get("bulk_edit_category") or "").strip().upper()
        if category not in CATEGORY_CODES:
            category = ""
        keywords = [k.strip() for k in (result.get("bulk_edit_keywords") or []) if k and k.strip()]
        return ParsedEvent(
            # start/end are inert placeholders here -- the bulk-edit flow never reads them.
            title="", start=now, end=now, time_was_guessed=False,
            intent="bulk_edit_time",
            bulk_edit_category=category,
            bulk_edit_keywords=keywords,
            bulk_edit_new_time=(result.get("bulk_edit_new_time") or "").strip(),
        )

    start_raw = result.get("start")
    if not start_raw:
        # Gemini found no date/time -- trust that instead of retrying locally.
        return None

    try:
        start_dt = datetime.fromisoformat(start_raw)
    except ValueError:
        logger.warning("Gemini returned an unparseable start datetime %r, falling back to local parser", start_raw)
        return parse_event_text(text, now=now)

    return _result_to_parsed_event(result, start_dt)
