import json
import os
import random
from datetime import date

from app.core.config import get_settings
from app.nlp import gemini_client

PROMPT = (
    "Give me one short original motivational quote for self-discipline and getting "
    "things done tomorrow. Under 20 words. No attribution, no quotation marks, just "
    "the quote text."
)

# Used if Gemini is unconfigured or every key fails.
FALLBACK_QUOTES = [
    "Discipline is choosing between what you want now and what you want most.",
    "Tomorrow is built by what you do tonight and follow through tomorrow.",
    "Small consistent actions beat occasional bursts of motivation.",
    "You don't have to be great to start, but you have to start to be great.",
    "The best time to plan tomorrow is tonight.",
    "Do the hard thing first. Everything else gets easier.",
    "Progress, not perfection.",
    "Show up for yourself tomorrow the way you'd show up for someone you love.",
    "One good day, repeated, is a good life.",
    "Rest tonight, then go get it tomorrow.",
]


def _cache_path() -> str:
    state_path = get_settings().state_file_path
    return os.path.join(os.path.dirname(state_path) or ".", "quote_cache.json")


async def get_daily_quote() -> str:
    """One quote per calendar day, cached on disk -- /today shouldn't call Gemini
    on every tap for something that only needs to change once a day."""
    today_str = date.today().isoformat()
    path = _cache_path()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("date") == today_str:
                return cached["quote"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass

    quote = await gemini_client.generate_text(PROMPT)
    quote = quote.strip().strip('"') if quote else random.choice(FALLBACK_QUOTES)

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump({"date": today_str, "quote": quote}, f)
    os.replace(tmp_path, path)

    return quote
