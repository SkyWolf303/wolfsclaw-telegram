"""X/Twitter poller using API v2 — verified source timestamps only."""

import asyncio
import logging
from html import escape
from urllib.parse import quote

import aiohttp

from bot.config import (
    TWITTER_ACCOUNTS,
    TWITTER_LOOKUP_ACCOUNTS,
    TWITTER_SEARCH_QUERIES,
    X_BEARER_TOKEN,
)
from bot.db import (
    cache_user_id,
    get_cached_user_id,
    is_tweet_seen,
    mark_tweet_seen,
)
from bot.telegram import send_message
from bot.timeutils import parse_iso, is_fresh, age_label

logger = logging.getLogger(__name__)

_API_BASE = "https://api.twitter.com/2"
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_TIMELINE_SLEEP = 0.5


def _tw_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {X_BEARER_TOKEN}"}


def _format_timeline_tweet(username: str, text: str, tweet_id: str, source_time=None) -> str:
    truncated = escape(text[:280])
    time_str = f" · {age_label(source_time)}" if source_time else ""
    return (
        f"<b>🐺 @{escape(username)}</b>\n"
        f"{truncated}\n"
        f"<i>Posted{time_str}</i>\n"
        f'🔗 <a href="https://x.com/{username}/status/{tweet_id}">View on X</a>'
    )


def _format_search_tweet(username: str, text: str, tweet_id: str, source_time=None) -> str:
    truncated = escape(text[:280])
    time_str = f" · {age_label(source_time)}" if source_time else ""
    return (
        f"<b>🐺 Den Buzz</b>\n"
        f"<b>by @{escape(username)}</b>\n"
        f"{truncated}\n"
        f"<i>Posted{time_str}</i>\n"
        f'🔗 <a href="https://x.com/{username}/status/{tweet_id}">View on X</a>'
    )


async def _lookup_user_id(
    session: aiohttp.ClientSession, username: str, db
) -> str | None:
    cached = await get_cached_user_id(db, username)
    if cached:
        return cached
    url = f"{_API_BASE}/users/by/username/{username}"
    try:
        async with session.get(url, headers=_tw_headers(), timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning("Twitter user lookup for %s returned %d", username, resp.status)
                return None
            data = await resp.json()
            user_id = data.get("data", {}).get("id")
            if user_id:
                await cache_user_id(db, username, user_id)
                return user_id
    except Exception as e:
        logger.error("Twitter user lookup error for %s: %s", username, e)
    return None


async def _poll_timeline(
    session: aiohttp.ClientSession,
    username: str,
    user_id: str,
    db,
) -> int:
    url = (
        f"{_API_BASE}/users/{user_id}/tweets"
        f"?max_results=10&exclude=retweets,replies"
        f"&tweet.fields=created_at,text,author_id"
        f"&expansions=author_id&user.fields=username"
    )
    try:
        async with session.get(url, headers=_tw_headers(), timeout=_TIMEOUT) as resp:
            if resp.status == 429:
                logger.warning("Twitter rate limit hit for @%s", username)
                return 0
            if resp.status != 200:
                logger.warning("Twitter timeline for @%s returned %d", username, resp.status)
                return 0
            data = await resp.json()
    except Exception as e:
        logger.error("Twitter timeline error for @%s: %s", username, e)
        return 0

    posted = 0
    for tweet in data.get("data", []):
        tweet_id = tweet.get("id", "")
        if not tweet_id or await is_tweet_seen(db, tweet_id):
            continue

        # Verified source timestamp from Twitter API
        source_time = parse_iso(tweet.get("created_at"))

        # Reject stale tweets — verified from Twitter's created_at field
        if not is_fresh(source_time):
            logger.debug("Skipping stale tweet %s (source_time=%s)", tweet_id, source_time)
            continue

        text = tweet.get("text", "")
        msg = _format_timeline_tweet(username, text, tweet_id, source_time)
        await send_message(msg, post_type="twitter_timeline", db=db, source_time=source_time)
        await mark_tweet_seen(db, tweet_id, username, "timeline")
        posted += 1

    return posted


async def _poll_search(
    session: aiohttp.ClientSession,
    query: str,
    db,
) -> int:
    url = (
        f"{_API_BASE}/tweets/search/recent"
        f"?query={quote(query)}"
        f"&max_results=10"
        f"&tweet.fields=created_at,text,author_id"
        f"&expansions=author_id&user.fields=username"
    )
    try:
        async with session.get(url, headers=_tw_headers(), timeout=_TIMEOUT) as resp:
            if resp.status == 429:
                logger.warning("Twitter rate limit hit for search: %s", query)
                return 0
            if resp.status != 200:
                logger.warning("Twitter search returned %d for query: %s", resp.status, query)
                return 0
            data = await resp.json()
    except Exception as e:
        logger.error("Twitter search error for '%s': %s", query, e)
        return 0

    users_map: dict[str, str] = {}
    for u in data.get("includes", {}).get("users", []):
        users_map[u["id"]] = u.get("username", "unknown")

    posted = 0
    for tweet in data.get("data", []):
        tweet_id = tweet.get("id", "")
        if not tweet_id or await is_tweet_seen(db, tweet_id):
            continue

        source_time = parse_iso(tweet.get("created_at"))

        if not is_fresh(source_time):
            logger.debug("Skipping stale search tweet %s", tweet_id)
            continue

        author_id = tweet.get("author_id", "")
        username = users_map.get(author_id, "unknown")
        text = tweet.get("text", "")
        msg = _format_search_tweet(username, text, tweet_id, source_time)
        await send_message(msg, post_type="twitter_search", db=db, source_time=source_time)
        await mark_tweet_seen(db, tweet_id, username, "search", query)
        posted += 1

    return posted


async def poll_twitter(db) -> None:
    if not X_BEARER_TOKEN:
        logger.warning("X_BEARER_TOKEN not set — skipping Twitter source")
        return

    logger.info("Polling Twitter…")
    total_posted = 0

    async with aiohttp.ClientSession() as session:
        accounts = dict(TWITTER_ACCOUNTS)
        for username in TWITTER_LOOKUP_ACCOUNTS:
            uid = await _lookup_user_id(session, username, db)
            if uid:
                accounts[username] = uid
            else:
                logger.warning("Could not resolve @%s — skipping", username)

        for username, user_id in accounts.items():
            total_posted += await _poll_timeline(session, username, user_id, db)
            await asyncio.sleep(_TIMELINE_SLEEP)

        for query in TWITTER_SEARCH_QUERIES:
            total_posted += await _poll_search(session, query, db)
            await asyncio.sleep(_TIMELINE_SLEEP)

    logger.info("Twitter poll done — %d new tweets posted", total_posted)
