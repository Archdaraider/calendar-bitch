"""Gemini caller with fallback across up to 3 API keys (GEMINI_API_KEYS)."""

import json
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

GEMINI_URL_TMPL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
REQUEST_TIMEOUT_SECONDS = 10


async def _call_gemini(api_key: str, model: str, prompt: str, schema: dict | None) -> str:
    url = GEMINI_URL_TMPL.format(model=model)
    generation_config: dict = {"temperature": 0}
    if schema is not None:
        generation_config["responseMimeType"] = "application/json"
        generation_config["responseSchema"] = schema
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": generation_config,
    }
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, params={"key": api_key}, json=payload)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


async def _ask_gemini(prompt: str, schema: dict | None) -> str | None:
    settings = get_settings()
    keys = settings.gemini_api_key_list
    for i, key in enumerate(keys):
        try:
            return await _call_gemini(key, settings.gemini_model, prompt, schema)
        except Exception:
            logger.warning("Gemini key #%d failed, trying next", i + 1, exc_info=True)
    if keys:
        logger.warning("All %d Gemini key(s) failed", len(keys))
    return None


async def generate_json(prompt: str, schema: dict) -> dict | None:
    raw_text = await _ask_gemini(prompt, schema)
    if raw_text is None:
        return None
    try:
        return json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Gemini returned non-JSON despite schema: %r", raw_text)
        return None


async def generate_text(prompt: str) -> str | None:
    raw_text = await _ask_gemini(prompt, schema=None)
    if raw_text is None:
        return None
    return raw_text.strip()
