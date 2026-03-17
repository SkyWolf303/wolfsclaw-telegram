"""Brave Search web poller — surfaces organic Sky ecosystem news."""

import hashlib
import logging
from html import escape

import aiohttp

from bot.config import BRAVE_API_KEY
from bot.db import is_post_duplicate, log_post
from bot.telegram import send_message
from bot.timeutils import parse_iso, parse_unix, is_fresh, age_label

logger = logging.getLogger(__name__)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/news/search"

# Queries to run each poll cycle
SEARCH_QUERIES: list[dict] = [
    {"q": "Sky ecosystem USDS governance", "label": "Sky Ecosystem"},
    {"q": "SparkLend DeFi lending protocol", "label": "SparkLend"},
    {"q": "SKY token MakerDAO stablecoin", "label": "SKY Protocol"},
    {"q": "USDS stablecoin supply growth", "label": "USDS"},
    {"q": "Laniakea Sky protocol agents", "label": "Laniakea"},
    {"q": "Sky governance Rune Christensen", "label": "Sky Governance"},
]

FRESHNESS = "pd"  # past day


async def _search(session: aiohttp.ClientSession, query: str, count: int = 5) -> list[dict]:
    """Run a single Brave News search and return results."""
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY,
    }
    params = {
        "q": query,
        "count": count,
        "freshness": FRESHNESS,
        "text_decorations": False,
        "search_lang": "en",
        "country": "us",
    }
    try:
        async with session.get(BRAVE_SEARCH_URL, headers=headers, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("results", [])
            elif resp.status == 429:
                logger.warning("Brave Search rate limited")
            else:
                logger.warning("Brave Search returned %d for query: %s", resp.status, query)
    except Exception:
        logger.exception("Brave Search request failed for: %s", query)
    return []


def _result_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


async def poll_web(db) -> None:
    """Poll Brave News for each query, post new results to Telegram."""
    if not BRAVE_API_KEY:
        logger.warning("BRAVE_API_KEY not set — skipping web poller")
        return

    async with aiohttp.ClientSession() as session:
        for item in SEARCH_QUERIES:
            query = item["q"]
            label = item["label"]
            results = await _search(session, query)

            for result in results:
                url = result.get("url", "")
                title = result.get("title", "").strip()
                description = result.get("description", "").strip()
                source_name = result.get("meta_url", {}).get("hostname", "") or result.get("source", "")

                if not url or not title:
                    continue

                # Extract verified source timestamp from Brave result
                # Brave returns 'page_age' (ISO string) and 'age' (human string)
                source_time = parse_iso(result.get("page_age"))
                if source_time is None:
                    # Try 'age' field as fallback (sometimes a Unix timestamp)
                    source_time = parse_unix(result.get("age"))

                # Reject stale content — verified from Brave's page_age field
                if not is_fresh(source_time):
                    logger.debug("Skipping stale web result: %s (age=%s)", title[:60], source_time)
                    continue

                time_str = age_label(source_time) if source_time else ""
                desc_line = f"\n{escape(description[:200])}" if description else ""
                source_line = f"\n<i>{escape(source_name)}{' · ' + time_str if time_str else ''}</i>" if (source_name or time_str) else ""

                text = (
                    f"<b>🌐 Web — {escape(label)}</b>\n"
                    f"<a href=\"{escape(url)}\">{escape(title)}</a>"
                    f"{desc_line}"
                    f"{source_line}\n"
                    f'🔗 <a href="{escape(url)}">Read more</a>'
                )

                if await is_post_duplicate(db, text):
                    continue

                await send_message(text, priority=7, source_time=source_time)
                await log_post(db, f"web_{label}", text)
                logger.info("Posted web result [%s]: %s", label, title)
