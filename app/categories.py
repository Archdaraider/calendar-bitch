import re

from app.core.config import get_settings

CATEGORIES = {
    "WORK": "work / job tasks",
    "!!!": "urgent or high-importance, not tied to a specific type below",
    "MEET": "social meetups with friends",
    "DL": "deadlines -- assignments, submissions, due dates",
    "SCH": "classes, school, lectures",
    "LEI": "leisure, hobbies, rest",
    "GF": "girlfriend / romantic relationship activities",
    "OTH": "anything that doesn't clearly fit the above",
}

CATEGORY_CODES = list(CATEGORIES.keys())

_CODES_PATTERN = "|".join(re.escape(code) for code in CATEGORY_CODES)
TAG_PREFIX_RE = re.compile(rf"^\[({_CODES_PATTERN})\]\s+")


def strip_tag(title: str) -> str:
    return TAG_PREFIX_RE.sub("", title)


def is_tagged(title: str) -> bool:
    return TAG_PREFIX_RE.match(title) is not None


def get_category(title: str) -> str | None:
    """Returns the category code if `title` starts with a recognized [CODE] tag,
    otherwise None. Used to filter events by category (e.g. /upcoming_workstuff)."""
    m = TAG_PREFIX_RE.match(title)
    return m.group(1) if m else None


def category_hint() -> str:
    """Shared prompt fragment describing the category codes to Gemini, used by both
    new-event parsing and the /categorize bulk backfill."""
    hint = "Categories: " + "; ".join(f"{code}={desc}" for code, desc in CATEGORIES.items()) + "."
    names = get_settings().girlfriend_name_list
    if names:
        names_str = " or ".join(f'"{n}"' for n in names)
        hint += f" Events mentioning {names_str} or clearly a romantic/relationship activity should use GF."
    return hint
