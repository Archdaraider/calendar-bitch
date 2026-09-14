from google.oauth2.credentials import Credentials

from app.core.config import get_settings
from app.gcal.oauth import SCOPES


class NotConfiguredError(Exception):
    pass


def build_credentials() -> Credentials:
    settings = get_settings()
    if not settings.google_refresh_token:
        raise NotConfiguredError(
            "GOOGLE_REFRESH_TOKEN is not set — run scripts/google_auth_setup.py once "
            "and paste the printed refresh token into your .env / Railway variables."
        )
    return Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        scopes=SCOPES,
    )
