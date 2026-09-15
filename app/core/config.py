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

    # Public URL of the deployed webhook service, used to register it with Telegram.
    public_base_url: str = ""
    # Secret Telegram echoes back on every webhook call, to reject spoofed requests.
    telegram_webhook_secret: str = ""

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/telegram/webhook"
    howabout_api_key: str = "PLACEHOLDER_HOWABOUT_API_KEY"
    howabout_base_url: str = "https://PLACEHOLDER.howabout.example/api"
    # Shown as a button in the nightly reminder once set to a real URL.
    howabout_app_link: str = "PLACEHOLDER_HOWABOUT_LINK"

    # How the bot addresses you in daily briefs. Blank defaults to "there".
    owner_greeting_name: str = ""

    # Up to 3 comma-separated Gemini keys, tried in order. Blank disables AI parsing.
    gemini_api_keys: str = ""
    gemini_model: str = "gemini-3.6-flash"

    # Comma-separated name + nicknames -- used as her display name and a Gemini hint.
    girlfriend_name: str = ""

    # Her shared Google Calendar ID (= her email). Blank disables this source.
    girlfriend_email: str = ""

    # Name of a calendar subscribed via Google Calendar > Add calendar > From URL.
    girlfriend_howabout_calendar_name: str = ""

    # Her timezone, for /gf_schedule_today's dual-timezone display.
    girlfriend_timezone: str = "UTC"

    # YYYY-MM-DD, for /gf's countdown. Blank means /gf just says it isn't set.
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
        """First name from GIRLFRIEND_NAME, or "Partner" if unset."""
        names = self.girlfriend_name_list
        return names[0] if names else "Partner"

    @property
    def greeting_name(self) -> str:
        return self.owner_greeting_name.strip() or "there"


@lru_cache
def get_settings() -> Settings:
    return Settings()
