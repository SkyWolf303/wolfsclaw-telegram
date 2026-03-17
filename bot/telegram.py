"""Telegram message helpers.

Two-layer system:
- send_message(): enriches with Grok, deduplicates, then pushes to rate-limited queue
- _send_raw(): called by the dispatcher — actual Telegram API send with retry
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError

from bot.config import DRY_RUN, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, XAI_API_KEY
from bot.db import is_post_duplicate, log_post

logger = logging.getLogger(__name__)

_bot: Bot | None = None


def get_bot() -> Bot:
    global _bot
    if _bot is None:
        _bot = Bot(token=TELEGRAM_BOT_TOKEN)
    return _bot


async def _send_raw(
    text: str,
    post_type: str = "general",
    db=None,
    disable_preview: bool = True,
    max_retries: int = 3,
) -> bool:
    """Directly send a message to Telegram. Called only by the queue dispatcher.

    Returns True if sent (or dry-run logged), False on failure.
    """
    if DRY_RUN:
        logger.info("[DRY RUN] Would send (%s):\n%s", post_type, text)
        if db:
            await log_post(db, post_type, text)
        return True

    bot = get_bot()
    backoff = 1
    for attempt in range(1, max_retries + 1):
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=disable_preview,
            )
            logger.info("Sent %s message (%d chars)", post_type, len(text))
            if db:
                await log_post(db, post_type, text)
            return True
        except RetryAfter as e:
            wait = e.retry_after + 1
            logger.warning("Rate limited by Telegram, waiting %ds (attempt %d)", wait, attempt)
            await asyncio.sleep(wait)
        except TelegramError as e:
            logger.error(
                "Telegram send failed (attempt %d/%d): %s",
                attempt,
                max_retries,
                e,
            )
            if attempt < max_retries:
                await asyncio.sleep(backoff)
                backoff *= 2

    logger.error("Failed to send message after %d retries", max_retries)
    return False


async def send_message(
    text: str,
    post_type: str = "general",
    db=None,
    priority: int = 5,
    enrich: bool = True,
    source_time: Optional[datetime] = None,
) -> None:
    """Enrich, deduplicate, and enqueue a message for rate-limited delivery.

    Priority: 1=urgent (breaking governance), 3=high (forum VIP/Atlas),
              5=normal (market/TVL/twitter), 7=low (web/insights)
    """
    # Fast keyword pre-filter — drop obvious ads without an API call
    _text_lower = text.lower()
    _ad_signals = [
        "earn up to", "apy now", "limited time", "click here to", "sign up now",
        "don't miss out", "exclusive offer", "join our waitlist", "referral",
        "hackathon", "we're hiring", "job opening", "giveaway", "win ",
        "sponsored", "ad:", "promoted", "try it now", "get started today",
        "liquidity mining", "boost your yield",
    ]
    if any(sig in _text_lower for sig in _ad_signals):
        logger.info("Keyword pre-filter dropped ad: %s…", text[:60])
        return

    # Grok ad filter — catches subtler promotional content
    if XAI_API_KEY:
        from bot.enricher import is_ad
        if await is_ad(text):
            return  # silently dropped

    # Enrich with Grok before dedup check
    if enrich and XAI_API_KEY:
        from bot.enricher import enrich as grok_enrich
        text = await grok_enrich(text)

    # Deduplicate against already-sent posts
    if db and await is_post_duplicate(db, text):
        logger.debug("Skipping duplicate post: %s…", text[:60])
        return

    from bot.queue import get_queue
    await get_queue().push(text, post_type=post_type, db=db, priority=priority, source_time=source_time)
