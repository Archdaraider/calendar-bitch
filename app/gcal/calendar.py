import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.core.config import get_settings
from app.state import BotState, CalendarPref


@dataclass
class CalendarInfo:
    id: str
    summary: str
    primary: bool


@dataclass
class EventInfo:
    id: str
    calendar_id: str
    summary: str
    start: str
    end: str
    is_all_day: bool
    created: str = ""
    # Set only on recurring-series instances -- points back to the master event.
    recurring_event_id: str = ""


def _service(credentials: Credentials):
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def _invalidate_cache() -> None:
    # Deferred import to avoid a circular import with app.gcal.cache.
    from app.gcal.cache import invalidate
    invalidate()


async def list_calendars(credentials: Credentials) -> list[CalendarInfo]:
    def _call():
        service = _service(credentials)
        result = service.calendarList().list().execute()
        return [
            CalendarInfo(
                id=item["id"],
                summary=item.get("summary", item["id"]),
                primary=item.get("primary", False),
            )
            for item in result.get("items", [])
        ]

    return await asyncio.to_thread(_call)


async def sync_calendars_into_state(credentials: Credentials, state: BotState) -> None:
    """Refreshes state.calendars, excluding the girlfriend's calendar(s) if configured."""
    settings = get_settings()
    girlfriend_email = settings.girlfriend_email.strip().lower()
    howabout_name = settings.girlfriend_howabout_calendar_name.strip().lower()

    if girlfriend_email:
        state.calendars.pop(girlfriend_email, None)

    calendars = await list_calendars(credentials)
    for cal in calendars:
        if girlfriend_email and cal.id.strip().lower() == girlfriend_email:
            continue
        if howabout_name and cal.summary.strip().lower() == howabout_name:
            state.girlfriend_howabout_calendar_id = cal.id
            state.calendars.pop(cal.id, None)
            continue
        existing = state.calendars.get(cal.id)
        is_shareable = existing.is_shareable if existing else False
        state.calendars[cal.id] = CalendarPref(
            id=cal.id, name=cal.summary, is_shareable=is_shareable, is_primary=cal.primary
        )


async def list_events(
    credentials: Credentials,
    calendar_ids: list[str],
    time_min: datetime,
    time_max: datetime,
) -> list[EventInfo]:
    def _call():
        service = _service(credentials)
        events: list[EventInfo] = []
        for cal_id in calendar_ids:
            result = (
                service.events()
                .list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat(),
                    timeMax=time_max.isoformat(),
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            for item in result.get("items", []):
                start = item.get("start", {})
                end = item.get("end", {})
                is_all_day = "date" in start
                events.append(
                    EventInfo(
                        id=item["id"],
                        calendar_id=cal_id,
                        summary=item.get("summary", "(no title)"),
                        start=start.get("dateTime", start.get("date", "")),
                        end=end.get("dateTime", end.get("date", "")),
                        is_all_day=is_all_day,
                        created=item.get("created", ""),
                        recurring_event_id=item.get("recurringEventId", ""),
                    )
                )
        events.sort(key=lambda e: e.start)
        return events

    return await asyncio.to_thread(_call)


async def list_recently_created_events(
    credentials: Credentials, calendar_ids: list[str], limit: int = 10
) -> list[EventInfo]:
    """Most recently created events -- Google can't sort by creation time server-side."""
    now = datetime.now(timezone.utc)
    events = await list_events(credentials, calendar_ids, now - timedelta(days=30), now + timedelta(days=730))
    events.sort(key=lambda e: e.created, reverse=True)
    return events[:limit]


async def create_event(
    credentials: Credentials,
    calendar_id: str,
    summary: str,
    start: datetime,
    end: datetime,
    tz: str,
    description: str = "",
    recurrence: list[str] | None = None,
) -> EventInfo:
    def _call():
        service = _service(credentials)
        body = {
            "summary": summary,
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }
        if description:
            body["description"] = description
        if recurrence:
            body["recurrence"] = recurrence
        item = service.events().insert(calendarId=calendar_id, body=body).execute()
        return EventInfo(
            id=item["id"],
            calendar_id=calendar_id,
            summary=item.get("summary", summary),
            start=item["start"].get("dateTime", ""),
            end=item["end"].get("dateTime", ""),
            is_all_day=False,
        )

    result = await asyncio.to_thread(_call)
    _invalidate_cache()
    return result


async def get_event(credentials: Credentials, calendar_id: str, event_id: str) -> dict:
    """Raw event resource -- for fields list_events() doesn't surface, e.g. a master's own recurrence."""
    def _call():
        service = _service(credentials)
        return service.events().get(calendarId=calendar_id, eventId=event_id).execute()

    return await asyncio.to_thread(_call)


async def get_event_recurrence(credentials: Credentials, calendar_id: str, event_id: str) -> list[str] | None:
    """The master's RRULE list -- instances from list_events() don't carry this."""
    item = await get_event(credentials, calendar_id, event_id)
    return item.get("recurrence")


async def update_event_time(
    credentials: Credentials, calendar_id: str, event_id: str, start: datetime, end: datetime, tz: str
) -> None:
    """For a recurring master, moves the whole series' clock time at once."""
    def _call():
        service = _service(credentials)
        body = {
            "start": {"dateTime": start.isoformat(), "timeZone": tz},
            "end": {"dateTime": end.isoformat(), "timeZone": tz},
        }
        service.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()

    await asyncio.to_thread(_call)
    _invalidate_cache()


async def update_event_recurrence(
    credentials: Credentials, calendar_id: str, event_id: str, recurrence: list[str]
) -> None:
    def _call():
        service = _service(credentials)
        service.events().patch(calendarId=calendar_id, eventId=event_id, body={"recurrence": recurrence}).execute()

    await asyncio.to_thread(_call)
    _invalidate_cache()


async def delete_event(credentials: Credentials, calendar_id: str, event_id: str) -> None:
    def _call():
        service = _service(credentials)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()

    await asyncio.to_thread(_call)
    _invalidate_cache()


async def update_event_summary(
    credentials: Credentials, calendar_id: str, event_id: str, new_summary: str
) -> EventInfo:
    def _call():
        service = _service(credentials)
        item = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
        item["summary"] = new_summary
        updated = service.events().update(calendarId=calendar_id, eventId=event_id, body=item).execute()
        start = updated.get("start", {})
        end = updated.get("end", {})
        return EventInfo(
            id=updated["id"],
            calendar_id=calendar_id,
            summary=updated.get("summary", new_summary),
            start=start.get("dateTime", start.get("date", "")),
            end=end.get("dateTime", end.get("date", "")),
            is_all_day="date" in start,
        )

    result = await asyncio.to_thread(_call)
    _invalidate_cache()
    return result
