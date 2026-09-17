import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request, Response
from telegram import BotCommand, Update
from telegram.ext import Application

from app.agenda_cron import run_scheduled_checks
from app.bot.handlers import BOT_COMMANDS, register_handlers
from app.core.config import get_settings
from app.gcal.credentials import build_credentials
from app.state import load_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

telegram_app: Application = Application.builder().token(settings.telegram_bot_token).build()
register_handlers(telegram_app)

telegram_ready = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    global telegram_ready

    if not settings.telegram_webhook_secret:
        logger.warning(
            "TELEGRAM_WEBHOOK_SECRET is not set -- the webhook endpoint accepts "
            "unauthenticated requests. Set it before exposing this publicly."
        )

    # A bad token or missing Google config must not take /healthz down with it.
    try:
        await telegram_app.initialize()
        telegram_app.bot_data["state"] = load_state()
        telegram_app.bot_data["credentials"] = build_credentials()
        telegram_ready = True
    except Exception:
        logger.warning(
            "Startup failed -- check TELEGRAM_BOT_TOKEN and Google env vars. The web "
            "server will still run, but the webhook will return 503.",
            exc_info=True,
        )

    if telegram_ready:
        try:
            await telegram_app.bot.set_my_commands([BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS])
        except Exception:
            logger.warning("Could not set the bot's command list (typing '/' won't show it)", exc_info=True)
        try:
            await telegram_app.bot.set_webhook(
                url=settings.telegram_webhook_url,
                secret_token=settings.telegram_webhook_secret or None,
            )
            logger.info(
                "Calendar_Bitch ready — webhook mode for owner_telegram_user_id=%s",
                settings.owner_telegram_user_id,
            )
        except Exception:
            logger.warning(
                "Could not register the Telegram webhook -- check PUBLIC_BASE_URL is a "
                "real reachable HTTPS URL. Handlers are still ready once it's fixed and "
                "the service restarts.",
                exc_info=True,
            )

    yield

    if telegram_ready:
        await telegram_app.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "telegram_ready": telegram_ready}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    if settings.telegram_webhook_secret and x_telegram_bot_api_secret_token != settings.telegram_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid secret token")
    if not telegram_ready:
        raise HTTPException(status_code=503, detail="Bot not ready -- check startup logs")
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    # Awaited inline, not queued -- Serverless can sleep right after this response,
    # so nothing can be left running in the background.
    await telegram_app.process_update(update)
    return Response(status_code=200)


@app.post("/internal/tick")
async def internal_tick(x_internal_secret: str | None = Header(default=None)) -> Response:
    if not settings.internal_api_secret or x_internal_secret != settings.internal_api_secret:
        raise HTTPException(status_code=401, detail="Invalid internal secret")
    await run_scheduled_checks()
    return Response(status_code=200)
