"""Grok enrichment layer — improves raw content before posting to Telegram.

Uses xAI's Grok API (OpenAI-compatible) to add context, sharpen framing,
and make posts more informative for a Sky ecosystem audience.
"""

import logging

import aiohttp

from bot.config import XAI_API_KEY

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-3-latest"

SYSTEM_PROMPT = """You are an expert on the Sky ecosystem (formerly MakerDAO).
You understand the full agent hierarchy (Core Council → Guardians → Primes → Halos),
key actors (Spark, Grove, Keel, Obex, Skybase, Soter Labs, Atlas Axis, Rune, Phoenix Labs, etc.),
governance mechanics (Atlas edits, spells, MSC settlements, StarGuards),
and protocol economics (USDS supply, SKY token, BEAM, LCTS, Laniakea phases).

Your job: take a raw content snippet and return an improved Telegram message.

Rules:
- Use HTML formatting only (Telegram HTML mode): <b>, <i>, <a href="...">, <code>
- Keep it concise: 4-6 lines max for alerts, 8-10 for summaries
- Add 1-2 lines of context/insight that a governance-focused reader would find valuable
- Don't add speculation — only facts you're confident about
- Keep the original source link
- Keep the emoji prefix from the original (🔔 📊 🏦 📜 🐦 🔎 🌐)
- Do NOT add "In summary:" or "Note:" prefixes — just write the post naturally
- Output ONLY the Telegram message text, nothing else"""


async def enrich(raw_text: str) -> str:
    """Pass raw_text through Grok and return an improved version.
    Falls back to raw_text if API call fails or key not set."""
    if not XAI_API_KEY:
        return raw_text

    payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Improve this Telegram post for the Sky ecosystem channel:\n\n{raw_text}"},
        ],
        "max_tokens": 400,
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {XAI_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                XAI_BASE_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    improved = data["choices"][0]["message"]["content"].strip()
                    logger.debug("Grok enriched post (%d→%d chars)", len(raw_text), len(improved))
                    return improved
                else:
                    body = await resp.text()
                    logger.warning("xAI API returned %d: %s", resp.status, body[:200])
    except Exception:
        logger.exception("Grok enrichment failed — using raw text")

    return raw_text
