"""Timestamp utilities — verified source times only.

Every piece of content MUST carry a verified source timestamp.
We never assume recency — we verify it from the source and reject stale content.
"""

from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

logger = logging.getLogger(__name__)

# Maximum age of content we'll accept
MAX_AGE_HOURS = 6  # Only content from last 6 hours — keeps alerts timely


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(ts: str | None) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string to a timezone-aware datetime.
    Returns None if unparseable.
    """
    if not ts:
        return None
    # Handle various formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S+00:00",
        "%Y-%m-%dT%H:%M:%S.%f+00:00",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    logger.debug("Could not parse timestamp: %r", ts)
    return None


def parse_unix(ts: int | float | None) -> Optional[datetime]:
    """Convert a Unix timestamp to a timezone-aware datetime."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def is_fresh(dt: Optional[datetime], max_hours: int = MAX_AGE_HOURS) -> bool:
    """Return True if dt is within max_hours of now."""
    if dt is None:
        return False
    age = utcnow() - dt
    return age <= timedelta(hours=max_hours)


def fmt_source_time(dt: Optional[datetime]) -> str:
    """Format a verified source timestamp for display in posts.
    Returns a compact UTC string like '2026-03-17 14:23 UTC'.
    """
    if dt is None:
        return "time unknown"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def age_label(dt: Optional[datetime]) -> str:
    """Human-readable age like '2h ago', '45m ago'."""
    if dt is None:
        return ""
    delta = utcnow() - dt
    total_minutes = int(delta.total_seconds() / 60)
    if total_minutes < 1:
        return "just now"
    if total_minutes < 60:
        return f"{total_minutes}m ago"
    hours = total_minutes // 60
    mins = total_minutes % 60
    if mins == 0:
        return f"{hours}h ago"
    return f"{hours}h {mins}m ago"


def source_epoch(dt: Optional[datetime]) -> float:
    """Convert source datetime to epoch float for queue sorting.
    Newer = smaller value (queue prioritizes lower numbers).
    We negate so newest content sorts first within same priority tier.
    """
    if dt is None:
        return 0.0  # unknown time goes last
    return -dt.timestamp()
