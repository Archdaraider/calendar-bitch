"""Girlfriend's calendars -- read-only, separate from the owner's own calendars (see
app.gcal.calendar.sync_calendars_into_state for the exclusion side of this). Two
possible sources, used together if both are configured:
  1. Her Google Calendar, shared directly with the owner's account (GIRLFRIEND_EMAIL).
  2. A calendar the owner subscribed to via Google Calendar's own "Add calendar > From
     URL" (a HowAbout public calendar feed, named via GIRLFRIEND_HOWABOUT_CALENDAR_NAME)
     -- resolved by name at sync time since subscribed/ICS calendars don't have a
     predictable ID.
Both reuse the same Google credentials as the rest of the bot -- no separate auth needed.
"""

from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials

from app.core.config import get_settings
from app.gcal.calendar import EventInfo, list_events
from app.state import BotState


async def get_girlfriend_events(
    credentials: Credentials, day: datetime, state: BotState, days: int = 1
) -> list[EventInfo]:
    """Prefers her Howabout calendar (resolved by name into
    state.girlfriend_howabout_calendar_id) since that's the calendar she actually keeps
    up to date. Only falls back to GIRLFRIEND_EMAIL (her raw Google account share) if
    the Howabout calendar hasn't been configured/resolved yet, so the feature still
    works before the first sync."""
    calendar_ids = []
    if state.girlfriend_howabout_calendar_id:
        calendar_ids.append(state.girlfriend_howabout_calendar_id)
    else:
        girlfriend_email = get_settings().girlfriend_email.strip()
        if girlfriend_email:
            calendar_ids.append(girlfriend_email)
    if not calendar_ids:
        return []
    start_of_day = day.replace(hour=0, minute=0, second=0, microsecond=0)
    return await list_events(credentials, calendar_ids, start_of_day, start_of_day + timedelta(days=days))


def format_girlfriend_summary(events: list[EventInfo], day: datetime, when: str = "today") -> str:
    date_str = day.strftime("%d/%m")
    label = "today" if when == "today" else "tomorrow"
    name = get_settings().girlfriend_display_name
    lines = [f"👩🏽❤️ {name}'s Schedule for {label}, ({date_str}):"]
    if not events:
        lines.append("NA")
    else:
        for idx, e in enumerate(events, start=1):
            if e.is_all_day:
                lines.append(f"{idx}. {e.summary} (all day)")
            else:
                start = datetime.fromisoformat(e.start)
                lines.append(f"{idx}. {start.strftime('%H:%M')} — {e.summary}")
    return "\n".join(lines)
