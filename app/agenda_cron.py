"""Entry point for the separate Railway Cron Job service that sends scheduled
messages. Runs independently of the main webhook service (which sleeps between
messages and can't reliably fire its own scheduled jobs), so this runs on its
own schedule (every 5 minutes, the shortest interval Railway allows), checks
whether it's currently within a configured send window, and sends the message
directly via a raw Telegram API call -- no need to boot the full bot
Application for this.

    python -m app.agenda_cron
"""

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx

from app.categories import CATEGORY_EMOJI, get_category, strip_tag
from app.core.config import get_settings
from app.gcal import calendar as gcal
from app.gcal.cache import list_events_cached
from app.gcal.credentials import build_credentials
from app.girlfriend import format_girlfriend_summary, get_girlfriend_events
from app.nlp.quote import get_daily_quote
from app.state import load_state, safe_zoneinfo, save_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Matches Railway's minimum cron interval -- if the schedule and this drift apart,
# widen this rather than the cron interval.
WINDOW_MINUTES = 5


def _event_title(e) -> str:
    """Category-tagged events display with an emoji instead of the raw [CODE] tag,
    everywhere events are rendered -- mirrors the treatment in app/bot/handlers.py."""
    code = get_category(e.summary)
    if code:
        return f"{CATEGORY_EMOJI.get(code, '📌')} {strip_tag(e.summary)}"
    return e.summary


async def _send_telegram_message(text: str) -> None:
    settings = get_settings()
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            url, json={"chat_id": settings.owner_telegram_user_id, "text": text}
        )
    resp.raise_for_status()


def _next_day_target(target_time: str, now: datetime) -> date | None:
    """Both scheduled messages mean "preview the next calendar day", but their
    configured send time can be any HH:MM -- including right at the midnight
    boundary (23:59), where Railway's 5-minute cron ticks never land exactly on
    target, so the actual fire happens shortly *after* midnight. This one modular
    check handles every case uniformly: if `now` is within WINDOW_MINUTES after
    `target_time` (wrapping across midnight), returns the date being previewed --
    "today" if the tick already rolled past midnight relative to the target, else
    "tomorrow". Returns None if we're outside the send window entirely.
    """
    hour, minute = (int(x) for x in target_time.split(":"))
    target_minutes = hour * 60 + minute
    now_minutes = now.hour * 60 + now.minute
    if (now_minutes - target_minutes) % 1440 >= WINDOW_MINUTES:
        return None
    wrapped_past_midnight = now_minutes < target_minutes
    return now.date() if wrapped_past_midnight else now.date() + timedelta(days=1)


def _format_night_preview(events: list, preview_date: date, quote: str) -> str:
    date_str = preview_date.strftime("%d/%m")
    greeting = get_settings().greeting_name
    lines = [f"Good morning, {greeting}. Here is your schedule for tmr, ({date_str}):", ""]
    if not events:
        lines.append("NA")
    else:
        for idx, e in enumerate(events, start=1):
            if e.is_all_day:
                lines.append(f"{idx}. {_event_title(e)} (all day)")
            else:
                start = datetime.fromisoformat(e.start)
                lines.append(f"{idx}. {start.strftime('%H:%M')} — {_event_title(e)}")
    lines.append("")
    lines.append(f'"{quote}"')

    settings = get_settings()
    if settings.howabout_app_link and settings.howabout_app_link != "PLACEHOLDER_HOWABOUT_LINK":
        lines.append("")
        lines.append(f"HowAbout: {settings.howabout_app_link}")

    return "\n".join(lines)


async def maybe_send_night_preview() -> None:
    """Your own next-day agenda -- default 23:59, adjustable via /night_agenda_set."""
    state = load_state()
    if not state.night_agenda_enabled:
        logger.info("Night agenda disabled, nothing to do")
        return

    now = datetime.now(safe_zoneinfo(state.timezone))
    today_str = now.strftime("%Y-%m-%d")
    if state.night_agenda_last_sent_date == today_str:
        logger.info("Already sent today's night preview, nothing to do")
        return

    preview_date = _next_day_target(state.night_agenda_time, now)
    if preview_date is None:
        logger.info("Not within the night-preview window yet (target=%s, now=%s)", state.night_agenda_time, now.strftime("%H:%M"))
        return

    cal_ids = state.all_calendar_ids()
    events: list = []
    if cal_ids:
        credentials = build_credentials()
        start_of_day = datetime(preview_date.year, preview_date.month, preview_date.day, tzinfo=now.tzinfo)
        events = await list_events_cached(credentials, cal_ids, start_of_day, start_of_day + timedelta(days=1))

    quote = await get_daily_quote()
    text = _format_night_preview(events, preview_date, quote)
    await _send_telegram_message(text)

    state.night_agenda_last_sent_date = today_str
    save_state(state)
    logger.info("Sent night preview for %s", today_str)


async def maybe_send_girlfriend_summary() -> None:
    """Her next-day agenda -- default 17:00, adjustable via /girlfriend_agenda_set.
    Sent in the owner's evening since she's ~12h ahead in SG time, so her next day has
    usually already started by then."""
    state = load_state()
    if not state.girlfriend_agenda_enabled:
        logger.info("Girlfriend summary disabled, nothing to do")
        return
    settings = get_settings()
    if not settings.girlfriend_email and not settings.girlfriend_howabout_calendar_name:
        logger.info("No girlfriend calendar source configured, nothing to do")
        return

    now = datetime.now(safe_zoneinfo(state.timezone))
    today_str = now.strftime("%Y-%m-%d")
    if state.girlfriend_agenda_last_sent_date == today_str:
        logger.info("Already sent today's girlfriend summary, nothing to do")
        return

    preview_date = _next_day_target(state.girlfriend_agenda_time, now)
    if preview_date is None:
        logger.info("Not within the girlfriend-summary window yet (target=%s, now=%s)", state.girlfriend_agenda_time, now.strftime("%H:%M"))
        return

    target_dt = datetime(preview_date.year, preview_date.month, preview_date.day, tzinfo=now.tzinfo)
    try:
        credentials = build_credentials()
        if settings.girlfriend_howabout_calendar_name and not state.girlfriend_howabout_calendar_id:
            await gcal.sync_calendars_into_state(credentials, state)
            save_state(state)
        events = await get_girlfriend_events(credentials, target_dt, state)
    except Exception:
        logger.warning("Could not fetch girlfriend's calendar -- check sharing is still granted", exc_info=True)
        return

    text = format_girlfriend_summary(events, target_dt, when="tomorrow")
    await _send_telegram_message(text)

    state.girlfriend_agenda_last_sent_date = today_str
    save_state(state)
    logger.info("Sent girlfriend summary for %s", today_str)


async def _run_all() -> None:
    # Each check is independent -- one raising must not prevent the others from running
    # in the same cron tick.
    for check in (maybe_send_night_preview, maybe_send_girlfriend_summary):
        try:
            await check()
        except Exception:
            logger.warning("%s failed", check.__name__, exc_info=True)


def main() -> None:
    asyncio.run(_run_all())


if __name__ == "__main__":
    main()
