"""Telegram message helpers with retry and deduplication."""

import asyncio
import logging

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


async def send_message(
    text: str,
    post_type: str = "general",
    db=None,
    disable_preview: bool = True,
    max_retries: int = 3,
    enrich: bool = True,
) -> bool:
    """Send a message to the configured Telegram channel.

    Returns True if sent (or dry-run logged), False on failure.
    Runs Grok enrichment by default if XAI_API_KEY is set.
    """
    # Enrich with Grok before dedup check (enriched content is what we send)
    if enrich and XAI_API_KEY:
        from bot.enricher import enrich as grok_enrich
        text = await grok_enrich(text)

    if db and await is_post_duplicate(db, text):
        logger.debug("Skipping duplicate post: %s…", text[:60])
        return False

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
            logger.warning("Rate limited, waiting %ds (attempt %d)", wait, attempt)
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
