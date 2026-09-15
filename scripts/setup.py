"""Guided local setup. Run before deploying anywhere: python scripts/setup.py"""

import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"

PLACEHOLDER_MARKERS = ("PLACEHOLDER", "")

REQUIRED_VARS = [
    ("TELEGRAM_BOT_TOKEN", "Get from @BotFather on Telegram (New Bot -> copy the token)."),
    ("OWNER_TELEGRAM_USER_ID", "Message @userinfobot on Telegram to get your numeric ID."),
    ("GOOGLE_CLIENT_ID", "Google Cloud Console > APIs & Services > Credentials > \"Desktop app\" OAuth client."),
    ("GOOGLE_CLIENT_SECRET", "From the same OAuth client as GOOGLE_CLIENT_ID."),
]

OPTIONAL_VARS = [
    ("GEMINI_API_KEYS", "Comma-separated free-tier keys from https://aistudio.google.com (up to 3). Skip to use the simpler local date parser instead of AI."),
    ("GIRLFRIEND_NAME", "Her first name + nicknames, comma-separated (e.g. Alex,babe). Used as her display name and to help auto-suggest the GF category."),
    ("GIRLFRIEND_EMAIL", "Her Google account email, if she's shared her calendar with you directly."),
    ("GIRLFRIEND_HOWABOUT_CALENDAR_NAME", "Exact name of a calendar you've subscribed to via Google Calendar > Add calendar > From URL, if using a HowAbout feed instead."),
    ("GIRLFRIEND_TIMEZONE", "Her IANA timezone, e.g. Asia/Singapore -- only affects /gf_schedule_today's dual-timezone display."),
    ("PARTNER_BIRTHDAY_DATE", "YYYY-MM-DD, for /gf's countdown."),
    ("ANNIVERSARY_DATE", "YYYY-MM-DD, for /gf's countdown."),
    ("OWNER_GREETING_NAME", "How the bot addresses you in full-day briefs, e.g. \"Alex\". Leave blank for a generic \"there\"."),
    ("HOWABOUT_APP_LINK", "A link to your HowAbout app/profile, shown as a button once set. Leave blank to skip."),
]


def _read_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        return {}
    values = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _write_env_var(key: str, value: str) -> None:
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    prefix = f"{key}="
    for i, line in enumerate(lines):
        if line.strip().startswith(prefix):
            lines[i] = f"{key}={value}"
            ENV_PATH.write_text("\n".join(lines) + "\n")
            return
    lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(lines) + "\n")


def _is_unset(value: str | None) -> bool:
    if value is None:
        return True
    stripped = value.strip()
    if stripped in PLACEHOLDER_MARKERS:
        return True
    return "PLACEHOLDER" in stripped.upper()


def _prompt(key: str, hint: str, required: bool) -> str | None:
    label = "required" if required else "optional, Enter to skip"
    print(f"\n{key} ({label})")
    print(f"  {hint}")
    answer = input(f"  {key} = ").strip()
    if not answer and required:
        print("  This one's required -- try again, or Ctrl+C to stop and come back later.")
        return _prompt(key, hint, required)
    return answer or None


def _validate_telegram_token(token: str) -> bool:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=10) as resp:
            body = resp.read().decode()
            return '"ok":true' in body
    except (urllib.error.URLError, urllib.error.HTTPError):
        return False


def main() -> None:
    print("=" * 60)
    print("Calendar_Bitch setup")
    print("=" * 60)

    if not ENV_PATH.exists():
        if not ENV_EXAMPLE_PATH.exists():
            print("Can't find .env.example -- run this from the repo root.")
            sys.exit(1)
        ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text())
        print(f"Created {ENV_PATH} from .env.example.")

    existing = _read_env()

    print("\n-- Required --")
    for key, hint in REQUIRED_VARS:
        if not _is_unset(existing.get(key)):
            print(f"\n{key}: already set, skipping.")
            continue
        value = _prompt(key, hint, required=True)
        _write_env_var(key, value)
        existing[key] = value

    token = existing.get("TELEGRAM_BOT_TOKEN", "")
    if token and not _is_unset(token):
        print("\nValidating Telegram bot token...")
        if _validate_telegram_token(token):
            print("  Token looks valid (getMe succeeded).")
        else:
            print("  Couldn't validate that token against Telegram's API -- double-check it's correct.")

    print("\n-- Optional (press Enter to skip any of these) --")
    for key, hint in OPTIONAL_VARS:
        if not _is_unset(existing.get(key)):
            print(f"\n{key}: already set, skipping.")
            continue
        value = _prompt(key, hint, required=False)
        if value:
            _write_env_var(key, value)

    print("\n" + "=" * 60)
    print("Local .env is ready.")
    print("=" * 60)

    run_oauth = input("\nRun Google sign-in now? [Y/n] ").strip().lower()
    if run_oauth in ("", "y", "yes"):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "google_auth_setup.py")])
        print(
            "\nDon't forget to paste the GOOGLE_REFRESH_TOKEN it printed into .env "
            "(already done above if it wrote automatically -- check the file)."
        )
    else:
        print("\nSkipped. Run `python scripts/google_auth_setup.py` yourself when ready.")

    print("\n" + "=" * 60)
    print("Next: deploy to Railway (see README.md's Railway section for the full")
    print("walkthrough). Once deployed, the very first thing to do in Telegram is:")
    print("  /timezone <your IANA timezone>   e.g. /timezone America/Toronto")
    print("Everything else defaults sensibly, but timezone has no safe default --")
    print("skip this and every event gets created in UTC instead of your local time.")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nStopped -- re-run `python scripts/setup.py` anytime to pick up where you left off.")
