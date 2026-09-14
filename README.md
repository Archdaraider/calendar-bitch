# Calendar_Bitch

A personal Telegram bot that syncs with **your own** Google Calendar. Text it anything
with a date/time in it — typos and casual phrasing included — and it creates the event,
letting you pick a category yourself. Handles recurring events, bulk delete/edit by
category or keyword (always with a confirmation step first), and an optional second
calendar feed (e.g. a partner's) for a daily cross-timezone summary.

This is a **single-user bot**: each adopter deploys their own copy, configured for their
own Google account and Telegram ID. It is not a multi-tenant SaaS.

## Features

- **Free-text event creation** — `dinner with sam fri 7pm` just works. With a Gemini API
  key configured, typos get corrected and phrasing is flexible; without one, it falls
  back to a local parser (works fine, just stricter about word order).
- **Recurring events** — "every Monday until December" / "every other Tuesday until
  January" creates a real Google Calendar recurring series, with the exact end date
  computed deterministically (not left to an LLM's date arithmetic).
- **Manual categorization, never guessed** — every new event gets a `[CODE]` tag you
  pick from a button row (`WORK`, `!!!`, `MEET`, `DL`, `SCH`, `LEI`, `GF`, `OTH`).
- **Bulk actions with a confirm step** — "delete all BTM and COMP [SCH] events coming
  up" or "move all BTM [SCH] classes to 6pm" match by category/keyword, show you exactly
  what will change, and wait for you to tap Confirm before touching anything.
- **A second calendar feed** (optional) — e.g. a partner's shared calendar or a public
  ICS feed, kept fully separate from your own views, with a daily summary that accounts
  for the two of you being in different timezones.
- **Two adjustable scheduled messages** — your own next-day agenda and (optionally) the
  second calendar's next-day summary, each with its own on/off toggle and `_set HH:MM`
  command.
- **Runs for ~$0-1/month** on Railway's free tier — see [Cost](#cost).

## Quickstart

```bash
git clone https://github.com/Archdaraider/calendar-bitch.git
cd calendar-bitch
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/setup.py
```

`scripts/setup.py` walks you through everything in `.env` (required vars first, then
skippable optional ones), validates your Telegram bot token, and offers to run the
Google sign-in flow for you. When it's done, follow [Deploying to Railway](#deploying-to-railway)
below.

**The very first thing to do in Telegram once it's live:** `/timezone <your IANA
timezone>` (e.g. `/timezone America/Toronto`). There's no safe default for this — skip
it and every event gets created in UTC instead of your local time.

## Configuration reference

All configuration is environment variables — see `.env.example` for the full annotated
list. The essentials:

| Variable | Required? | What it's for |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | From @BotFather |
| `OWNER_TELEGRAM_USER_ID` | Yes | The only Telegram user ID the bot will respond to |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Yes | Google Cloud OAuth client ("Desktop app" type) |
| `GOOGLE_REFRESH_TOKEN` | Yes | Printed by `scripts/google_auth_setup.py` |
| `PUBLIC_BASE_URL` / `TELEGRAM_WEBHOOK_SECRET` | Yes (deploy only) | Webhook registration |
| `GEMINI_API_KEYS` | No | Up to 3 comma-separated free-tier keys; blank = local parser only |
| `OWNER_GREETING_NAME` | No | How the bot addresses you in daily briefs |
| `GIRLFRIEND_EMAIL` / `GIRLFRIEND_HOWABOUT_CALENDAR_NAME` | No | Second calendar source(s) — see below |
| `GIRLFRIEND_NAME` | No | Her display name + Gemini category hint |
| `GIRLFRIEND_TIMEZONE` | No | For the dual-timezone `/gf_schedule_today` display |
| `PARTNER_BIRTHDAY_DATE` / `ANNIVERSARY_DATE` | No | For `/gf`'s countdown |
| `HOWABOUT_APP_LINK` | No | Shown as a button once set (see "What's stubbed") |

### Second calendar (optional)

Two independent sources — set up either, or both (the Howabout-style one is preferred if
both resolve):

1. **Shared Google Calendar**: she shares her calendar with your Google account at "See
   all event details" (not just free/busy), then set `GIRLFRIEND_EMAIL=<her email>`.
2. **Subscribed ICS/public feed**: Google Calendar web → Settings → Add calendar → From
   URL → paste the feed link. Set `GIRLFRIEND_HOWABOUT_CALENDAR_NAME=` to match whatever
   name it shows up under in your calendar list (matched by name, not ID, since
   subscribed calendars don't have a predictable one).

Either source is automatically excluded from your own `/today`, `/week`, `/next`, and
`/categorize` — it only ever feeds the dedicated commands and scheduled summary.

## Commands reference

**View**
- `/today` — full-day brief: greeting, numbered schedule, a daily quote
- `/tomorrow`, `/week`, `/next` — plainer schedule views
- `/upcoming_workstuff` — upcoming `WORK`/`!!!`/`SCH`/`DL` (next 30 days)
- `/upcoming_socials` — upcoming `MEET`/`LEI` (next 30 days)

**Edit & organize**
- `/edit`, `/cancel` — pick an event to rename or delete (recent or upcoming view, with
  a Back button once you've picked one)
- `/calendars` — flag calendars Shared vs Private (dormant seam for a future HowAbout
  push integration)
- `/categorize` — one-time backfill of `[CODE]` tags onto events that predate this bot

**Second calendar** (needs `GIRLFRIEND_EMAIL` and/or `GIRLFRIEND_HOWABOUT_CALENDAR_NAME`)
- `/gf` — countdown to her birthday, your anniversary, and the next `[GF]`-tagged event
- `/gf_schedule` — her week ahead
- `/gf_schedule_today` — her today + tomorrow only, each event's time shown in both her
  timezone and yours

**Scheduled messages**
- `/night_agenda_on` / `/night_agenda_off` / `/night_agenda_set HH:MM` — your own
  next-day agenda (default ~23:59)
- `/girlfriend_agenda_on` / `/girlfriend_agenda_off` / `/girlfriend_agenda_set HH:MM` —
  her next-day summary (default ~17:00)

**Settings**
- `/timezone <IANA name>` — e.g. `/timezone America/Toronto`. Controls what time zone
  new events are actually created in. Find yours at
  [the IANA tz list](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

Type `/` alone in the chat for Telegram's native command picker (kept in sync
automatically via `set_my_commands` at startup — no manual BotFather step needed).

### Free-text phrasing

```
dinner with sam fri 7pm
gym tomorrow at 6am for 90 min, bring the mat
BTM480 Classes 5.45pm every Monday until December
BTM480 Classes 5.45pm every other Monday until January
BTM480 Class is cancelled for 9th October
delete all BTM and COMP [SCH] events coming up
move all BTM [SCH] classes to 6pm
```

With `GEMINI_API_KEYS` set, phrasing/word order are flexible and typos get corrected.
Without it, keep date + time adjacent (ideally at the end of the message) for reliable
results — and recurrence/cancel/bulk-action phrasing won't work at all, since the local
fallback parser has no semantic understanding, only plain one-off "add". Either way, no
time given defaults to 9:00 AM and the bot says so.

**Category is always picked by you, never guessed** — after parsing, the bot shows the
event details and 8 category buttons; tap one to create it with that `[CODE]` prefix on
your **primary** calendar, or Discard to drop it. The reply carries an "↩️ Undo" button.

**Recurring events** support weekly only (any interval — "every", "every other", "every
3rd"). "Until December" means through the *last* occurrence of that weekday in December.
Undo on a recurring event removes the whole series.

**Cancelling one occurrence**: "X is cancelled for 9th October" finds an event on that
date whose title contains "X" and deletes just that occurrence. If it can't pin down
exactly one match, it shows that day's events as buttons instead of guessing.

**Bulk delete/edit**: matches by category and/or keyword(s), shows every match with a
per-item checklist (pre-selected, tap to exclude any), then waits for you to tap Apply.
Bulk-delete truncates matched recurring series from today onward (past occurrences stay
as history); bulk-edit-time moves a matched series as a whole (past and future together)
since it's a correction, not a "stop happening" action.

## Architecture

Two small services on Railway, both billed by actual usage-seconds — designed to fit
inside Railway's Free plan ($0/month, $1/month included usage credit):

- **Main service** (`app/main.py`) — a FastAPI app receiving Telegram messages via
  **webhook** rather than polling, so Railway's **Serverless** feature can put it to
  sleep between messages and wake it in ~1-2s, billing only for active seconds instead
  of 24/7.
- **Cron service** (`app/agenda_cron.py`) — a separate scheduled job, ticking every 5
  minutes (Railway's minimum), that sends the two adjustable scheduled messages. Exists
  because the main service is asleep most of the day and can't reliably fire its own
  timers. Its window-check (`_next_day_target()`) correctly handles any configured send
  time, including ones that fall right on the midnight boundary.
- Google credentials are built at startup from `GOOGLE_CLIENT_ID`/`SECRET`/
  `GOOGLE_REFRESH_TOKEN`; the client library auto-refreshes the access token in memory
  on every call.
- New events are parsed by **Gemini** (`app/nlp/llm_parser.py`, via the shared
  `app/nlp/gemini_client.py` key-fallback caller) into title/start/duration/description/
  recurrence/intent as JSON. Up to 3 keys are tried in order; if all fail, it falls back
  to a local rule-based parser (`app/nlp/event_parser.py`, using `dateparser`).
- Every event title gets a `[CODE]` prefix from `app/categories.py`'s 8 codes — picked
  by you via buttons for new events, or backfilled by `/categorize` (Gemini-classified,
  since there's no interactive step for a bulk sweep) for pre-existing ones.
- The second calendar (`app/girlfriend.py`) reuses the same Google credentials — no
  separate auth. It's deliberately excluded from your own `/today`/`/week`/`/next`/
  `/categorize`.
- All bot state (calendar Share/Private flags, timezone, scheduled-message settings)
  lives in one JSON file on a Volume **shared by both services**.
- The bot only responds to `OWNER_TELEGRAM_USER_ID`; anyone else gets "This bot is
  private."

**Trade-off of the sleep model, honestly:** the first message after inactivity takes
~1-2s longer while the container wakes. That's the deliberate cost of the $1/month Free
plan instead of the $5/month Hobby plan, where none of this sleep/cron complexity would
be necessary.

## Cost

Designed to stay within Railway's Free plan ($1/month of included usage credit, no card
required beyond that unless you exceed it):

- The main service only bills for the seconds it's actually handling a request —
  serverless sleep means near-zero cost between messages for typical personal usage.
- The cron service runs a lightweight check every 5 minutes and exits immediately if
  nothing's due — also close to zero.
- Gemini's free tier covers typical personal usage (a handful of events/day) comfortably
  within its per-minute/per-day quotas.

Realistic personal use (a few messages a day, two scheduled messages) should track near
$0/month. Check Railway's dashboard **Usage** tab after a day or two to confirm it's not
climbing — a steady climb usually means something's keeping the main service awake
(check webhook registration and Serverless is actually enabled).

## Deploying to Railway

You'll create **two services** in one Railway project, sharing one Volume.

1. **Install the CLI and log in**: `railway login`, then `railway init` from this
   directory.
2. **Add a Volume**: dashboard → project → New → Volume → mount path `/data`.
3. **Set env vars on the main service**: dashboard → service → Variables → Raw Editor,
   paste in everything from your local `.env` plus:
   ```
   STATE_FILE_PATH=/data/state.json
   PUBLIC_BASE_URL=<filled in after step 4>
   TELEGRAM_WEBHOOK_SECRET=<python -c "import secrets; print(secrets.token_urlsafe(32))">
   ```
4. **Deploy and set the public domain**: `railway up`, then dashboard → service →
   Settings → Networking → Generate Domain. Copy it into `PUBLIC_BASE_URL`, then
   `railway up` again so the webhook registers with the real URL.
5. **Enable Serverless** (this is what makes it fit the free tier): dashboard → main
   service → Settings → Deploy → Serverless → toggle on.
6. **Add the cron service**: dashboard → project → New → same repo → Settings → Start
   Command → `python -m app.agenda_cron` → Cron Schedule → `*/5 * * * *`. Attach the
   same Volume at `/data`. Copy over `TELEGRAM_BOT_TOKEN`, `OWNER_TELEGRAM_USER_ID`,
   `GOOGLE_CLIENT_ID/SECRET`, `GOOGLE_REFRESH_TOKEN`, `STATE_FILE_PATH`,
   `GIRLFRIEND_EMAIL`/`GIRLFRIEND_HOWABOUT_CALENDAR_NAME`, `HOWABOUT_APP_LINK`, and
   `GEMINI_API_KEYS`/`GEMINI_MODEL` (used for the nightly quote; falls back to a static
   list if missing). It does *not* need `PUBLIC_BASE_URL`/`TELEGRAM_WEBHOOK_SECRET` —
   those are only for the webhook. Leave Serverless **off** here — cron jobs run to
   completion and exit on their own regardless.
7. **Verify**: Usage tab over the next day or two should track near $0, not climb
   steadily.

There's also a `.railway/railway.ts` Infrastructure-as-Code file describing both
services — see `.railway/README.md` for `railway config plan`/`apply` usage if you'd
rather manage it that way than clicking through the dashboard.

## Testing your setup

- `GET https://<your-domain>/healthz` → `200 {"status":"ok","telegram_ready":true}`.
- `/start` → confirm the persistent "📅 Open Google Calendar" button appears and opens
  Google Calendar; if `timezone` is still unset, confirm the ⚠️ first-run nudge appears.
- Text `coffee with alex tomorrow 10am` → pick a category → confirm it appears on Google
  Calendar at the right time; tap Undo → confirm it's deleted.
- Text something with no time (`dentist thursday`) → confirm it defaults to 9:00 AM.
- Text `BTM480 Classes 5.45pm every Monday until December` → confirm a real recurring
  series, ending on the last Monday of December.
- With that series created, text `BTM480 Class is cancelled for 9th October` → confirm
  only that occurrence disappears.
- Text `delete all BTM [SCH] events coming up` → confirm the checklist shows the right
  matches, deselect one, tap Apply → confirm only the selected ones were touched.
- `/edit` and `/cancel` → confirm the recent/upcoming view toggle and Back button work.
- If you configured a second calendar: `/calendars` → confirm it's excluded from the
  list; `/gf_schedule` → confirm it shows her actual events.
- `/night_agenda_set` a few minutes out, `/night_agenda_on` → wait → confirm the push
  arrives from the **cron service** (check its logs, not the main service's).
- Message the bot from a different Telegram account → confirm "This bot is private."
- Don't message the bot for 20+ minutes, then text it → confirm it still responds (just
  slightly slower on that first message — the sleep/wake cycle working as intended).

## What's stubbed / known limitations

- **HowAbout push integration** (`app/integrations/howabout_client.py`) —
  `push_shared_events()` only logs; `get_shareable_events()` already filters to
  Shared-flagged calendars, so wiring in a real API later is a self-contained change.
- New events always go to your **primary** calendar (no per-message picker, to keep
  texting frictionless).
- Without `GEMINI_API_KEYS` (or if all 3 fail), parsing falls back to `dateparser` —
  keep date+time adjacent for best results, and recurrence/cancel/bulk-action phrasing
  won't be understood at all (those need Gemini).
- `/categorize` classifies from the event **title only** (no description), to avoid an
  extra API call per event.
- Recurrence only supports **weekly** (any interval). Daily/monthly isn't handled —
  extend `app/nlp/recurrence.py` and the schema in `app/nlp/llm_parser.py` if needed.
- Cancel-by-description and bulk actions match by simple case-insensitive substring
  against the title — a too-generic keyword may match more than intended (bulk actions
  always show you the list before doing anything, precisely because of this).
- Single-user only — see the note at the top. Adding real multi-tenant support (per-user
  OAuth, per-user state) would be a significant rearchitecture, not a config change.

## Contributing

Issues and PRs welcome. This started as a personal project so some defaults (category
codes in `app/categories.py`, the second-calendar framing) are opinionated — forking and
adjusting them directly in source is expected and fine.

## License

[MIT](LICENSE)
