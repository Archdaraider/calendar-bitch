import { defineRailway, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const calendarBitchVolume = volume("calendar-bitch-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, sizeMB: 5000, region: "sfo" });

  const calendarBitch = service("calendar-bitch", {
    replicas: { "sfo": 1 },
    volumeMounts: { "/data": calendarBitchVolume },
    start: "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
    build: { builder: "NIXPACKS" },
    deploy: { sleepApplication: true },
    env: {
      GIRLFRIEND_EMAIL: preserve(),
      GIRLFRIEND_NAME: preserve(),
      GIRLFRIEND_HOWABOUT_CALENDAR_NAME: preserve(),
      GOOGLE_CLIENT_ID: preserve(),
      GOOGLE_CLIENT_SECRET: preserve(),
      GOOGLE_REFRESH_TOKEN: preserve(),
      OWNER_TELEGRAM_USER_ID: preserve(),
      PUBLIC_BASE_URL: preserve(),
      STATE_FILE_PATH: preserve(),
      TELEGRAM_BOT_TOKEN: preserve(),
      TELEGRAM_WEBHOOK_SECRET: preserve(),
      GEMINI_API_KEYS: preserve(),
      GEMINI_MODEL: preserve(),
      ANNIVERSARY_DATE: preserve(),
      PARTNER_BIRTHDAY_DATE: preserve(),
      GIRLFRIEND_TIMEZONE: preserve(),
      OWNER_GREETING_NAME: preserve(),
      HOWABOUT_API_KEY: preserve(),
      HOWABOUT_BASE_URL: preserve(),
      HOWABOUT_APP_LINK: preserve(),
      INTERNAL_API_SECRET: preserve(),
    },
  });

  // Stateless pinger -- no volume, no Google/Telegram credentials. It just POSTs to
  // calendar-bitch's /internal/tick, which does the actual work in-process there,
  // against the one real state file (see app/agenda_cron.py's module docstring).
  const agendaCron = service("agenda-cron", {
    start: "python -m app.agenda_cron",
    build: { builder: "NIXPACKS" },
    deploy: { cronSchedule: "*/5 * * * *", restartPolicyType: "NEVER" },
    env: {
      PUBLIC_BASE_URL: calendarBitch.env.PUBLIC_BASE_URL,
      INTERNAL_API_SECRET: calendarBitch.env.INTERNAL_API_SECRET,
    },
  });

  return project("calendar-bitch", {
    resources: [calendarBitch, agendaCron, calendarBitchVolume],
  });
});
