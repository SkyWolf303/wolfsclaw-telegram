"""Spell schedule helper for weekly digest — shared schedule data."""

from datetime import datetime, timezone
from bot.timeutils import utcnow

# 2026 Spell Schedule: (date_str, crafter)
SPELL_SCHEDULE: list[tuple[str, str]] = [
    ("2026-03-26", "Sidestream"),
    ("2026-04-09", "DeWiz"),
    ("2026-04-23", "Sidestream"),
    ("2026-05-07", "DeWiz"),
    ("2026-06-04", "Sidestream"),
    ("2026-06-18", "DeWiz"),
    ("2026-07-02", "Sidestream"),
    ("2026-07-16", "DeWiz"),
    ("2026-08-13", "Sidestream"),
    ("2026-08-27", "DeWiz"),
    ("2026-09-10", "Sidestream"),
    ("2026-09-24", "DeWiz"),
    ("2026-10-08", "Sidestream"),
    ("2026-10-22", "DeWiz"),
    ("2026-11-05", "Sidestream"),
    ("2026-11-19", "DeWiz"),
    ("2026-12-03", "Sidestream"),
    ("2026-12-17", "DeWiz"),
]


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def get_next_spell() -> tuple[datetime, str, int] | None:
    """Return (date, crafter, days_until) for the next upcoming spell, or None."""
    now = utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    for date_str, crafter in SPELL_SCHEDULE:
        spell_date = _parse_date(date_str)
        if spell_date >= today:
            days_until = (spell_date - today).days
            return spell_date, crafter, days_until
    return None
