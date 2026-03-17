"""Sky Forum (Discourse) poller — verified source timestamps only."""

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
from bot.timeutils import parse_iso, parse_unix, is_fresh, age_label

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


def _extract_source_time(topic: dict):
    """Extract verified source timestamp from a Discourse topic object."""
    # created_at is the authoritative creation time from Discourse
    for field in ("created_at", "bumped_at", "last_posted_at"):
        dt = parse_iso(topic.get(field))
        if dt:
            return dt
    # Fallback: Unix timestamp in 'created_at_unix'
    dt = parse_unix(topic.get("created_at_unix"))
    return dt


def _format_topic(topic: dict, category_label: str, source_time=None) -> str:
    title = escape(topic.get("title", "Untitled"))
    slug = topic.get("slug", "")
    topic_id = topic.get("id", 0)
    author = topic.get("last_poster_username") or "unknown"
    url = f"{SKY_FORUM_BASE_URL}/t/{slug}/{topic_id}"
    vip_tag = " 🚨" if author.lower() in {v.lower() for v in VIP_AUTHORS} else ""
    time_str = age_label(source_time) if source_time else ""
    time_line = f"\n<i>Posted {time_str} · {escape(category_label)}</i>" if time_str else f"\n<i>{escape(category_label)}</i>"

    return (
        f"<b>🔔 Sky Forum{vip_tag}</b>\n"
        f"<b>by @{escape(str(author))}</b>\n"
        f'<a href="{url}">{title}</a>'
        f"{time_line}\n"
        f'🔗 <a href="{url}">Read on forum</a>'
    )


async def _fetch_topics_from_endpoint(
    session: aiohttp.ClientSession,
    endpoint: str,
) -> list[dict]:
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

    if "topic_list" in data:
        return data["topic_list"].get("topics", [])
    if "topics" in data:
        return data["topics"]
    return []


async def _fetch_search_topics(
    session: aiohttp.ClientSession,
    endpoint: str,
) -> list[dict]:
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


async def _process_topic(topic: dict, label: str, db) -> bool:
    """Validate, check freshness, and queue a forum topic. Returns True if posted."""
    tid = topic.get("id")
    if not tid or await is_topic_seen(db, tid):
        return False

    source_time = _extract_source_time(topic)

    # Reject stale content — verified from Discourse timestamps
    if not is_fresh(source_time):
        logger.debug("Skipping stale forum topic %d (source_time=%s)", tid, source_time)
        return False

    author = topic.get("last_poster_username", "unknown")
    cat_id = topic.get("category_id", 0)
    priority = 2 if author.lower() in {v.lower() for v in VIP_AUTHORS} else 3

    msg = _format_topic(topic, label, source_time)
    await send_message(msg, post_type="forum_alert", db=db, priority=priority, source_time=source_time)
    await mark_topic_seen(db, tid, topic.get("title", ""), author, cat_id)
    return True


async def poll_forum(db) -> None:
    """Poll all Sky Forum endpoints and post new topics."""
    logger.info("Polling Sky Forum…")
    posted = 0

    async with aiohttp.ClientSession() as session:
        for topics in [
            await _fetch_topics_from_endpoint(session, "/latest.json"),
        ]:
            for t in topics:
                cat_id = t.get("category_id", 0)
                label = CATEGORY_NAME_MAP.get(cat_id, "General")
                if await _process_topic(t, label, db):
                    posted += 1

        for label, endpoint in FORUM_CATEGORY_ENDPOINTS.items():
            for t in await _fetch_topics_from_endpoint(session, endpoint):
                if await _process_topic(t, label, db):
                    posted += 1

        for label, endpoint in FORUM_TAG_ENDPOINTS.items():
            for t in await _fetch_topics_from_endpoint(session, endpoint):
                if await _process_topic(t, label, db):
                    posted += 1

        for label, endpoint in FORUM_SEARCH_ENDPOINTS.items():
            for t in await _fetch_search_topics(session, endpoint):
                if await _process_topic(t, label, db):
                    posted += 1

    logger.info("Forum poll done — %d new topics posted", posted)
