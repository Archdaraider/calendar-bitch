import random

from app.nlp import gemini_client

PROMPT = (
    "Give me one short original motivational quote for self-discipline and getting "
    "things done tomorrow. Under 20 words. No attribution, no quotation marks, just "
    "the quote text."
)

# Used if Gemini is unconfigured or every key fails -- the nightly message should
# never be missing this line.
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


async def get_daily_quote() -> str:
    quote = await gemini_client.generate_text(PROMPT)
    if quote:
        return quote.strip().strip('"')
    return random.choice(FALLBACK_QUOTES)
