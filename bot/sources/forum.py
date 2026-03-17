"""Sky Forum (Discourse) poller."""

import logging
from html import escape

import aiohttp

from bot.config import (
    FORUM_CATEGORY_ENDPOINTS,
    FORUM_SEARCH_ENDPOINTS,
    FORUM_TAG_ENDPOINTS,
    SKY_FORUM_API_KEY,
    SKY_FORUM_BASE_URL,
    VIP_AUTHORS,
)
from bot.db import is_topic_seen, mark_topic_seen
from bot.telegram import send_message

logger = logging.getLogger(__name__)

CATEGORY_NAME_MAP: dict[int, str] = {
    92: "Sky Core",
    84: "Spark Prime",
    99: "Incubating Primes",
}


def _forum_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/json"}
    if SKY_FORUM_API_KEY:
        headers["Api-Key"] = SKY_FORUM_API_KEY
    return headers


def _format_topic(topic: dict, category_label: str) -> str:
    title = escape(topic.get("title", "Untitled"))
    slug = topic.get("slug", "")
    topic_id = topic.get("id", 0)
    author = topic.get("last_poster_username") or topic.get("posters", [{}])[0].get("extras", "unknown")
    url = f"{SKY_FORUM_BASE_URL}/t/{slug}/{topic_id}"

    vip_tag = " 🚨 VIP" if author.lower() in {v.lower() for v in VIP_AUTHORS} else ""
    return (
        f"<b>🔔 Sky Forum — New Topic{vip_tag}</b>\n"
        f'<a href="{url}">{title}</a>\n'
        f"by @{escape(str(author))} · {escape(category_label)}"
    )


async def _fetch_topics_from_endpoint(
    session: aiohttp.ClientSession,
    endpoint: str,
) -> list[dict]:
    """Fetch topic list from a Discourse endpoint."""
    url = f"{SKY_FORUM_BASE_URL}{endpoint}"
    try:
        async with session.get(url, headers=_forum_headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning("Forum %s returned %d", endpoint, resp.status)
                return []
            data = await resp.json()
    except Exception as e:
        logger.error("Forum fetch error for %s: %s", endpoint, e)
        return []

    # Discourse puts topics in different places depending on endpoint
    if "topic_list" in data:
        return data["topic_list"].get("topics", [])
    if "topics" in data:
        return data["topics"]
    return []


async def _fetch_search_topics(
    session: aiohttp.ClientSession,
    endpoint: str,
) -> list[dict]:
    """Fetch topics from a search endpoint."""
    url = f"{SKY_FORUM_BASE_URL}{endpoint}"
    try:
        async with session.get(url, headers=_forum_headers(), timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning("Forum search %s returned %d", endpoint, resp.status)
                return []
            data = await resp.json()
    except Exception as e:
        logger.error("Forum search error for %s: %s", endpoint, e)
        return []
    return data.get("topics", [])


async def poll_forum(db) -> None:
    """Poll all Sky Forum endpoints and post new topics."""
    logger.info("Polling Sky Forum…")
    posted = 0

    async with aiohttp.ClientSession() as session:
        # 1. Latest topics overall
        topics = await _fetch_topics_from_endpoint(session, "/latest.json")
        for t in topics:
            tid = t.get("id")
            if not tid or await is_topic_seen(db, tid):
                continue
            cat_id = t.get("category_id", 0)
            cat_name = CATEGORY_NAME_MAP.get(cat_id, "General")
            author = t.get("last_poster_username", "unknown")
            msg = _format_topic(t, cat_name)
            await send_message(msg, post_type="forum_alert", db=db, priority=3)
            await mark_topic_seen(db, tid, t.get("title", ""), author, cat_id)
            posted += 1

        # 2. Category-specific endpoints
        for label, endpoint in FORUM_CATEGORY_ENDPOINTS.items():
            topics = await _fetch_topics_from_endpoint(session, endpoint)
            for t in topics:
                tid = t.get("id")
                if not tid or await is_topic_seen(db, tid):
                    continue
                author = t.get("last_poster_username", "unknown")
                msg = _format_topic(t, label)
                await send_message(msg, post_type="forum_alert", db=db, priority=3)
                await mark_topic_seen(db, tid, t.get("title", ""), author, t.get("category_id", 0))
                posted += 1

        # 3. Tag endpoints
        for label, endpoint in FORUM_TAG_ENDPOINTS.items():
            topics = await _fetch_topics_from_endpoint(session, endpoint)
            for t in topics:
                tid = t.get("id")
                if not tid or await is_topic_seen(db, tid):
                    continue
                author = t.get("last_poster_username", "unknown")
                msg = _format_topic(t, label)
                await send_message(msg, post_type="forum_alert", db=db, priority=3)
                await mark_topic_seen(db, tid, t.get("title", ""), author, t.get("category_id", 0))
                posted += 1

        # 4. Search endpoints
        for label, endpoint in FORUM_SEARCH_ENDPOINTS.items():
            topics = await _fetch_search_topics(session, endpoint)
            for t in topics:
                tid = t.get("id")
                if not tid or await is_topic_seen(db, tid):
                    continue
                author = t.get("last_poster_username", "unknown")
                msg = _format_topic(t, label)
                await send_message(msg, post_type="forum_alert", db=db, priority=3)
                await mark_topic_seen(db, tid, t.get("title", ""), author, t.get("category_id", 0))
                posted += 1

    logger.info("Forum poll done — %d new topics posted", posted)
