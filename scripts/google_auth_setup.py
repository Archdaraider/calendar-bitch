"""One-time local Google OAuth consent: python scripts/google_auth_setup.py"""

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
