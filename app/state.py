import json
import os
from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def safe_zoneinfo(tz_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("UTC")


@dataclass
class CalendarPref:
    id: str
    name: str
    is_shareable: bool = False
    is_primary: bool = False


@dataclass
class BotState:
    calendars: dict[str, CalendarPref] = field(default_factory=dict)
    # Neutral until you run /timezone -- see the README's first-run steps.
    timezone: str = "UTC"
    # Your own next-day agenda -- default 23:59, adjustable via /night_agenda_set.
    night_agenda_enabled: bool = False
    night_agenda_time: str = "23:59"
    night_agenda_last_sent_date: str = ""
    # Girlfriend's shared-calendar summary, previewing HER next day -- default 17:00
    # (she's ~12h ahead in SG time, so her next day has usually already started by the
    # owner's evening), adjustable via /girlfriend_agenda_set.
    girlfriend_agenda_enabled: bool = False
    girlfriend_agenda_time: str = "17:00"
    girlfriend_agenda_last_sent_date: str = ""
    # Resolved by name during a calendar sync (see gcal.calendar.sync_calendars_into_state)
    # since subscribed/ICS calendars don't have a predictable ID to configure directly.
    girlfriend_howabout_calendar_id: str = ""

    def primary_calendar_id(self) -> str | None:
        for cal in self.calendars.values():
            if cal.is_primary:
                return cal.id
        return next(iter(self.calendars), None)

    def shareable_calendar_ids(self) -> list[str]:
        return [c.id for c in self.calendars.values() if c.is_shareable]

    def all_calendar_ids(self) -> list[str]:
        return list(self.calendars.keys())


def _state_path() -> str:
    return get_settings().state_file_path


def load_state() -> BotState:
    path = _state_path()
    if not os.path.exists(path):
        return BotState()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    calendars = {
        cal_id: CalendarPref(**cal_data) for cal_id, cal_data in raw.get("calendars", {}).items()
    }
    return BotState(
        calendars=calendars,
        timezone=raw.get("timezone", "UTC"),
        night_agenda_enabled=raw.get("night_agenda_enabled", False),
        night_agenda_time=raw.get("night_agenda_time", "23:59"),
        night_agenda_last_sent_date=raw.get("night_agenda_last_sent_date", ""),
        girlfriend_agenda_enabled=raw.get("girlfriend_agenda_enabled", False),
        girlfriend_agenda_time=raw.get("girlfriend_agenda_time", "17:00"),
        girlfriend_agenda_last_sent_date=raw.get("girlfriend_agenda_last_sent_date", ""),
        girlfriend_howabout_calendar_id=raw.get("girlfriend_howabout_calendar_id", ""),
    )


def save_state(state: BotState) -> None:
    path = _state_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = {
        "calendars": {cal_id: asdict(cal) for cal_id, cal in state.calendars.items()},
        "timezone": state.timezone,
        "night_agenda_enabled": state.night_agenda_enabled,
        "night_agenda_time": state.night_agenda_time,
        "night_agenda_last_sent_date": state.night_agenda_last_sent_date,
        "girlfriend_agenda_enabled": state.girlfriend_agenda_enabled,
        "girlfriend_agenda_time": state.girlfriend_agenda_time,
        "girlfriend_agenda_last_sent_date": state.girlfriend_agenda_last_sent_date,
        "girlfriend_howabout_calendar_id": state.girlfriend_howabout_calendar_id,
    }
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp_path, path)
