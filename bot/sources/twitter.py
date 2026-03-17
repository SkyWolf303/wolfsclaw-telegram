"""X/Twitter poller using API v2."""

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

logger = logging.getLogger(__name__)

_API_BASE = "https://api.twitter.com/2"
_TIMEOUT = aiohttp.ClientTimeout(total=30)
_TIMELINE_SLEEP = 0.5  # seconds between timeline calls


def _tw_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {X_BEARER_TOKEN}"}


def _format_timeline_tweet(username: str, text: str, tweet_id: str) -> str:
    truncated = escape(text[:280])
    return (
        f"<b>🐦 @{escape(username)}</b>\n"
        f"{truncated}\n"
        f'<a href="https://x.com/{username}/status/{tweet_id}">View tweet</a>'
    )


def _format_search_tweet(username: str, text: str, tweet_id: str, query: str) -> str:
    truncated = escape(text[:280])
    return (
        f"<b>🔎 Community Buzz</b>\n"
        f"@{escape(username)}: {truncated}\n"
        f'<a href="https://x.com/{username}/status/{tweet_id}">View tweet</a>'
    )


async def _lookup_user_id(
    session: aiohttp.ClientSession, username: str, db
) -> str | None:
    """Look up a Twitter user ID by username, with caching."""
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
    """Fetch recent tweets for a single user. Returns count of new tweets posted."""
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

    tweets = data.get("data", [])
    posted = 0
    for tweet in tweets:
        tweet_id = tweet.get("id", "")
        if not tweet_id or await is_tweet_seen(db, tweet_id):
            continue
        text = tweet.get("text", "")
        msg = _format_timeline_tweet(username, text, tweet_id)
        await send_message(msg, post_type="twitter_timeline", db=db)
        await mark_tweet_seen(db, tweet_id, username, "timeline")
        posted += 1

    return posted


async def _poll_search(
    session: aiohttp.ClientSession,
    query: str,
    db,
) -> int:
    """Run a keyword search and post new tweets. Returns count posted."""
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

    tweets = data.get("data", [])
    # Build author_id -> username map from includes
    users_map: dict[str, str] = {}
    for u in data.get("includes", {}).get("users", []):
        users_map[u["id"]] = u.get("username", "unknown")

    posted = 0
    for tweet in tweets:
        tweet_id = tweet.get("id", "")
        if not tweet_id or await is_tweet_seen(db, tweet_id):
            continue
        author_id = tweet.get("author_id", "")
        username = users_map.get(author_id, "unknown")
        text = tweet.get("text", "")
        msg = _format_search_tweet(username, text, tweet_id, query)
        await send_message(msg, post_type="twitter_search", db=db)
        await mark_tweet_seen(db, tweet_id, username, "search", query)
        posted += 1

    return posted


async def poll_twitter(db) -> None:
    """Poll all Twitter timelines and keyword searches."""
    if not X_BEARER_TOKEN:
        logger.warning("X_BEARER_TOKEN not set — skipping Twitter source")
        return

    logger.info("Polling Twitter…")
    total_posted = 0

    async with aiohttp.ClientSession() as session:
        # Build full accounts map including runtime lookups
        accounts = dict(TWITTER_ACCOUNTS)
        for username in TWITTER_LOOKUP_ACCOUNTS:
            uid = await _lookup_user_id(session, username, db)
            if uid:
                accounts[username] = uid
            else:
                logger.warning("Could not resolve @%s — skipping", username)

        # Poll timelines with rate-limit-friendly sleep
        for username, user_id in accounts.items():
            posted = await _poll_timeline(session, username, user_id, db)
            total_posted += posted
            await asyncio.sleep(_TIMELINE_SLEEP)

        # Poll keyword searches
        for query in TWITTER_SEARCH_QUERIES:
            posted = await _poll_search(session, query, db)
            total_posted += posted
            await asyncio.sleep(_TIMELINE_SLEEP)

    logger.info("Twitter poll done — %d new tweets posted", total_posted)
