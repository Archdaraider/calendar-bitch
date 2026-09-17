"""Scheduled-message checks, run every 5 minutes.

Railway volumes attach to exactly one service, so this cron service has no
persistent disk of its own -- its entrypoint (main()) just pings the main
service's /internal/tick, which calls run_scheduled_checks() in-process there,
against the one real state file on its volume.

    python -m app.agenda_cron
"""

import logging
import os
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

# Matches Railway's minimum cron interval.
WINDOW_MINUTES = 5


def _event_title(e) -> str:
    """Mirrors the [CODE]-to-emoji treatment in app/bot/handlers.py."""
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
    """Date being previewed if `now` is within the send window of `target_time`, else None. Handles wrapping past midnight."""
    hour, minute = (int(x) for x in target_time.split(":"))
    target_minutes = hour * 60 + minute
    now_minutes = now.hour * 60 + now.minute
    if (now_minutes - target_minutes) % 1440 >= WINDOW_MINUTES:
        return None
    wrapped_past_midnight = now_minutes < target_minutes
    return now.date() if wrapped_past_midnight else now.date() + timedelta(days=1)


def _format_night_preview(events: list, preview_date: date, quote: str, tz) -> str:
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
                start = datetime.fromisoformat(e.start).astimezone(tz)
                lines.append(f"{idx}. {start.strftime('%H:%M')} — {_event_title(e)}")
    lines.append("")
    lines.append(f'"{quote}"')

    settings = get_settings()
    if settings.howabout_app_link and settings.howabout_app_link != "PLACEHOLDER_HOWABOUT_LINK":
        lines.append("")
        lines.append(f"HowAbout: {settings.howabout_app_link}")

    return "\n".join(lines)


async def maybe_send_night_preview() -> None:
    """Your own next-day agenda, default 23:59."""
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
    text = _format_night_preview(events, preview_date, quote, safe_zoneinfo(state.timezone))
    await _send_telegram_message(text)

    state.night_agenda_last_sent_date = today_str
    save_state(state)
    logger.info("Sent night preview for %s", today_str)


async def maybe_send_girlfriend_summary() -> None:
    """Her next-day agenda, default 17:00 (sent in the owner's evening, her morning)."""
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

    text = format_girlfriend_summary(events, target_dt, safe_zoneinfo(state.timezone), when="tomorrow")
    await _send_telegram_message(text)

    state.girlfriend_agenda_last_sent_date = today_str
    save_state(state)
    logger.info("Sent girlfriend summary for %s", today_str)


async def run_scheduled_checks() -> None:
    # Each check is independent -- one raising shouldn't block the other.
    for check in (maybe_send_night_preview, maybe_send_girlfriend_summary):
        try:
            await check()
        except Exception:
            logger.warning("%s failed", check.__name__, exc_info=True)


def main() -> None:
    """Entrypoint for the standalone cron service -- see module docstring. Reads env
    vars directly rather than going through app.core.config.Settings, since this
    process's minimal env doesn't have the webhook-service fields Settings requires."""
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "")
    internal_api_secret = os.environ.get("INTERNAL_API_SECRET", "")
    if not public_base_url or not internal_api_secret:
        logger.error("PUBLIC_BASE_URL and INTERNAL_API_SECRET must both be set for the cron service to work")
        return
    url = f"{public_base_url.rstrip('/')}/internal/tick"
    try:
        resp = httpx.post(url, headers={"X-Internal-Secret": internal_api_secret}, timeout=60)
        resp.raise_for_status()
        logger.info("Tick delivered to %s", url)
    except httpx.HTTPError:
        logger.warning("Failed to deliver tick to %s", url, exc_info=True)


if __name__ == "__main__":
    main()
