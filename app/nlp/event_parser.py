import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import dateparser.search

DURATION_RE = re.compile(r"\bfor\s+(\d+)\s*(hour|hr|minute|min)s?\b", re.IGNORECASE)
TIME_HINT_RE = re.compile(
    r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b|\bnoon\b|\bmidnight\b", re.IGNORECASE
)

DEFAULT_DURATION_MINUTES = 60
DEFAULT_HOUR_IF_NO_TIME = 9


@dataclass
class ParsedEvent:
    title: str
    start: datetime
    end: datetime
    time_was_guessed: bool
    description: str = ""
    # "add", "cancel", "bulk_delete", or "bulk_edit_time" -- only app.nlp.llm_parser
    # (Gemini) can detect anything but "add"; this local parser always produces "add".
    intent: str = "add"
    recurrence_rrule: str | None = None
    cancel_target_date: str = ""
    cancel_target_keyword: str = ""
    bulk_delete_category: str = ""
    bulk_delete_keywords: list[str] = field(default_factory=list)
    bulk_edit_category: str = ""
    bulk_edit_keywords: list[str] = field(default_factory=list)
    bulk_edit_new_time: str = ""


def parse_event_text(text: str, now: datetime | None = None) -> ParsedEvent | None:
    now = now or datetime.now()

    duration_minutes = DEFAULT_DURATION_MINUTES
    working_text = text
    dur_match = DURATION_RE.search(working_text)
    if dur_match:
        amount = int(dur_match.group(1))
        unit = dur_match.group(2).lower()
        duration_minutes = amount * 60 if unit.startswith("h") else amount
        working_text = working_text[: dur_match.start()] + working_text[dur_match.end() :]

    settings = {"PREFER_DATES_FROM": "future", "RELATIVE_BASE": now}
    results = dateparser.search.search_dates(working_text, languages=["en"], settings=settings)
    if not results:
        return None

    # search_dates finds the span; dateparser.parse() on just that substring is more reliable.
    matched_text, _ = max(results, key=lambda r: len(r[0]))
    start_dt = dateparser.parse(matched_text, languages=["en"], settings=settings)
    if start_dt is None:
        return None

    time_was_guessed = not TIME_HINT_RE.search(matched_text)
    if time_was_guessed:
        start_dt = start_dt.replace(hour=DEFAULT_HOUR_IF_NO_TIME, minute=0, second=0, microsecond=0)
    else:
        start_dt = start_dt.replace(second=0, microsecond=0)

    match_pos = working_text.find(matched_text)
    prefix = working_text[:match_pos]
    prefix = re.sub(r"(?i)\b(next|this)\s+$", "", prefix)
    title = (prefix + working_text[match_pos + len(matched_text) :]).strip(" ,-:")
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        title = "Event"

    end_dt = start_dt + timedelta(minutes=duration_minutes)
    return ParsedEvent(title=title, start=start_dt, end=end_dt, time_was_guessed=time_was_guessed)
