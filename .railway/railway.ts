import { defineRailway, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const calendarBitchVolume = volume("calendar-bitch-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, sizeMB: 5000 });

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
      JOY_BIRTHDAY_DATE: preserve(),
      HOWABOUT_API_KEY: preserve(),
      HOWABOUT_BASE_URL: preserve(),
      HOWABOUT_APP_LINK: preserve(),
    },
  });

  const agendaCron = service("agenda-cron", {
    start: "python -m app.agenda_cron",
    build: { builder: "NIXPACKS" },
    deploy: { cronSchedule: "*/5 * * * *", restartPolicyType: "NEVER" },
    volumeMounts: { "/data": calendarBitchVolume },
    env: {
      TELEGRAM_BOT_TOKEN: calendarBitch.env.TELEGRAM_BOT_TOKEN,
      OWNER_TELEGRAM_USER_ID: calendarBitch.env.OWNER_TELEGRAM_USER_ID,
      GOOGLE_CLIENT_ID: calendarBitch.env.GOOGLE_CLIENT_ID,
      GOOGLE_CLIENT_SECRET: calendarBitch.env.GOOGLE_CLIENT_SECRET,
      GOOGLE_REFRESH_TOKEN: calendarBitch.env.GOOGLE_REFRESH_TOKEN,
      GIRLFRIEND_EMAIL: calendarBitch.env.GIRLFRIEND_EMAIL,
      GIRLFRIEND_HOWABOUT_CALENDAR_NAME: calendarBitch.env.GIRLFRIEND_HOWABOUT_CALENDAR_NAME,
      STATE_FILE_PATH: calendarBitch.env.STATE_FILE_PATH,
      HOWABOUT_APP_LINK: calendarBitch.env.HOWABOUT_APP_LINK,
      GEMINI_API_KEYS: preserve(),
      GEMINI_MODEL: preserve(),
    },
  });

  return project("calendar-bitch", {
    resources: [calendarBitch, agendaCron, calendarBitchVolume],
  });
});
