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

# Minimum follower count for keyword search hits
MIN_FOLLOWERS_FOR_SEARCH = 5000

# Keyword searches are restricted to authors who are in our monitored accounts list.
# Random people tweeting about Sky = noise. Timeline-only is the clean approach.
# Set to True to only post keyword search hits from monitored account handles.
SEARCH_MONITORED_ONLY = True

# Hype phrases — reject tweets that are pure social media influence noise
_HYPE_PHRASES = [
    "are you paying attention",
    "real capital is shifting",
    "you need to know about",
    "this is huge",
    "don't sleep on",
    "going to explode",
    "massive gains",
    "100x",
    "moon soon",
    "buy now",
    "last chance",
    "alpha leak",
    "hidden gem",
    "this changes everything",
    "i called it",
    "thread 🧵",
    "follow for more",
    "like and retweet",
    "not financial advice but",
    "nfa but",
    "aping in",
    "dyor but",
]


# Accounts exempt from minimum engagement filter (always post)
_ENGAGEMENT_EXEMPT = {"runekek", "adamfraser", "soterlabs"}

# Governance keywords — tweets containing these bypass engagement filter
_GOVERNANCE_TERMS = {"governance", "proposal", "settlement", "spell", "atlas", "msc"}


def _is_hype_tweet(text: str) -> bool:
    """Returns True if tweet is social media hype with no analytical value."""
    lower = text.lower()
    return any(phrase in lower for phrase in _HYPE_PHRASES)


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


def _engagement_score(metrics: dict) -> int:
    """Calculate engagement score from public_metrics."""
    likes = metrics.get("like_count", 0) or 0
    rts = metrics.get("retweet_count", 0) or 0
    replies = metrics.get("reply_count", 0) or 0
    return likes + rts * 2 + replies


def _engagement_label(metrics: dict) -> str:
    """Format engagement context string."""
    likes = metrics.get("like_count", 0) or 0
    rts = metrics.get("retweet_count", 0) or 0
    if likes + rts == 0:
        return ""
    return f" (🔥 {likes} likes · {rts} RTs)"


def _priority_from_engagement(score: int, default: int = 5) -> int:
    """Escalate priority based on engagement score."""
    if score > 500:
        return 1  # breaking
    if score > 100:
        return 3  # high
    return default


async def _poll_timeline(
    session: aiohttp.ClientSession,
    username: str,
    user_id: str,
    db,
    cycle_conversations: set[str],
    cycle_account_count: dict[str, int],
) -> int:
    url = (
        f"{_API_BASE}/users/{user_id}/tweets"
        f"?max_results=10&exclude=retweets,replies"
        f"&tweet.fields=created_at,text,author_id,public_metrics,conversation_id"
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
        metrics = tweet.get("public_metrics", {})
        score = _engagement_score(metrics)

        # Thread detection: only post root tweets, skip thread replies
        conv_id = tweet.get("conversation_id", tweet_id)
        if conv_id != tweet_id:
            logger.debug("Skipping thread reply %s (root: %s)", tweet_id, conv_id)
            await mark_tweet_seen(db, tweet_id, username, "timeline")
            continue
        if conv_id in cycle_conversations:
            logger.debug("Skipping duplicate thread %s from @%s", conv_id, username)
            await mark_tweet_seen(db, tweet_id, username, "timeline")
            continue
        cycle_conversations.add(conv_id)

        # Per-account rate limit: max 1 per cycle (2 if viral >200 engagement)
        account_count = cycle_account_count.get(username, 0)
        if account_count >= 2:
            logger.debug("Hard rate limit: @%s already posted %d this cycle", username, account_count)
            await mark_tweet_seen(db, tweet_id, username, "timeline")
            continue
        if account_count >= 1 and score <= 200:
            logger.debug("Rate limit: @%s already posted this cycle (score=%d)", username, score)
            await mark_tweet_seen(db, tweet_id, username, "timeline")
            continue

        # Minimum engagement for non-VIP, non-governance tweets
        likes = metrics.get("like_count", 0) or 0
        rts = metrics.get("retweet_count", 0) or 0
        if (likes < 5 and rts < 2
                and username.lower() not in _ENGAGEMENT_EXEMPT
                and not any(term in text.lower() for term in _GOVERNANCE_TERMS)):
            logger.debug("Skipping low-engagement tweet from @%s (%d likes, %d RTs)", username, likes, rts)
            await mark_tweet_seen(db, tweet_id, username, "timeline")
            continue

        priority = _priority_from_engagement(score)
        eng_label = _engagement_label(metrics)

        msg = _format_timeline_tweet(username, text, tweet_id, source_time)
        if eng_label:
            msg += f"\n<i>{escape(eng_label)}</i>"
        await send_message(msg, post_type="twitter_timeline", db=db, source_time=source_time, priority=priority, source_account=username)
        await mark_tweet_seen(db, tweet_id, username, "timeline")
        cycle_account_count[username] = account_count + 1
        posted += 1

    return posted


async def _poll_search(
    session: aiohttp.ClientSession,
    query: str,
    db,
    cycle_conversations: set[str],
) -> int:
    url = (
        f"{_API_BASE}/tweets/search/recent"
        f"?query={quote(query)}"
        f"&max_results=10"
        f"&tweet.fields=created_at,text,author_id,conversation_id"
        f"&expansions=author_id&user.fields=username,public_metrics"
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

    # Build author map with follower counts
    users_map: dict[str, dict] = {}
    for u in data.get("includes", {}).get("users", []):
        users_map[u["id"]] = {
            "username": u.get("username", "unknown"),
            "followers": u.get("public_metrics", {}).get("followers_count", 0),
        }

    posted = 0
    for tweet in data.get("data", []):
        tweet_id = tweet.get("id", "")
        if not tweet_id or await is_tweet_seen(db, tweet_id):
            continue

        source_time = parse_iso(tweet.get("created_at"))
        if not is_fresh(source_time):
            logger.debug("Skipping stale search tweet %s", tweet_id)
            continue

        # Thread detection: skip thread replies and duplicate threads
        conv_id = tweet.get("conversation_id", tweet_id)
        if conv_id != tweet_id:
            logger.debug("Skipping search thread reply %s", tweet_id)
            continue
        if conv_id in cycle_conversations:
            logger.debug("Skipping duplicate thread %s in search", conv_id)
            continue
        cycle_conversations.add(conv_id)

        author_id = tweet.get("author_id", "")
        author = users_map.get(author_id, {"username": "unknown", "followers": 0})
        username = author["username"]
        followers = author["followers"]
        text = tweet.get("text", "")

        # Filter: only post keyword search hits from our monitored accounts
        if SEARCH_MONITORED_ONLY:
            monitored_handles = {h.lower() for h in TWITTER_ACCOUNTS.keys()}
            if username.lower() not in monitored_handles:
                logger.debug("Skipping keyword search tweet from non-monitored @%s", username)
                continue

        # Filter: minimum followers for keyword search hits
        if followers < MIN_FOLLOWERS_FOR_SEARCH:
            logger.debug("Skipping low-follower search tweet (@%s, %d followers)", username, followers)
            continue

        # Filter: hype/influence noise
        if _is_hype_tweet(text):
            logger.debug("Skipping hype tweet from @%s", username)
            continue

        msg = _format_search_tweet(username, text, tweet_id, source_time)
        await send_message(msg, post_type="twitter_search", db=db, source_time=source_time, source_account=username)
        await mark_tweet_seen(db, tweet_id, username, "search", query)
        posted += 1

    return posted


async def poll_twitter(db) -> None:
    if not X_BEARER_TOKEN:
        logger.warning("X_BEARER_TOKEN not set — skipping Twitter source")
        return

    logger.info("Polling Twitter…")
    total_posted = 0

    # Per-cycle tracking: shared across all timeline + search polls
    cycle_conversations: set[str] = set()
    cycle_account_count: dict[str, int] = {}

    async with aiohttp.ClientSession() as session:
        accounts = dict(TWITTER_ACCOUNTS)
        for username in TWITTER_LOOKUP_ACCOUNTS:
            uid = await _lookup_user_id(session, username, db)
            if uid:
                accounts[username] = uid
            else:
                logger.warning("Could not resolve @%s — skipping", username)

        for username, user_id in accounts.items():
            total_posted += await _poll_timeline(
                session, username, user_id, db, cycle_conversations, cycle_account_count,
            )
            await asyncio.sleep(_TIMELINE_SLEEP)

        for query in TWITTER_SEARCH_QUERIES:
            total_posted += await _poll_search(session, query, db, cycle_conversations)
            await asyncio.sleep(_TIMELINE_SLEEP)

    logger.info("Twitter poll done — %d new tweets posted", total_posted)
