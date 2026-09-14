from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.core.config import get_settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def build_installed_app_flow() -> InstalledAppFlow:
    """Used only by scripts/google_auth_setup.py for the one-time local consent."""
    settings = get_settings()
    client_config = {
        "installed": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }
    return InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)


def fetch_email_and_timezone(credentials: Credentials) -> tuple[str | None, str]:
    oauth2_service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    userinfo = oauth2_service.userinfo().get().execute()
    email = userinfo.get("email")

    calendar_service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    settings_resp = calendar_service.settings().get(setting="timezone").execute()
    tz = settings_resp.get("value", "UTC")

    return email, tz
