"""Placeholder adapter for the future HowAbout integration.

Nothing here talks to a real HowAbout API yet -- HOWABOUT_API_KEY and
HOWABOUT_BASE_URL are placeholders (see .env.example). When HowAbout exposes
an API, implement the HTTP calls inside push_shared_events() and this module
is the only thing that needs to change.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials

from app.gcal.calendar import EventInfo, list_events
from app.state import BotState

logger = logging.getLogger(__name__)


@dataclass
class ShareableEvent:
    event: EventInfo
    calendar_name: str


async def get_shareable_events(
    credentials: Credentials, state: BotState, window: timedelta = timedelta(days=7)
) -> list[ShareableEvent]:
    """Events from calendars flagged Share, within `window`."""
    shareable = {cal.id: cal.name for cal in state.calendars.values() if cal.is_shareable}
    if not shareable:
        return []

    now = datetime.now(timezone.utc)
    events = await list_events(credentials, list(shareable.keys()), now, now + window)
    return [ShareableEvent(event=e, calendar_name=shareable[e.calendar_id]) for e in events]


async def push_shared_events(events: list[ShareableEvent]) -> None:
    """Stub: would POST shareable events to HowAbout. Currently just logs."""
    logger.info(
        "howabout_client.push_shared_events stub called with %d event(s); no real "
        "HowAbout endpoint configured yet",
        len(events),
    )
