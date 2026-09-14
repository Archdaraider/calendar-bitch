"""One-time local Google OAuth consent for Calendar_Bitch.

Run this once on your Mac, after filling in TELEGRAM_BOT_TOKEN, GOOGLE_CLIENT_ID,
GOOGLE_CLIENT_SECRET, and OWNER_TELEGRAM_USER_ID in .env:

    python scripts/google_auth_setup.py

It opens your browser, you sign in with the Google account you want the bot to use,
and grant Calendar access. It prints a refresh token -- paste that into .env / Railway
as GOOGLE_REFRESH_TOKEN. It also seeds data/state.json with your live calendar list and
detected timezone.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.gcal.calendar import sync_calendars_into_state  # noqa: E402
from app.gcal.oauth import build_installed_app_flow, fetch_email_and_timezone  # noqa: E402
from app.state import load_state, save_state  # noqa: E402


def main() -> None:
    flow = build_installed_app_flow()
    print("Opening your browser for Google sign-in...")
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not credentials.refresh_token:
        print(
            "Google didn't return a refresh token. Revoke this app's access at "
            "https://myaccount.google.com/permissions and re-run this script."
        )
        return

    email, tz = fetch_email_and_timezone(credentials)

    state = load_state()
    state.timezone = tz
    asyncio.run(sync_calendars_into_state(credentials, state))
    save_state(state)

    print()
    print("=" * 60)
    print(f"Signed in as: {email}")
    print(f"Detected timezone: {tz}")
    print(f"Synced {len(state.calendars)} calendar(s) into data/state.json")
    print()
    print("Paste this into your .env / Railway variables:")
    print()
    print(f"GOOGLE_REFRESH_TOKEN={credentials.refresh_token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
