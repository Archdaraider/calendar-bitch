import functools
from collections.abc import Awaitable, Callable

from telegram import Update
from telegram.ext import ContextTypes

from app.core.config import get_settings

HandlerFunc = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def restricted(func: HandlerFunc) -> HandlerFunc:
    """Blocks the handler for any Telegram user other than OWNER_TELEGRAM_USER_ID."""

    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        owner_id = get_settings().owner_telegram_user_id
        user = update.effective_user
        if user is None or user.id != owner_id:
            if update.effective_message:
                await update.effective_message.reply_text("This bot is private.")
            return
        await func(update, context)

    return wrapper
