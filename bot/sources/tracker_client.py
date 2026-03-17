"""Client for the WolfsClaw Atlas Tracker API (running on localhost:3001).

Uses the tracker as single source of truth for Atlas data where available,
falls back to direct GitHub API when tracker is unavailable.
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

TRACKER_BASE = "http://localhost:3001"
_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def get_open_prs(limit: int = 30) -> Optional[list[dict]]:
    """Fetch open PRs from the tracker API.
    Returns None if tracker is unavailable (caller should fall back to GitHub).
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TRACKER_BASE}/api/prs",
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    prs = data.get("prs", [])
                    logger.debug("Tracker returned %d PRs", len(prs))
                    return prs[:limit]
    except Exception as e:
        logger.debug("Tracker unavailable for PRs: %s", e)
    return None


async def get_recent_edits(limit: int = 20, scope: str = None, agent: str = None) -> Optional[list[dict]]:
    """Fetch decomposed Atlas edits from the tracker API.
    Each edit has: section_id, section_title, scope, change_type,
    affected_agents, impact_score, narration (title, summary, govops_impact).
    Returns None if tracker is unavailable or has no data.
    """
    try:
        params = {"limit": str(limit)}
        if scope:
            params["scope"] = scope
        if agent:
            params["agent"] = agent

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{TRACKER_BASE}/api/edits",
                params=params,
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) > 0:
                        logger.debug("Tracker returned %d edits", len(data))
                        return data
    except Exception as e:
        logger.debug("Tracker unavailable for edits: %s", e)
    return None


async def is_tracker_healthy() -> bool:
    """Check if the tracker is responding."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{TRACKER_BASE}/api/prs", timeout=_TIMEOUT) as resp:
                return resp.status == 200
    except Exception:
        return False
