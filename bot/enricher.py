"""Enrichment layer — uses Claude Sonnet (Anthropic) by default, falls back to xAI Grok.

Every post gets context-aware summarization before hitting Telegram.
"""

import logging
import re
import aiohttp

from bot.config import XAI_API_KEY

logger = logging.getLogger(__name__)

import os
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

XAI_BASE_URL = "https://api.x.ai/v1/chat/completions"
XAI_MODEL = "grok-3-latest"

AD_FILTER_PROMPT = """You are a strict content filter for a Sky ecosystem governance intelligence channel.
Your only job: decide if this content is GOVERNANCE INTELLIGENCE or NOISE.

APPROVE (governance intelligence) — ONLY these categories:
- Governance proposals, votes, executive votes, polls, protocol parameter changes
- Settlement reports (MSC settlements), OEA reports
- Risk assessments, security disclosures, audit results
- Significant metric moves: TVL, USDS supply, SKY price, revenue milestones
- Atlas/documentation changes, scope amendments
- New agent launches or major protocol architecture changes
- Regulatory news directly affecting Sky/MakerDAO
- Spell payloads, technical deployments with governance implications

REJECT (noise) — including but not limited to:
- Product/feature launch announcements ("our new X is live", "introducing Y")
- Marketing/promotional content, even from ecosystem participants
- Partnership announcements with no direct governance impact
- Thread continuation tweets (replies, "1/", "2/", follow-ups to earlier tweets)
- Yield farming ads, liquidity mining, referral programs, giveaways
- Generic ecosystem commentary or hype ("exciting times", "big things coming")
- Content primarily about a non-Sky product with only tangential Sky mention
- Event invites, hackathon promotions, job postings
- Press release style announcements ("we're excited to announce")

When in doubt, REJECT. This channel is for governance-critical updates only.

Reply with ONLY one word: APPROVE or REJECT. Nothing else."""


SYSTEM_PROMPT = """You are the editor for WolfsClaw's Den — a Sky ecosystem intelligence channel.
This channel is INTELLIGENCE ONLY. No ads, no promotions, no hype. Governance, protocol mechanics, data.
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
- Line 1: keep the original emoji + category label, then " · WolfsClaw" appended
- Line 2: "by [Author]" — use the real author name/handle if known
- Line 3: ONE sentence. Clear, specific, jargon-explained. WHO did WHAT and WHY it matters. No fluff.
- Line 4: 🔗 <a href="...">Source</a> — ALWAYS include the original URL, NEVER drop it
- Max 4 lines. No extra commentary. No "Note:", "Summary:", "In conclusion:"
- CRITICAL: Every <a href="..."> tag from the input MUST appear in the output. Never remove links.
- Output ONLY the Telegram message. Nothing else."""


async def _call_anthropic(prompt: str, system: str, max_tokens: int = 400) -> str | None:
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                ANTHROPIC_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["content"][0]["text"].strip()
                else:
                    body = await resp.text()
                    logger.warning("Anthropic API returned %d: %s", resp.status, body[:200])
    except Exception:
        logger.exception("Anthropic call failed")
    return None


async def _call_xai(system: str, user: str, max_tokens: int = 400) -> str | None:
    if not XAI_API_KEY:
        return None
    payload = {
        "model": XAI_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {XAI_API_KEY}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                XAI_BASE_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.exception("xAI call failed")
    return None


async def is_ad(raw_text: str) -> bool:
    """Returns True if content looks like advertising."""
    result = None
    if ANTHROPIC_API_KEY:
        result = await _call_anthropic(raw_text[:800], AD_FILTER_PROMPT, max_tokens=5)
    elif XAI_API_KEY:
        result = await _call_xai(AD_FILTER_PROMPT, raw_text[:800], max_tokens=5)
    if result and "REJECT" in result.upper():
        logger.info("Ad filter REJECTED: %s…", raw_text[:80])
        return True
    return False


async def narrate_atlas_diff(diff_excerpt: str, commit_message: str) -> str:
    prompt = f"""You are a Sky ecosystem governance expert. An Atlas edit was just committed:

Commit: {commit_message}

Diff excerpt (first 1500 chars):
{diff_excerpt[:1500]}

Write 2-3 plain English sentences explaining:
1. What specifically changed in the Atlas
2. What this means for GovOps teams, executor agents, or governance participants

Be specific and technical. No fluff. Output ONLY the sentences, nothing else."""

    result = None
    if ANTHROPIC_API_KEY:
        result = await _call_anthropic(prompt, "You are a Sky ecosystem governance expert.", max_tokens=200)
    elif XAI_API_KEY:
        result = await _call_xai("You are a Sky ecosystem governance expert.", prompt, max_tokens=200)
    return result or ""


async def enrich(raw_text: str) -> str:
    """Enrich raw_text with Claude/Grok. Falls back to raw on failure."""
    result = None
    if ANTHROPIC_API_KEY:
        result = await _call_anthropic(f"Rewrite this into the required format:\n\n{raw_text}", SYSTEM_PROMPT)
    elif XAI_API_KEY:
        result = await _call_xai(SYSTEM_PROMPT, f"Rewrite this into the required format:\n\n{raw_text}")

    if not result:
        return raw_text

    # Safety: if links were dropped, fall back to raw
    raw_links = set(re.findall(r'href="([^"]+)"', raw_text))
    improved_links = set(re.findall(r'href="([^"]+)"', result))
    if raw_links and not raw_links.issubset(improved_links):
        dropped = raw_links - improved_links
        logger.warning("AI dropped %d link(s) — using raw text: %s", len(dropped), dropped)
        return raw_text

    logger.debug("Enriched post (%d→%d chars)", len(raw_text), len(result))
    return result
