"""SQLite database initialisation and query helpers."""

import hashlib
from datetime import datetime, timezone

import aiosqlite

from bot.config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_forum_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id INTEGER UNIQUE,
    title TEXT,
    author TEXT,
    category_id INTEGER,
    posted_at TEXT
);

CREATE TABLE IF NOT EXISTS seen_tweets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tweet_id TEXT UNIQUE,
    author_handle TEXT,
    source_type TEXT,
    search_query TEXT,
    posted_at TEXT
);

CREATE TABLE IF NOT EXISTS seen_atlas_commits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha TEXT UNIQUE,
    message TEXT,
    committed_at TEXT
);

CREATE TABLE IF NOT EXISTS seen_atlas_prs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_number INTEGER UNIQUE,
    title TEXT,
    opened_at TEXT
);

CREATE TABLE IF NOT EXISTS seen_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE,
    title TEXT,
    found_at TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset TEXT,
    price REAL,
    supply REAL,
    market_cap REAL,
    recorded_at TEXT
);

CREATE TABLE IF NOT EXISTS tvl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol TEXT,
    tvl REAL,
    recorded_at TEXT
);

CREATE TABLE IF NOT EXISTS posts_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_type TEXT,
    content_hash TEXT UNIQUE,
    sent_at TEXT
);

CREATE TABLE IF NOT EXISTS cached_user_ids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE,
    twitter_id TEXT,
    cached_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(_SCHEMA)
    await db.commit()
    return db


# ── Forum ────────────────────────────────────────────────────────────────

async def is_topic_seen(db: aiosqlite.Connection, topic_id: int) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM seen_forum_topics WHERE topic_id = ?", (topic_id,)
    )
    return (await cur.fetchone()) is not None


async def mark_topic_seen(
    db: aiosqlite.Connection,
    topic_id: int,
    title: str,
    author: str,
    category_id: int,
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO seen_forum_topics (topic_id, title, author, category_id, posted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (topic_id, title, author, category_id, _now()),
    )
    await db.commit()


# ── Tweets ───────────────────────────────────────────────────────────────

async def is_tweet_seen(db: aiosqlite.Connection, tweet_id: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM seen_tweets WHERE tweet_id = ?", (tweet_id,)
    )
    return (await cur.fetchone()) is not None


async def mark_tweet_seen(
    db: aiosqlite.Connection,
    tweet_id: str,
    author_handle: str,
    source_type: str,
    search_query: str | None = None,
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO seen_tweets (tweet_id, author_handle, source_type, search_query, posted_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (tweet_id, author_handle, source_type, search_query, _now()),
    )
    await db.commit()


# ── Atlas ────────────────────────────────────────────────────────────────

async def is_commit_seen(db: aiosqlite.Connection, sha: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM seen_atlas_commits WHERE sha = ?", (sha,)
    )
    return (await cur.fetchone()) is not None


async def mark_commit_seen(
    db: aiosqlite.Connection, sha: str, message: str
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO seen_atlas_commits (sha, message, committed_at) VALUES (?, ?, ?)",
        (sha, message, _now()),
    )
    await db.commit()


async def is_pr_seen(db: aiosqlite.Connection, pr_number: int) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM seen_atlas_prs WHERE pr_number = ?", (pr_number,)
    )
    return (await cur.fetchone()) is not None


async def mark_pr_seen(
    db: aiosqlite.Connection, pr_number: int, title: str
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO seen_atlas_prs (pr_number, title, opened_at) VALUES (?, ?, ?)",
        (pr_number, title, _now()),
    )
    await db.commit()


# ── Reports ──────────────────────────────────────────────────────────────

async def is_report_seen(db: aiosqlite.Connection, url: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM seen_reports WHERE url = ?", (url,)
    )
    return (await cur.fetchone()) is not None


async def mark_report_seen(
    db: aiosqlite.Connection, url: str, title: str
) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO seen_reports (url, title, found_at) VALUES (?, ?, ?)",
        (url, title, _now()),
    )
    await db.commit()


# ── Market snapshots ─────────────────────────────────────────────────────

async def save_market_snapshot(
    db: aiosqlite.Connection,
    asset: str,
    price: float | None,
    supply: float | None,
    market_cap: float | None,
) -> None:
    await db.execute(
        "INSERT INTO market_snapshots (asset, price, supply, market_cap, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (asset, price, supply, market_cap, _now()),
    )
    await db.commit()


async def get_last_market_snapshot(
    db: aiosqlite.Connection, asset: str
) -> dict | None:
    cur = await db.execute(
        "SELECT price, supply, market_cap, recorded_at FROM market_snapshots "
        "WHERE asset = ? ORDER BY id DESC LIMIT 1",
        (asset,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {
        "price": row["price"],
        "supply": row["supply"],
        "market_cap": row["market_cap"],
        "recorded_at": row["recorded_at"],
    }


# ── TVL snapshots ───────────────────────────────────────────────────────

async def save_tvl_snapshot(
    db: aiosqlite.Connection, protocol: str, tvl: float
) -> None:
    await db.execute(
        "INSERT INTO tvl_snapshots (protocol, tvl, recorded_at) VALUES (?, ?, ?)",
        (protocol, tvl, _now()),
    )
    await db.commit()


async def get_last_tvl_snapshot(
    db: aiosqlite.Connection, protocol: str
) -> dict | None:
    cur = await db.execute(
        "SELECT tvl, recorded_at FROM tvl_snapshots "
        "WHERE protocol = ? ORDER BY id DESC LIMIT 1",
        (protocol,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    return {"tvl": row["tvl"], "recorded_at": row["recorded_at"]}


# ── Posts log ────────────────────────────────────────────────────────────

async def is_post_duplicate(db: aiosqlite.Connection, text: str) -> bool:
    h = content_hash(text)
    cur = await db.execute(
        "SELECT 1 FROM posts_log WHERE content_hash = ?", (h,)
    )
    return (await cur.fetchone()) is not None


async def log_post(
    db: aiosqlite.Connection, post_type: str, text: str
) -> None:
    h = content_hash(text)
    await db.execute(
        "INSERT OR IGNORE INTO posts_log (post_type, content_hash, sent_at) VALUES (?, ?, ?)",
        (post_type, h, _now()),
    )
    await db.commit()


# ── Cached user IDs ─────────────────────────────────────────────────────

async def get_cached_user_id(
    db: aiosqlite.Connection, username: str
) -> str | None:
    cur = await db.execute(
        "SELECT twitter_id FROM cached_user_ids WHERE username = ?",
        (username,),
    )
    row = await cur.fetchone()
    return row["twitter_id"] if row else None


async def cache_user_id(
    db: aiosqlite.Connection, username: str, twitter_id: str
) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO cached_user_ids (username, twitter_id, cached_at) "
        "VALUES (?, ?, ?)",
        (username, twitter_id, _now()),
    )
    await db.commit()
