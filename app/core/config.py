from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str
    google_client_id: str
    google_client_secret: str
    google_refresh_token: str = ""
    owner_telegram_user_id: int
    state_file_path: str = "./data/state.json"

    # Public URL of the deployed webhook service (Railway domain in prod; a tunnel URL
    # like ngrok for local testing). Used to register the Telegram webhook.
    public_base_url: str = ""
    # Random secret Telegram echoes back in a header on every webhook call, so we can
    # reject requests that don't come from Telegram. Leave empty to disable the check.
    telegram_webhook_secret: str = ""

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/telegram/webhook"
    howabout_api_key: str = "PLACEHOLDER_HOWABOUT_API_KEY"
    howabout_base_url: str = "https://PLACEHOLDER.howabout.example/api"
    # Link shown in the nightly reminder. Left as this placeholder, the line is omitted
    # from the message entirely until you set a real URL.
    howabout_app_link: str = "PLACEHOLDER_HOWABOUT_LINK"

    # How the bot addresses you in the full-day briefs (/today, the nightly preview).
    # Leave blank for a generic "there".
    owner_greeting_name: str = ""

    # Comma-separated free-tier Gemini API keys, tried in order. Empty = AI parsing
    # disabled, bot falls back straight to the local dateparser-based parser.
    gemini_api_keys: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # Optional, comma-separated first name + nicknames (e.g. "Alex,babe") -- improves
    # GF category classification accuracy (event parsing mentioning any of these -> GF)
    # and is used as the display name in girlfriend-related messages (e.g. "Alex's
    # Schedule for tomorrow"). Leave blank to rely on generic relationship-language
    # heuristics and a generic "Partner" display name.
    girlfriend_name: str = ""

    # Her Google Calendar ID (= her email) -- must already be shared with your Google
    # account ("See all event details"). Empty disables the girlfriend-summary feature
    # entirely. Never mixed into your own /today, /week, /next, or /categorize.
    girlfriend_email: str = ""

    # Exact name of a calendar you've subscribed to in Google Calendar (Settings > Add
    # calendar > From URL) pointing at her HowAbout public calendar feed. Matched by
    # name (not email) since subscribed/ICS calendars don't have a predictable ID.
    # Also excluded from your own personal views, same as girlfriend_email's calendar.
    # Empty disables this source (same convention as girlfriend_email).
    girlfriend_howabout_calendar_name: str = ""

    # Her timezone, used by /gf_schedule_today to show each event's time in both her
    # zone and yours (state.timezone) side by side, since the two can be far enough
    # apart that "her day" and "your day" don't line up. Defaults to UTC (neutral) --
    # set this to get a meaningful dual-timezone display.
    girlfriend_timezone: str = "UTC"

    # Fixed dates for /gf's countdown, format YYYY-MM-DD (the actual/original date --
    # next occurrence is computed from the month/day). Leave either blank to have /gf
    # say it isn't set rather than error.
    anniversary_date: str = ""
    partner_birthday_date: str = ""

    @property
    def gemini_api_key_list(self) -> list[str]:
        return [k.strip() for k in self.gemini_api_keys.split(",") if k.strip()]

    @property
    def girlfriend_name_list(self) -> list[str]:
        return [n.strip() for n in self.girlfriend_name.split(",") if n.strip()]

    @property
    def girlfriend_display_name(self) -> str:
        """First name from GIRLFRIEND_NAME, used in girlfriend-related message text
        (e.g. "Alex's Schedule for tomorrow"). Falls back to a generic "Partner" if
        unset, rather than hardcoding any particular name."""
        names = self.girlfriend_name_list
        return names[0] if names else "Partner"

    @property
    def greeting_name(self) -> str:
        return self.owner_greeting_name.strip() or "there"


@lru_cache
def get_settings() -> Settings:
    return Settings()
