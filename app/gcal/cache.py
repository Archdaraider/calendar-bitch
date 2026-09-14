"""Disk-backed cache for the "events from some start point onward" read pattern shared
by almost every view/bulk command and the cron's scheduled messages.

Why disk instead of in-memory: the main service sleeps between messages under
Railway's Serverless mode, so an in-memory cache would just get thrown away on every
cold start and never help. A small JSON file on the same Volume both services already
share survives that, and reading/writing it is cheap compute -- far cheaper than a
fresh Google API round-trip, which is exactly the cost this exists to cut. Railway
bills serverless compute by the second, and a chunk of every request is just sitting
idle waiting on Google's API; skipping that wait when a recent-enough cached fetch
already covers the request directly cuts billed compute time. That's the priority here
over minimizing the *number* of API calls for its own sake -- one wide fetch that
over-covers common requests (so more commands hit cache) beats several narrow ones.

Kept deliberately simple: one entry per distinct calendar-ID set, a short TTL, and a
full invalidation (not a surgical patch) on any write, since writes are rare next to
reads for this bot.
"""

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta

from app.core.config import get_settings
from app.gcal.calendar import EventInfo, list_events

# Long enough that a burst of commands in one sitting (or a cron tick shortly after you
# used the bot) shares one fetch; short enough that an edit made directly in Google
# Calendar (bypassing this bot's own invalidation) doesn't stay invisible for long.
CACHE_TTL_SECONDS = 300

# Wide enough to cover every current "from now" read in the app -- the widest being
# bulk actions' 365-day lookahead -- so one cached fetch can serve /today through
# bulk-delete without each needing its own API call.
CACHE_WINDOW_DAYS = 365


def _cache_path() -> str:
    state_path = get_settings().state_file_path
    return os.path.join(os.path.dirname(state_path) or ".", "events_cache.json")


def _read_entries() -> list[dict]:
    path = _cache_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _write_entries(entries: list[dict]) -> None:
    path = _cache_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    os.replace(tmp_path, path)


def invalidate() -> None:
    """Called by every write in app.gcal.calendar. Drops the whole cache rather than
    patching specific entries -- simpler, and writes are infrequent enough that the
    next read just re-fetching once is a non-issue."""
    _write_entries([])


def _cache_key(calendar_ids: list[str]) -> str:
    return ",".join(sorted(calendar_ids))


def _event_start_dt(e: EventInfo, tzinfo) -> datetime:
    if e.is_all_day:
        return datetime.strptime(e.start, "%Y-%m-%d").replace(tzinfo=tzinfo)
    return datetime.fromisoformat(e.start)


async def list_events_cached(credentials, calendar_ids: list[str], time_min: datetime, time_max: datetime) -> list[EventInfo]:
    """Drop-in cached alternative to app.gcal.calendar.list_events() for the common
    "now (or a known start point) onward" pattern. Only use this for that shape --
    arbitrary single-day lookups or the recently-created sort don't fit a shared wide
    cache and should call list_events() directly."""
    key = _cache_key(calendar_ids)
    entries = _read_entries()
    now_ts = time.time()

    for entry in entries:
        if (
            entry.get("key") == key
            and now_ts - entry.get("fetched_at", 0) < CACHE_TTL_SECONDS
            and datetime.fromisoformat(entry["time_min"]) <= time_min
            and datetime.fromisoformat(entry["time_max"]) >= time_max
        ):
            events = [EventInfo(**item) for item in entry["events"]]
            return [e for e in events if time_min <= _event_start_dt(e, time_min.tzinfo) < time_max]

    wide_time_max = max(time_max, time_min + timedelta(days=CACHE_WINDOW_DAYS))
    events = await list_events(credentials, calendar_ids, time_min, wide_time_max)

    entries = [e for e in entries if e.get("key") != key]
    entries.append({
        "key": key,
        "fetched_at": now_ts,
        "time_min": time_min.isoformat(),
        "time_max": wide_time_max.isoformat(),
        "events": [asdict(e) for e in events],
    })
    _write_entries(entries)

    return [e for e in events if time_min <= _event_start_dt(e, time_min.tzinfo) < time_max]
