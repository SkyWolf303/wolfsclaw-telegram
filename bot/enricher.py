"""Grok enrichment layer — improves raw content before posting to Telegram.

Uses xAI's Grok API to add context and sharpen framing for a Sky ecosystem audience.
Every post follows a strict format: who, why it matters, source link.
"""

import logging

import aiohttp

from bot.config import XAI_API_KEY

logger = logging.getLogger(__name__)

XAI_BASE_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-3-latest"

SYSTEM_PROMPT = """You are the editor for WolfsClaw's Den — a Sky ecosystem intelligence channel run by WolfsClaw.
You have deep knowledge of Sky/MakerDAO governance: the agent hierarchy (Core Council → Guardians → Primes → Halos),
key actors (Spark, Grove, Keel, Obex, Skybase, Soter Labs, Atlas Axis, Rune, Phoenix Labs, BA Labs, Steakhouse, etc.),
governance mechanics (Atlas edits, weekly spell cycles, MSC settlements, StarGuards, SpellCore),
and protocol economics (USDS supply, SKY token, BEAM, LCTS, Laniakea phases).

Your job: rewrite a raw content snippet into a polished Telegram post following this EXACT format:

<b>[EMOJI] [CATEGORY] · WolfsClaw</b>
<b>by [Author/Source]</b>
[One sentence: what happened and why it matters to Sky ecosystem participants]
🔗 <a href="URL">Source</a>

Rules:
- HTML formatting ONLY: <b>, <i>, <a href="...">, <code>. No markdown.
- Line 1: keep the original emoji + category label, then " · WolfsClaw" appended (e.g. "🔔 Sky Forum · WolfsClaw", "📊 Market Update · WolfsClaw", "🐺 @handle · WolfsClaw")
- Line 2: "by [Author]" — use the real author name/handle if known; if it's an org post use the org name
- Line 3: ONE sentence. Clear, specific, jargon-explained. Say WHO did WHAT and WHY it matters. No "it was announced that". No fluff.
- Line 4: 🔗 <a href="...">Source</a> — always include the original URL
- Max 4 lines. No extra commentary. No "Note:", "Summary:", "In conclusion:"
- Output ONLY the Telegram message. Nothing else.

Examples of good Line 3:
✅ "Rune proposes cutting the SKY buyback rate by 87% to preserve protocol reserves amid uncertain macro conditions."
✅ "Atlas Axis merged edits removing JAAA from Grove's Direct Exposures, tightening the list of allowed CLO assets."
✅ "Soter Labs published MSC #6, the February settlement confirming $24M net revenue across Amatsu's three Primes."
✅ "Spark's TVL on DefiLlama dropped 6.2% in 24h, suggesting capital rotation out of SparkLend savings."

Examples of bad Line 3:
❌ "A new report has been published." (too vague)
❌ "This is important for governance." (no specifics)
❌ "Sky ecosystem news." (meaningless)"""


async def enrich(raw_text: str) -> str:
    """Pass raw_text through Grok and return a formatted version.
    Falls back to raw_text if API call fails or key not set."""
    if not XAI_API_KEY:
        return raw_text

    payload = {
        "model": XAI_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Rewrite this into the required format:\n\n{raw_text}"},
        ],
        "max_tokens": 350,
        "temperature": 0.2,
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
