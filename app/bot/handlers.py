import logging
import re
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from app.bot.access import restricted
from app.categories import CATEGORY_CODES, CATEGORY_EMOJI, category_hint, get_category, is_tagged, strip_tag
from app.core.config import get_settings
from app.gcal import calendar as gcal
from app.gcal.cache import list_events_cached
from app.girlfriend import get_girlfriend_events
from app.nlp import gemini_client
from app.nlp.llm_parser import parse_event_with_ai
from app.nlp.quote import get_daily_quote
from app.nlp.recurrence import describe_rrule, truncate_rrule
from app.state import BotState, safe_zoneinfo, save_state

logger = logging.getLogger(__name__)

RENAME_TEXT = 0

HELP_TEXT = (
    "🗓 *Calendar_Bitch*\n\n"
    "Just text me an event and I'll add it to your primary Google Calendar, typos and "
    "all, e.g.:\n"
    "`dinner witg sam fri 7pm bring the wine`\n"
    "`gym tomorrow at 6am for 90 min`\n"
    "`BTM480 Classes 5.45pm every Monday until December` (recurring — also handles "
    "'every other <day>')\n"
    "`BTM480 Class is cancelled for 9th October` (cancels just that one occurrence)\n"
    "`delete all BTM and COMP [SCH] events coming up` (bulk delete by category/keyword "
    "— shows a confirm button first; recurring series are truncated from today onward, "
    "past occurrences are kept)\n"
    "`move all BTM [SCH] classes to 6pm` (bulk time-edit by category/keyword — shows a "
    "confirm button first; for a recurring series this moves the whole series, past "
    "and future)\n"
    "No time given? I default to 9am and tell you so. After parsing, I ask you to pick "
    "the category yourself (WORK, !!!, MEET, DL, SCH, LEI, GF, OTH) — never auto-assigned.\n\n"
    "*View*\n"
    "/today, /tomorrow, /week, /next — view events\n"
    "/upcoming_workstuff — upcoming WORK, !!!, SCH, DL events (next 30 days)\n"
    "/upcoming_socials — upcoming MEET, LEI events (next 30 days)\n\n"
    "*Edit & organize*\n"
    "/edit, /cancel — pick an event to edit or delete; each has a button to switch "
    "between 'recently added' and 'upcoming' views, and a Back button once you've "
    "picked one\n"
    "/calendars — choose which calendars are Shared vs Private\n"
    "/categorize — backfill category tags onto existing untagged events\n\n"
    "*Her calendar* (needs GIRLFRIEND_EMAIL and/or GIRLFRIEND_HOWABOUT_CALENDAR_NAME set)\n"
    "/gf — countdown to her birthday, anniversary, and next time together\n"
    "/gf_schedule — her week ahead, from her shared calendar\n"
    "/gf_schedule_today — her today + tomorrow only, each event's time shown in both "
    "her timezone and yours\n\n"
    "*Scheduled messages*\n"
    "/night_agenda_on, /night_agenda_off — toggle your own next-day agenda (~11:59pm)\n"
    "/night_agenda_set HH:MM — change when it's sent\n"
    "/girlfriend_agenda_on, /girlfriend_agenda_off — toggle her next-day schedule "
    "summary (~5pm)\n"
    "/girlfriend_agenda_set HH:MM — change when it's sent\n\n"
    "*Settings*\n"
    "/timezone <IANA name> — set your timezone (e.g. /timezone America/Toronto) — "
    "controls what time events are actually created at\n"
    "/stop — abort an /edit rename in progress\n\n"
    "Shared calendars are the ones that will feed your HowAbout social feed once that "
    "integration exists. Private calendars never leave this bot. The 📅 Open HowAbout "
    "button below only appears once HOWABOUT_APP_LINK is actually set."
)

# Registered with Telegram via set_my_commands() at startup (app/main.py) so typing "/"
# shows this list in the native command picker -- single source of truth, kept in sync
# with register_handlers() below rather than relying on a manual BotFather step.
BOT_COMMANDS = [
    # View
    ("today", "Show today's events"),
    ("tomorrow", "Show tomorrow's events"),
    ("week", "Show this week's events"),
    ("next", "Show your next event"),
    ("add", "Add an event, e.g. /add dinner fri 7pm"),
    ("upcoming_workstuff", "Upcoming WORK, !!!, SCH, DL events"),
    ("upcoming_socials", "Upcoming MEET, LEI events"),
    # Edit & organize
    ("edit", "Pick an event (recent or upcoming) to rename or delete"),
    ("cancel", "Pick an event (recent or upcoming) to delete"),
    ("calendars", "Toggle Share/Private per calendar"),
    ("categorize", "Backfill category tags onto existing untagged events"),
    # Her calendar
    ("gf", "Countdown to her birthday, anniversary, next time together"),
    ("gf_schedule", "Her week ahead, from her shared calendar"),
    ("gf_schedule_today", "Her today+tomorrow, with times shown in both timezones"),
    # Scheduled messages
    ("night_agenda_on", "Turn on your own next-day agenda (~11:59pm)"),
    ("night_agenda_off", "Turn off your own next-day agenda"),
    ("night_agenda_set", "Change when it's sent, e.g. /night_agenda_set 23:30"),
    ("girlfriend_agenda_on", "Turn on her next-day schedule summary (~5pm)"),
    ("girlfriend_agenda_off", "Turn off her next-day schedule summary"),
    ("girlfriend_agenda_set", "Change when it's sent, e.g. /girlfriend_agenda_set 17:30"),
    # Settings
    ("timezone", "Set your timezone, e.g. /timezone America/Toronto"),
    ("help", "List commands"),
]


def _get_state(context: ContextTypes.DEFAULT_TYPE) -> BotState:
    return context.bot_data["state"]


def _get_credentials(context: ContextTypes.DEFAULT_TYPE) -> Credentials:
    return context.bot_data["credentials"]


async def _ensure_calendars_synced(context: ContextTypes.DEFAULT_TYPE) -> BotState:
    state = _get_state(context)
    settings = get_settings()
    howabout_unresolved = bool(settings.girlfriend_howabout_calendar_name) and not state.girlfriend_howabout_calendar_id
    if not state.calendars or howabout_unresolved:
        await gcal.sync_calendars_into_state(_get_credentials(context), state)
        save_state(state)
    return state


def _event_title(e) -> str:
    """Category-tagged events display with an emoji instead of the raw [CODE] tag,
    everywhere events are rendered."""
    code = get_category(e.summary)
    if code:
        return f"{CATEGORY_EMOJI.get(code, '📌')} {strip_tag(e.summary)}"
    return e.summary


def _event_day(e) -> date:
    return datetime.strptime(e.start, "%Y-%m-%d").date() if e.is_all_day else datetime.fromisoformat(e.start).date()


# Short, fixed-width separator under each day header -- deliberately not a full-width
# rule, so it never wraps awkwardly on a narrow phone screen.
DAY_SEPARATOR = "──────"


def _format_events(events: list) -> str:
    if not events:
        return "Nothing found."
    lines: list[str] = []
    current_day: date | None = None
    for e in events:
        day = _event_day(e)
        if day != current_day:
            if current_day is not None:
                lines.append("")
            lines.append(day.strftime("%a %d %b"))
            lines.append(DAY_SEPARATOR)
            current_day = day
        if e.is_all_day:
            lines.append(f"{_event_title(e)} (all day)")
        else:
            start = datetime.fromisoformat(e.start)
            lines.append(f"{start.strftime('%H:%M')} — {_event_title(e)}")
    return "\n".join(lines)


# --- basic commands -----------------------------------------------------

GOOGLE_CALENDAR_URL = "https://calendar.google.com/calendar/r"
CALENDAR_BUTTON_TEXT = "📅 Open Google Calendar"
HOWABOUT_BUTTON_TEXT = "📅 Open HowAbout"


def _main_reply_keyboard() -> ReplyKeyboardMarkup | ReplyKeyboardRemove:
    """Persistent buttons docked below the text input, always visible in the chat.
    Google Calendar always shows (no config needed -- opens whichever Google account
    is active on the device). HowAbout only shows once HOWABOUT_APP_LINK is a real
    value -- its absence is the signal that the link isn't configured yet."""
    settings = get_settings()
    row = [CALENDAR_BUTTON_TEXT]
    if settings.howabout_app_link and settings.howabout_app_link != "PLACEHOLDER_HOWABOUT_LINK":
        row.append(HOWABOUT_BUTTON_TEXT)
    return ReplyKeyboardMarkup([row], resize_keyboard=True, is_persistent=True)


@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    tz_note = (
        "\n\n⚠️ *First thing*: set your timezone with /timezone (e.g. `/timezone "
        "America/Toronto`) — otherwise events will be created in UTC, not your local time."
        if state.timezone == "UTC" else ""
    )
    await update.effective_message.reply_text(
        "👋 I'm Calendar_Bitch.\n\n"
        "Just text me things like 'dinner with sam fri 7pm' and I'll add them to your "
        f"Google Calendar. /help for everything else I can do.{tz_note}",
        parse_mode="Markdown",
        reply_markup=_main_reply_keyboard(),
    )


@restricted
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(HELP_TEXT, parse_mode="Markdown", reply_markup=_main_reply_keyboard())


@restricted
async def calendar_button_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(
        "Tap below to open Google Calendar:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📅 Open Google Calendar", url=GOOGLE_CALENDAR_URL)]]),
    )


@restricted
async def howabout_button_pressed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    await update.effective_message.reply_text(
        "Tap below to open HowAbout:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📅 Open HowAbout", url=settings.howabout_app_link)]]),
    )


# --- viewing events ------------------------------------------------------

async def _list_events_window(update: Update, context: ContextTypes.DEFAULT_TYPE, window: timedelta, label: str) -> None:
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(timezone.utc)
    events = await list_events_cached(credentials, state.all_calendar_ids(), now, now + window)
    await update.effective_message.reply_text(f"{label}:\n{_format_events(events)}")


def _format_daily_brief(events: list, day: date, quote: str) -> str:
    date_str = day.strftime("%d/%m")
    greeting = get_settings().greeting_name
    lines = [f"Good day, {greeting}. Here is your schedule for today, ({date_str}):", ""]
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


@restricted
async def today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Calendar-day bounded (midnight to midnight in your timezone), full brief with
    # greeting + daily quote -- same treatment as the 11:59pm "tomorrow" preview.
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(safe_zoneinfo(state.timezone))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = await list_events_cached(credentials, state.all_calendar_ids(), start, start + timedelta(days=1))
    quote = await get_daily_quote()
    await update.effective_message.reply_text(_format_daily_brief(events, now.date(), quote))


@restricted
async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_events_window(update, context, timedelta(days=7), "This week")


@restricted
async def tomorrow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Calendar-day bounded (midnight to midnight in your timezone), unlike /today's
    # rolling 24h window -- "tomorrow" needs an actual day boundary to mean anything.
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(safe_zoneinfo(state.timezone))
    start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    events = await list_events_cached(credentials, state.all_calendar_ids(), start, start + timedelta(days=1))
    await update.effective_message.reply_text(f"Tomorrow:\n{_format_events(events)}")


@restricted
async def next_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(timezone.utc)
    events = await list_events_cached(credentials, state.all_calendar_ids(), now, now + timedelta(days=30))
    if not events:
        await update.effective_message.reply_text("No upcoming events in the next 30 days.")
        return
    await update.effective_message.reply_text("Next up:\n" + _format_events(events[:1]))


UPCOMING_WINDOW_DAYS = 30
WORKSTUFF_CATEGORIES = {"WORK", "!!!", "SCH", "DL"}
SOCIALS_CATEGORIES = {"MEET", "LEI"}


async def _list_upcoming_by_category(
    update: Update, context: ContextTypes.DEFAULT_TYPE, categories: set[str], label: str
) -> None:
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(timezone.utc)
    events = await list_events_cached(
        credentials, state.all_calendar_ids(), now, now + timedelta(days=UPCOMING_WINDOW_DAYS)
    )
    filtered = [e for e in events if get_category(e.summary) in categories]
    await update.effective_message.reply_text(f"{label} (next {UPCOMING_WINDOW_DAYS} days):\n{_format_events(filtered)}")


@restricted
async def upcoming_workstuff(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_upcoming_by_category(update, context, WORKSTUFF_CATEGORIES, "Work stuff")


@restricted
async def upcoming_socials(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _list_upcoming_by_category(update, context, SOCIALS_CATEGORIES, "Socials")


# --- /gf: countdown to her birthday, anniversary, next time together -----

GF_LOOKAHEAD_DAYS = 90


def _next_occurrence(month: int, day: int, today: date) -> date:
    try:
        candidate = date(today.year, month, day)
    except ValueError:
        candidate = date(today.year, month, 28)  # Feb 29 in a non-leap year
    if candidate < today:
        try:
            candidate = date(today.year + 1, month, day)
        except ValueError:
            candidate = date(today.year + 1, month, 28)
    return candidate


def _format_countdown(days: int) -> str:
    if days == 0:
        return "TODAY!"
    return f"{days} day{'s' if days != 1 else ''}"


@restricted
async def gf_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    state = _get_state(context)
    today = datetime.now(safe_zoneinfo(state.timezone)).date()
    lines = [f"💕 {settings.girlfriend_display_name} Countdown"]

    if settings.partner_birthday_date:
        try:
            bday = datetime.strptime(settings.partner_birthday_date, "%Y-%m-%d").date()
            next_bday = _next_occurrence(bday.month, bday.day, today)
            lines.append(
                f"🎂 Her birthday: {_format_countdown((next_bday - today).days)} ({next_bday.strftime('%d %b')})"
            )
        except ValueError:
            lines.append("🎂 Her birthday: PARTNER_BIRTHDAY_DATE is set but isn't a valid YYYY-MM-DD date")
    else:
        lines.append("🎂 Her birthday: not set (PARTNER_BIRTHDAY_DATE)")

    if settings.anniversary_date:
        try:
            anniv = datetime.strptime(settings.anniversary_date, "%Y-%m-%d").date()
            next_anniv = _next_occurrence(anniv.month, anniv.day, today)
            years = next_anniv.year - anniv.year
            lines.append(
                f"💍 Anniversary ({years} yr{'s' if years != 1 else ''}): "
                f"{_format_countdown((next_anniv - today).days)} ({next_anniv.strftime('%d %b')})"
            )
        except ValueError:
            lines.append("💍 Anniversary: ANNIVERSARY_DATE is set but isn't a valid YYYY-MM-DD date")
    else:
        lines.append("💍 Anniversary: not set (ANNIVERSARY_DATE)")

    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(timezone.utc)
    events = await list_events_cached(credentials, state.all_calendar_ids(), now, now + timedelta(days=GF_LOOKAHEAD_DAYS))
    gf_events = [e for e in events if get_category(e.summary) == "GF"]
    if gf_events:
        nxt = gf_events[0]
        if nxt.is_all_day:
            lines.append(f"👩🏽❤️ Next time together: {strip_tag(nxt.summary)} (all day)")
        else:
            start = datetime.fromisoformat(nxt.start)
            lines.append(
                f"👩🏽❤️ Next time together: {_format_countdown((start.date() - today).days)} "
                f"({start.strftime('%d %b')}) — {strip_tag(nxt.summary)}"
            )
    else:
        lines.append(f"📅 Next time together: nothing [GF]-tagged in the next {GF_LOOKAHEAD_DAYS} days")

    await update.effective_message.reply_text("\n".join(lines))


GF_SCHEDULE_DAYS = 7


@restricted
async def gf_schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not settings.girlfriend_email and not settings.girlfriend_howabout_calendar_name:
        await update.effective_message.reply_text(
            "Neither GIRLFRIEND_EMAIL nor GIRLFRIEND_HOWABOUT_CALENDAR_NAME is set — "
            "add one to your env vars first."
        )
        return
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(safe_zoneinfo(state.timezone))
    events = await get_girlfriend_events(credentials, now, state, days=GF_SCHEDULE_DAYS)
    name = get_settings().girlfriend_display_name
    await update.effective_message.reply_text(f"👩🏽❤️ {name}'s week ahead:\n{_format_events(events)}")


# Window is bounded by HER calendar day (GIRLFRIEND_TIMEZONE), not yours -- "today and
# tomorrow" means her today and her tomorrow, since this is her schedule.
GF_SCHEDULE_TODAY_DAYS = 2


def _format_gf_dual_tz(events: list, girlfriend_tz_name: str, local_tz_name: str) -> str:
    girlfriend_tz = safe_zoneinfo(girlfriend_tz_name)
    local_tz = safe_zoneinfo(local_tz_name)
    name = get_settings().girlfriend_display_name
    lines = [f"👩🏽❤️ {name}'s Schedule — today & tomorrow ({girlfriend_tz_name} → {local_tz_name}):"]
    if not events:
        lines.append("NA")
        return "\n".join(lines)

    current_day_label = None
    for e in events:
        if e.is_all_day:
            lines.append(f"{_event_title(e)} (all day)")
            continue
        start = datetime.fromisoformat(e.start)
        her_time = start.astimezone(girlfriend_tz)
        your_time = start.astimezone(local_tz)
        day_label = her_time.strftime("%a %d %b")
        if day_label != current_day_label:
            lines.append("")
            lines.append(f"{day_label} (her day)")
            lines.append(DAY_SEPARATOR)
            current_day_label = day_label
        lines.append(
            f"{her_time.strftime('%H:%M')} her time  →  {your_time.strftime('%H:%M')} yours — {_event_title(e)}"
        )
    return "\n".join(lines)


@restricted
async def gf_schedule_today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not settings.girlfriend_email and not settings.girlfriend_howabout_calendar_name:
        await update.effective_message.reply_text(
            "Neither GIRLFRIEND_EMAIL nor GIRLFRIEND_HOWABOUT_CALENDAR_NAME is set — "
            "add one to your env vars first."
        )
        return
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now_her = datetime.now(safe_zoneinfo(settings.girlfriend_timezone))
    events = await get_girlfriend_events(credentials, now_her, state, days=GF_SCHEDULE_TODAY_DAYS)
    text = _format_gf_dual_tz(events, settings.girlfriend_timezone, state.timezone)
    await update.effective_message.reply_text(text)


# --- adding events via free text ----------------------------------------

CATEGORY_PICKER_ROWS = [["WORK", "!!!"], ["MEET", "DL"], ["SCH", "LEI"], ["GF", "OTH"]]


def _category_picker_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(code, callback_data=f"catpick:{code}") for code in row]
        for row in CATEGORY_PICKER_ROWS
    ]
    rows.append([InlineKeyboardButton("❌ Discard", callback_data="catpick:discard")])
    return InlineKeyboardMarkup(rows)


def _pending_event_summary(parsed) -> str:
    lines = [f"📝 {parsed.title}"]
    time_part = f"{parsed.start.strftime('%a %d %b, %H:%M')}–{parsed.end.strftime('%H:%M')}"
    if parsed.recurrence_rrule:
        time_part += f" (repeats {describe_rrule(parsed.recurrence_rrule)})"
    if parsed.time_was_guessed:
        time_part += " (guessed 9am — no time was given)"
    lines.append(time_part)
    if parsed.description:
        lines.append(f"📝 {parsed.description}")
    lines.append("\nPick a category:")
    return "\n".join(lines)


async def _handle_cancel_by_description(
    update: Update, context: ContextTypes.DEFAULT_TYPE, target_date_str: str, target_keyword: str
) -> None:
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        await update.effective_message.reply_text(
            "Couldn't figure out which date that's for — try being more specific, e.g. "
            "'cancel BTM480 on 9 October'."
        )
        return

    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
    events = await gcal.list_events(credentials, state.all_calendar_ids(), start, start + timedelta(days=1))

    if not events:
        await update.effective_message.reply_text(f"No events found on {target_date.strftime('%d %b')}.")
        return

    keyword = (target_keyword or "").strip().lower()
    matches = [e for e in events if keyword and keyword in strip_tag(e.summary).lower()]

    if len(matches) == 1:
        e = matches[0]
        await gcal.delete_event(credentials, e.calendar_id, e.id)
        await update.effective_message.reply_text(
            f"🗑 Cancelled: {strip_tag(e.summary)} on {target_date.strftime('%d %b')}."
        )
        return

    text, keyboard = await _build_picker(context, mode="delete", list_mode="date", target_date=target_date)
    intro = "Found more than one possible match" if len(matches) > 1 else "Couldn't pin down which one"
    await update.effective_message.reply_text(
        f"{intro} — here's {target_date.strftime('%d %b')}'s schedule, pick one to cancel:",
        reply_markup=keyboard,
    )


# How far ahead to search for matches -- long enough to cover a full recurring-class
# semester (e.g. "until December" from September), short enough to stay a fast query.
BULK_ACTION_WINDOW_DAYS = 365


def _bulk_match_events(events: list, category: str, keywords: list[str]) -> list:
    def _matches(e) -> bool:
        if category and get_category(e.summary) != category:
            return False
        if not keywords:
            return True
        title = strip_tag(e.summary).lower()
        return any(kw.lower() in title for kw in keywords)

    return [e for e in events if _matches(e)]


async def _fetch_upcoming_for_bulk_action(context: ContextTypes.DEFAULT_TYPE) -> tuple[list, BotState]:
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(safe_zoneinfo(state.timezone))
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    events = await list_events_cached(
        credentials, state.all_calendar_ids(), today_start, today_start + timedelta(days=BULK_ACTION_WINDOW_DAYS)
    )
    return events, state


def _bulk_selection_keyboard(plan: list[dict], selected: set[int], prefix: str) -> InlineKeyboardMarkup:
    """Per-item checklist (✅/⬜, tap to toggle) instead of an all-or-nothing Confirm --
    shared by bulk-delete and bulk-edit-time so you can pick exactly which matches to
    actually apply."""
    rows = []
    for idx, item in enumerate(plan):
        mark = "✅" if idx in selected else "⬜"
        rows.append([InlineKeyboardButton(f"{mark} {item['label']}", callback_data=f"{prefix}:toggle:{idx}")])
    rows.append([
        InlineKeyboardButton("Select all", callback_data=f"{prefix}:all"),
        InlineKeyboardButton("Select none", callback_data=f"{prefix}:none"),
    ])
    rows.append([
        InlineKeyboardButton("✅ Apply", callback_data=f"{prefix}:confirm"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"{prefix}:cancel"),
    ])
    return InlineKeyboardMarkup(rows)


async def _handle_bulk_selection_toggle(
    query, context: ContextTypes.DEFAULT_TYPE, pending_key: str, prefix: str, action: str, idx: int | None
) -> bool:
    """Handles the toggle/all/none sub-actions shared by both bulk flows' callbacks.
    Returns True if it handled the tap (caller should return immediately after),
    False if `action` wasn't one of these (caller should fall through to confirm/cancel)."""
    if action not in ("toggle", "all", "none"):
        return False
    pending = context.user_data.get(pending_key)
    if not pending:
        await query.edit_message_text("That request expired — try again.")
        return True
    selected: set[int] = pending["selected"]
    if action == "toggle":
        selected.discard(idx) if idx in selected else selected.add(idx)
    elif action == "all":
        selected.clear()
        selected.update(range(len(pending["plan"])))
    elif action == "none":
        selected.clear()
    await query.edit_message_reply_markup(reply_markup=_bulk_selection_keyboard(pending["plan"], selected, prefix))
    return True


async def _handle_bulk_delete_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, keywords: list[str]
) -> None:
    if not category and not keywords:
        await update.effective_message.reply_text(
            "Couldn't tell what to match — name a category and/or keyword, e.g. 'delete "
            "all BTM and COMP [SCH] events coming up'."
        )
        return

    events, state = await _fetch_upcoming_for_bulk_action(context)
    now = datetime.now(safe_zoneinfo(state.timezone))
    matches = _bulk_match_events(events, category, keywords)
    if not matches:
        desc = " and ".join(keywords) if keywords else ""
        cat_note = f"[{category}] " if category else ""
        await update.effective_message.reply_text(f"Nothing upcoming matches {cat_note}{desc}.")
        return

    # One-off events get deleted outright; recurring series are only truncated from
    # today onward (past occurrences are left alone as history), per how you asked this
    # to behave -- so group matched instances by their master series and act on that
    # master once, rather than per matched instance.
    plan: list[dict] = []
    seen_masters: set[str] = set()
    for e in matches:
        if e.recurring_event_id:
            if e.recurring_event_id in seen_masters:
                continue
            seen_masters.add(e.recurring_event_id)
            # The matched instance's own weekday is the series' recurring day (weekly
            # RRULEs built by this bot always share DTSTART's weekday).
            weekday = "" if e.is_all_day else datetime.fromisoformat(e.start).strftime("%A")
            day_note = f", {weekday}s" if weekday else ""
            plan.append({
                "action": "truncate", "calendar_id": e.calendar_id, "master_event_id": e.recurring_event_id,
                "label": f"{strip_tag(e.summary)} (recurring{day_note}, stops today)",
            })
        else:
            when = "(all day)" if e.is_all_day else datetime.fromisoformat(e.start).strftime("%a %d %b, %H:%M")
            plan.append({
                "action": "delete", "calendar_id": e.calendar_id, "event_id": e.id,
                "label": f"{strip_tag(e.summary)} — {when}",
            })

    context.user_data["pending_bulk_delete"] = {
        "plan": plan, "today": now.date().isoformat(), "selected": set(range(len(plan))),
    }
    await update.effective_message.reply_text(
        f"Found {len(plan)} match(es) — tap to deselect any, then Apply:",
        reply_markup=_bulk_selection_keyboard(plan, set(range(len(plan))), "bulkdel"),
    )


async def bulk_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    if await _handle_bulk_selection_toggle(
        query, context, "pending_bulk_delete", "bulkdel", action, int(parts[2]) if action == "toggle" else None
    ):
        return

    pending = context.user_data.pop("pending_bulk_delete", None)
    if action == "cancel" or not pending:
        await query.edit_message_text("Cancelled — nothing deleted.")
        return

    selected_plan = [pending["plan"][i] for i in sorted(pending["selected"])]
    if not selected_plan:
        await query.edit_message_text("Nothing selected — nothing deleted.")
        return

    credentials = _get_credentials(context)
    last_occurrence_date = date.fromisoformat(pending["today"]) - timedelta(days=1)
    deleted = truncated = failed = 0
    for item in selected_plan:
        try:
            if item["action"] == "delete":
                await gcal.delete_event(credentials, item["calendar_id"], item["event_id"])
                deleted += 1
            else:
                recurrence = await gcal.get_event_recurrence(credentials, item["calendar_id"], item["master_event_id"])
                if not recurrence:
                    failed += 1
                    continue
                new_recurrence = [
                    truncate_rrule(r, last_occurrence_date) if r.startswith("RRULE") else r for r in recurrence
                ]
                await gcal.update_event_recurrence(credentials, item["calendar_id"], item["master_event_id"], new_recurrence)
                truncated += 1
        except Exception:
            logger.warning("Bulk-delete action failed for %s", item, exc_info=True)
            failed += 1

    summary = f"✅ Deleted {deleted} one-off event(s), truncated {truncated} recurring series."
    if failed:
        summary += f" {failed} failed — they may have already been removed."
    await query.edit_message_text(summary)


NEW_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


async def _handle_bulk_edit_time_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE, category: str, keywords: list[str], new_time_str: str
) -> None:
    if not category and not keywords:
        await update.effective_message.reply_text(
            "Couldn't tell what to match — name a category and/or keyword, e.g. 'move "
            "all BTM [SCH] classes to 6pm'."
        )
        return
    match = NEW_TIME_RE.match(new_time_str or "")
    if not match:
        await update.effective_message.reply_text(
            "Couldn't tell what time to move it to — try being explicit, e.g. 'move all "
            "BTM [SCH] classes to 18:00'."
        )
        return
    new_hour, new_minute = int(match.group(1)), int(match.group(2))

    events, state = await _fetch_upcoming_for_bulk_action(context)
    credentials = _get_credentials(context)
    matches = _bulk_match_events(events, category, keywords)
    if not matches:
        desc = " and ".join(keywords) if keywords else ""
        cat_note = f"[{category}] " if category else ""
        await update.effective_message.reply_text(f"Nothing upcoming matches {cat_note}{desc}.")
        return

    # A recurring match is edited once via its master -- moves the whole series' clock
    # time, past and future alike, since every generated occurrence derives its
    # time-of-day from the master's own start (per how you asked this to behave, unlike
    # bulk-delete's today-onward-only truncation).
    plan: list[dict] = []
    seen_targets: set[str] = set()
    for e in matches:
        target_event_id = e.recurring_event_id or e.id
        if target_event_id in seen_targets:
            continue
        seen_targets.add(target_event_id)

        raw = await gcal.get_event(credentials, e.calendar_id, target_event_id)
        start_raw = raw.get("start", {})
        end_raw = raw.get("end", {})
        if "dateTime" not in start_raw:
            continue  # all-day event -- no time-of-day to move

        old_start = datetime.fromisoformat(start_raw["dateTime"])
        old_end = datetime.fromisoformat(end_raw["dateTime"])
        duration = old_end - old_start
        new_start = old_start.replace(hour=new_hour, minute=new_minute, second=0, microsecond=0)
        new_end = new_start + duration
        if e.recurring_event_id:
            day_part = f"{old_start.strftime('%A')}s, whole series"
        else:
            day_part = f"{old_start.strftime('%a %d %b')}, one-off"
        plan.append({
            "calendar_id": e.calendar_id,
            "event_id": target_event_id,
            "new_start": new_start.replace(tzinfo=None).isoformat(),
            "new_end": new_end.replace(tzinfo=None).isoformat(),
            "label": f"{strip_tag(e.summary)} {old_start.strftime('%H:%M')}→{new_start.strftime('%H:%M')} ({day_part})",
        })

    if not plan:
        await update.effective_message.reply_text(
            "Found matches, but none have a time-of-day to move (all-day events)."
        )
        return

    context.user_data["pending_bulk_edit"] = {"plan": plan, "tz": state.timezone, "selected": set(range(len(plan)))}
    await update.effective_message.reply_text(
        f"Found {len(plan)} match(es) — tap to deselect any, then Apply:",
        reply_markup=_bulk_selection_keyboard(plan, set(range(len(plan))), "bulkedit"),
    )


async def bulk_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    if await _handle_bulk_selection_toggle(
        query, context, "pending_bulk_edit", "bulkedit", action, int(parts[2]) if action == "toggle" else None
    ):
        return

    pending = context.user_data.pop("pending_bulk_edit", None)
    if action == "cancel" or not pending:
        await query.edit_message_text("Cancelled — nothing changed.")
        return

    selected_plan = [pending["plan"][i] for i in sorted(pending["selected"])]
    if not selected_plan:
        await query.edit_message_text("Nothing selected — nothing changed.")
        return

    credentials = _get_credentials(context)
    tz = pending["tz"]
    updated = failed = 0
    for item in selected_plan:
        try:
            new_start = datetime.fromisoformat(item["new_start"])
            new_end = datetime.fromisoformat(item["new_end"])
            await gcal.update_event_time(credentials, item["calendar_id"], item["event_id"], new_start, new_end, tz)
            updated += 1
        except Exception:
            logger.warning("Bulk-edit action failed for %s", item, exc_info=True)
            failed += 1

    summary = f"✅ Updated {updated} event(s)/series."
    if failed:
        summary += f" {failed} failed — check they still exist."
    await query.edit_message_text(summary)


async def _handle_new_event_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    state = _get_state(context)
    now = datetime.now(safe_zoneinfo(state.timezone)).replace(tzinfo=None)
    parsed = await parse_event_with_ai(text, now=now)
    if parsed is None:
        await update.effective_message.reply_text(
            "Didn't catch a date/time in that. Try phrasing it like 'dinner with sam fri "
            "7pm' (date + time together, ideally at the end, works best). Or use /today, "
            "/week, /edit, /cancel, /calendars, /help."
        )
        return

    if parsed.intent == "cancel":
        await _handle_cancel_by_description(update, context, parsed.cancel_target_date, parsed.cancel_target_keyword)
        return

    if parsed.intent == "bulk_delete":
        await _handle_bulk_delete_request(update, context, parsed.bulk_delete_category, parsed.bulk_delete_keywords)
        return

    if parsed.intent == "bulk_edit_time":
        await _handle_bulk_edit_time_request(
            update, context, parsed.bulk_edit_category, parsed.bulk_edit_keywords, parsed.bulk_edit_new_time
        )
        return

    state = await _ensure_calendars_synced(context)
    calendar_id = state.primary_calendar_id()
    if not calendar_id:
        await update.effective_message.reply_text("No Google calendars found — check your Google account.")
        return

    context.user_data["pending_event"] = {
        "title": parsed.title,
        "start": parsed.start,
        "end": parsed.end,
        "description": parsed.description,
        "calendar_id": calendar_id,
        "time_was_guessed": parsed.time_was_guessed,
        "recurrence_rrule": parsed.recurrence_rrule,
    }
    await update.effective_message.reply_text(
        _pending_event_summary(parsed), reply_markup=_category_picker_keyboard()
    )


async def category_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1]
    pending = context.user_data.get("pending_event")
    if not pending:
        await query.edit_message_text("That request expired — text the event again.")
        return
    if code == "discard":
        await query.edit_message_text("Discarded.")
        context.user_data.pop("pending_event", None)
        return

    credentials = _get_credentials(context)
    state = _get_state(context)
    tagged_title = f"[{code}] {pending['title']}"
    recurrence = [pending["recurrence_rrule"]] if pending["recurrence_rrule"] else None
    event = await gcal.create_event(
        credentials, pending["calendar_id"], tagged_title, pending["start"], pending["end"],
        state.timezone, description=pending["description"], recurrence=recurrence,
    )

    guess_note = " (guessed 9am since no time was given — /edit to fix)" if pending["time_was_guessed"] else ""
    reply_text = (
        f"✅ {event.summary}\n"
        f"{pending['start'].strftime('%a %d %b, %H:%M')}–{pending['end'].strftime('%H:%M')}{guess_note}"
    )
    if pending["recurrence_rrule"]:
        reply_text += f"\n🔁 {describe_rrule(pending['recurrence_rrule'])}"
    if pending["description"]:
        reply_text += f"\n📝 {pending['description']}"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("↩️ Undo", callback_data=f"undo:{pending['calendar_id']}|{event.id}")]]
    )
    await query.edit_message_text(reply_text, reply_markup=keyboard)
    context.user_data.pop("pending_event", None)


@restricted
async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _handle_new_event_text(update, context, update.message.text.strip())


@restricted
async def add_alias(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /add <event text>, e.g. /add dinner with sam fri 7pm")
        return
    await _handle_new_event_text(update, context, " ".join(context.args))


async def undo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    payload = query.data.split(":", 1)[1]
    calendar_id, event_id = payload.split("|", 1)
    credentials = _get_credentials(context)
    await gcal.delete_event(credentials, calendar_id, event_id)
    await query.edit_message_text("↩️ Undone — event deleted.")


# --- calendar share/private toggle --------------------------------------

def _calendars_keyboard(state: BotState) -> InlineKeyboardMarkup:
    rows = []
    for cal in state.calendars.values():
        tag = "🌐 SHARE" if cal.is_shareable else "🔒 PRIVATE"
        star = " ★" if cal.is_primary else ""
        rows.append([InlineKeyboardButton(f"{tag} — {cal.name}{star}", callback_data=f"cal_toggle:{cal.id}")])
    return InlineKeyboardMarkup(rows)


@restricted
async def calendars_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    await gcal.sync_calendars_into_state(_get_credentials(context), state)
    save_state(state)
    await update.effective_message.reply_text(
        "Tap a calendar to toggle Share / Private.\n\n"
        "🌐 SHARE = will feed HowAbout once that integration exists.\n"
        "🔒 PRIVATE = never leaves this bot.",
        reply_markup=_calendars_keyboard(state),
    )


async def calendar_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    calendar_id = query.data.split(":", 1)[1]
    state = _get_state(context)
    cal = state.calendars.get(calendar_id)
    if cal is None:
        await query.answer("Not found.", show_alert=True)
        return
    cal.is_shareable = not cal.is_shareable
    save_state(state)
    await query.edit_message_reply_markup(reply_markup=_calendars_keyboard(state))


@restricted
async def timezone_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    if not context.args:
        await update.effective_message.reply_text(
            f"Current timezone: {state.timezone}\n\n"
            "Usage: /timezone <IANA name>, e.g. /timezone America/Toronto or "
            "/timezone Asia/Singapore. Find yours at "
            "https://en.wikipedia.org/wiki/List_of_tz_database_time_zones — this "
            "affects every time-of-day feature: event creation, /today, /tomorrow, the "
            "nightly preview, and the girlfriend summary window."
        )
        return
    tz_name = context.args[0].strip()
    try:
        ZoneInfo(tz_name)
    except Exception:
        await update.effective_message.reply_text(
            f"Didn't recognize '{tz_name}' as a timezone — use a full IANA name like "
            "America/Toronto. Find yours at "
            "https://en.wikipedia.org/wiki/List_of_tz_database_time_zones"
        )
        return
    state.timezone = tz_name
    save_state(state)
    await update.effective_message.reply_text(f"Timezone set to {tz_name}.")


@restricted
async def night_agenda_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    state.night_agenda_enabled = True
    save_state(state)
    await update.effective_message.reply_text(
        f"Nightly 'tomorrow's schedule' reminder enabled — sent around {state.night_agenda_time}."
    )


@restricted
async def night_agenda_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    state.night_agenda_enabled = False
    save_state(state)
    await update.effective_message.reply_text("Nightly reminder disabled.")


@restricted
async def night_agenda_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not re.match(r"^\d{2}:\d{2}$", context.args[0]):
        await update.effective_message.reply_text(
            "Usage: /night_agenda_set HH:MM (24h, in your timezone), e.g. /night_agenda_set 23:30"
        )
        return
    state = _get_state(context)
    state.night_agenda_time = context.args[0]
    save_state(state)
    await update.effective_message.reply_text(
        f"Nightly reminder time set to {state.night_agenda_time} ({state.timezone})."
    )


@restricted
async def girlfriend_agenda_on(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = get_settings()
    if not settings.girlfriend_email and not settings.girlfriend_howabout_calendar_name:
        await update.effective_message.reply_text(
            "Neither GIRLFRIEND_EMAIL nor GIRLFRIEND_HOWABOUT_CALENDAR_NAME is set — "
            "add one to your env vars first."
        )
        return
    state = _get_state(context)
    state.girlfriend_agenda_enabled = True
    save_state(state)
    await update.effective_message.reply_text(
        f"Girlfriend's schedule summary enabled — sent around {state.girlfriend_agenda_time}, previewing her next day."
    )


@restricted
async def girlfriend_agenda_off(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = _get_state(context)
    state.girlfriend_agenda_enabled = False
    save_state(state)
    await update.effective_message.reply_text("Girlfriend's schedule summary disabled.")


@restricted
async def girlfriend_agenda_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args or not re.match(r"^\d{2}:\d{2}$", context.args[0]):
        await update.effective_message.reply_text(
            "Usage: /girlfriend_agenda_set HH:MM (24h, in your timezone), e.g. /girlfriend_agenda_set 17:30"
        )
        return
    state = _get_state(context)
    state.girlfriend_agenda_time = context.args[0]
    save_state(state)
    await update.effective_message.reply_text(
        f"Girlfriend summary time set to {state.girlfriend_agenda_time} ({state.timezone})."
    )


# --- /categorize: bulk backfill category tags on existing events ---------

CATEGORIZE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"category": {"type": "STRING", "enum": CATEGORY_CODES}},
    "required": ["category"],
}

# Keeps one webhook request well under Telegram's delivery timeout even with a
# sizeable backlog -- /categorize is idempotent (already-tagged events are skipped),
# so re-running it just picks up where the previous run left off.
MAX_EVENTS_PER_CATEGORIZE_RUN = 20


def _categorize_prompt(title: str) -> str:
    return f'Classify this calendar event into exactly one category. Title: "{title}". {category_hint()}'


@restricted
async def categorize_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    now = datetime.now(timezone.utc)
    events = await list_events_cached(credentials, state.all_calendar_ids(), now, now + timedelta(days=60))

    to_tag = [e for e in events if not is_tagged(e.summary)]
    if not to_tag:
        await update.effective_message.reply_text("Nothing to categorize — everything upcoming is already tagged.")
        return

    batch = to_tag[:MAX_EVENTS_PER_CATEGORIZE_RUN]
    await update.effective_message.reply_text(f"Categorizing {len(batch)} event(s)...")

    tally: dict[str, int] = {}
    failed = 0
    for e in batch:
        result = await gemini_client.generate_json(_categorize_prompt(e.summary), CATEGORIZE_SCHEMA)
        category = result.get("category") if result else None
        if category not in CATEGORY_CODES:
            failed += 1
            continue
        await gcal.update_event_summary(credentials, e.calendar_id, e.id, f"[{category}] {e.summary}")
        tally[category] = tally.get(category, 0) + 1

    summary_lines = [f"{count} {code}" for code, count in sorted(tally.items(), key=lambda kv: -kv[1])]
    reply = f"Categorized {sum(tally.values())} event(s): " + ", ".join(summary_lines)
    if failed:
        reply += f"\n{failed} couldn't be classified — run /categorize again to retry."
    remaining = len(to_tag) - len(batch)
    if remaining:
        reply += f"\n{remaining} more still untagged — run /categorize again to continue."
    await update.effective_message.reply_text(reply)


# --- /edit and /cancel: pick an event, either recently-added or upcoming ---

PICKER_LIST_LABELS = {"recent": "recently added event", "upcoming": "upcoming event"}


async def _build_picker(
    context: ContextTypes.DEFAULT_TYPE, mode: str, list_mode: str, target_date: date | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    state = await _ensure_calendars_synced(context)
    credentials = _get_credentials(context)
    if list_mode == "recent":
        # Most recently *added* (by creation time), not soonest upcoming -- lets you
        # quickly find whatever you just texted in, wherever it's scheduled.
        events = await gcal.list_recently_created_events(credentials, state.all_calendar_ids(), limit=10)
    elif list_mode == "date":
        # A specific day's events -- used by the cancel-by-description fallback when
        # the keyword match was ambiguous or missing. No natural "other view" toggle.
        target_date = target_date or context.user_data.get("picker_date")
        start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
        events = await gcal.list_events(credentials, state.all_calendar_ids(), start, start + timedelta(days=1))
    else:
        now = datetime.now(timezone.utc)
        events = await list_events_cached(credentials, state.all_calendar_ids(), now, now + timedelta(days=14))
        events = events[:10]

    if list_mode == "date":
        context.user_data["picker_date"] = target_date

    context.user_data["picker_events"] = {
        str(idx): (e.calendar_id, e.id, e.summary) for idx, e in enumerate(events)
    }
    context.user_data["picker_mode"] = mode
    context.user_data["picker_list_mode"] = list_mode

    prefix = "pickedit" if mode == "edit" else "pickdel"
    verb = "edit" if mode == "edit" else "cancel"
    rows = []
    for idx, e in enumerate(events):
        label = e.summary if e.is_all_day else f"{datetime.fromisoformat(e.start).strftime('%a %d %b %H:%M')} {e.summary}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"{prefix}:{idx}")])

    if list_mode in ("recent", "upcoming"):
        other_list_mode = "upcoming" if list_mode == "recent" else "recent"
        toggle_label = "🕐 Show upcoming instead" if list_mode == "recent" else "🆕 Show recently added instead"
        rows.append([InlineKeyboardButton(toggle_label, callback_data=f"picktoggle:{mode}:{other_list_mode}")])

    if list_mode == "date":
        label = f"event on {target_date.strftime('%d %b')}"
    else:
        label = PICKER_LIST_LABELS[list_mode]
    if events:
        text = f"Pick a {label} to {verb}:" if list_mode != "date" else f"Pick which {target_date.strftime('%d %b')} event to {verb}:"
    else:
        text = f"No {label}s found." if list_mode != "date" else f"No events found on {target_date.strftime('%d %b')}."
        if list_mode in ("recent", "upcoming"):
            text += " Try the other view below."
    return text, InlineKeyboardMarkup(rows)


@restricted
async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = await _build_picker(context, mode="edit", list_mode="recent")
    await update.effective_message.reply_text(text, reply_markup=keyboard)


@restricted
async def cancel_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text, keyboard = await _build_picker(context, mode="delete", list_mode="upcoming")
    await update.effective_message.reply_text(text, reply_markup=keyboard)


async def picker_toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    _, mode, list_mode = query.data.split(":", 2)
    text, keyboard = await _build_picker(context, mode=mode, list_mode=list_mode)
    await query.edit_message_text(text, reply_markup=keyboard)


async def edit_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = query.data.split(":", 1)[1]
    entry = context.user_data.get("picker_events", {}).get(idx)
    if not entry:
        await query.edit_message_text("That picker expired, run /edit again.")
        return ConversationHandler.END
    cal_id, event_id, summary = entry
    context.user_data["editing_event"] = (cal_id, event_id)
    rows = [
        [InlineKeyboardButton("✏️ Rename", callback_data="editaction:rename")],
        [InlineKeyboardButton("🗑 Delete", callback_data="editaction:delete")],
        [InlineKeyboardButton("⬅️ Back", callback_data="editaction:back")],
    ]
    await query.edit_message_text(f"Editing: {summary}\nWhat do you want to do?", reply_markup=InlineKeyboardMarkup(rows))
    return ConversationHandler.END


async def edit_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data.split(":", 1)[1]
    if action == "back":
        list_mode = context.user_data.get("picker_list_mode", "recent")
        text, keyboard = await _build_picker(context, mode="edit", list_mode=list_mode)
        await query.edit_message_text(text, reply_markup=keyboard)
        context.user_data.pop("editing_event", None)
        return ConversationHandler.END
    cal_id, event_id = context.user_data.get("editing_event", (None, None))
    if not cal_id:
        await query.edit_message_text("Session expired, run /edit again.")
        return ConversationHandler.END
    if action == "delete":
        credentials = _get_credentials(context)
        await gcal.delete_event(credentials, cal_id, event_id)
        await query.edit_message_text("🗑 Deleted.")
        context.user_data.pop("editing_event", None)
        return ConversationHandler.END
    await query.edit_message_text(
        "Send the new title:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="renameback")]]),
    )
    return RENAME_TEXT


async def rename_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_title = update.message.text.strip()
    cal_id, event_id = context.user_data.get("editing_event", (None, None))
    if not cal_id:
        await update.message.reply_text("Session expired, run /edit again.")
        return ConversationHandler.END
    credentials = _get_credentials(context)
    event = await gcal.update_event_summary(credentials, cal_id, event_id, new_title)
    await update.message.reply_text(f"✅ Renamed to: {event.summary}")
    context.user_data.pop("editing_event", None)
    return ConversationHandler.END


async def rename_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    list_mode = context.user_data.get("picker_list_mode", "recent")
    text, keyboard = await _build_picker(context, mode="edit", list_mode=list_mode)
    await query.edit_message_text(text, reply_markup=keyboard)
    context.user_data.pop("editing_event", None)
    return ConversationHandler.END


async def flow_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("editing_event", None)
    await update.effective_message.reply_text("Okay, cancelled.")
    return ConversationHandler.END


rename_conversation = ConversationHandler(
    entry_points=[CallbackQueryHandler(edit_action_callback, pattern=r"^editaction:")],
    states={
        RENAME_TEXT: [
            CallbackQueryHandler(rename_back_callback, pattern=r"^renameback$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, rename_text),
        ],
    },
    fallbacks=[CommandHandler("stop", flow_stop)],
    name="rename_conversation",
    per_message=False,
)


async def delete_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    idx = query.data.split(":", 1)[1]
    entry = context.user_data.get("picker_events", {}).get(idx)
    if not entry:
        await query.edit_message_text("That picker expired, run /cancel again.")
        return
    cal_id, event_id, summary = entry
    context.user_data["deleting_event"] = (cal_id, event_id)
    rows = [
        [InlineKeyboardButton("✅ Yes, delete", callback_data="confirmdel:yes")],
        [InlineKeyboardButton("❌ No", callback_data="confirmdel:no")],
        [InlineKeyboardButton("⬅️ Back", callback_data="confirmdel:back")],
    ]
    await query.edit_message_text(f"Delete '{summary}'?", reply_markup=InlineKeyboardMarkup(rows))


async def confirm_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]
    if choice == "back":
        list_mode = context.user_data.get("picker_list_mode", "upcoming")
        text, keyboard = await _build_picker(context, mode="delete", list_mode=list_mode)
        await query.edit_message_text(text, reply_markup=keyboard)
        context.user_data.pop("deleting_event", None)
        return
    if choice == "no":
        await query.edit_message_text("Kept.")
        context.user_data.pop("deleting_event", None)
        return
    cal_id, event_id = context.user_data.pop("deleting_event", (None, None))
    if not cal_id:
        await query.edit_message_text("Session expired.")
        return
    credentials = _get_credentials(context)
    await gcal.delete_event(credentials, cal_id, event_id)
    await query.edit_message_text("🗑 Deleted.")


# --- registration ---------------------------------------------------------

def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("today", today))
    application.add_handler(CommandHandler("tomorrow", tomorrow))
    application.add_handler(CommandHandler("week", week))
    application.add_handler(CommandHandler("next", next_event))
    application.add_handler(CommandHandler("add", add_alias))
    application.add_handler(CommandHandler("upcoming_workstuff", upcoming_workstuff))
    application.add_handler(CommandHandler("upcoming_socials", upcoming_socials))
    application.add_handler(CommandHandler("gf", gf_cmd))
    application.add_handler(CommandHandler("gf_schedule", gf_schedule_cmd))
    application.add_handler(CommandHandler("gf_schedule_today", gf_schedule_today_cmd))
    application.add_handler(CommandHandler("calendars", calendars_cmd))
    application.add_handler(CallbackQueryHandler(calendar_toggle_callback, pattern=r"^cal_toggle:"))
    application.add_handler(CommandHandler("timezone", timezone_cmd))
    application.add_handler(CommandHandler("night_agenda_on", night_agenda_on))
    application.add_handler(CommandHandler("night_agenda_off", night_agenda_off))
    application.add_handler(CommandHandler("night_agenda_set", night_agenda_set))
    application.add_handler(CommandHandler("girlfriend_agenda_on", girlfriend_agenda_on))
    application.add_handler(CommandHandler("girlfriend_agenda_off", girlfriend_agenda_off))
    application.add_handler(CommandHandler("girlfriend_agenda_set", girlfriend_agenda_set))
    application.add_handler(CommandHandler("categorize", categorize_cmd))
    application.add_handler(CommandHandler("edit", edit_start))
    application.add_handler(CommandHandler("cancel", cancel_event_start))
    application.add_handler(CallbackQueryHandler(picker_toggle_callback, pattern=r"^picktoggle:"))
    application.add_handler(CallbackQueryHandler(edit_pick_callback, pattern=r"^pickedit:"))
    application.add_handler(rename_conversation)
    application.add_handler(CallbackQueryHandler(delete_pick_callback, pattern=r"^pickdel:"))
    application.add_handler(CallbackQueryHandler(confirm_delete_callback, pattern=r"^confirmdel:"))
    application.add_handler(CallbackQueryHandler(undo_callback, pattern=r"^undo:"))
    application.add_handler(CallbackQueryHandler(category_pick_callback, pattern=r"^catpick:"))
    application.add_handler(CallbackQueryHandler(bulk_delete_callback, pattern=r"^bulkdel:"))
    application.add_handler(CallbackQueryHandler(bulk_edit_callback, pattern=r"^bulkedit:"))
    application.add_handler(
        MessageHandler(filters.Regex(f"^{re.escape(CALENDAR_BUTTON_TEXT)}$"), calendar_button_pressed)
    )
    application.add_handler(
        MessageHandler(filters.Regex(f"^{re.escape(HOWABOUT_BUTTON_TEXT)}$"), howabout_button_pressed)
    )
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text))
