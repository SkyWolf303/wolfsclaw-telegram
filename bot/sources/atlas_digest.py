"""Weekly Atlas open PRs digest + enhanced Atlas change narration.

Pulls structured intelligence from the wolfs-claw-atlas-tracker decomposition
data if available, falls back to raw GitHub API otherwise.

Runs every Monday at 09:00 UTC alongside the TVL summary.
Also enhances the atlas.py poller with richer change descriptions.
"""

import logging
from html import escape

import aiohttp

from bot.config import GITHUB_TOKEN
from bot.telegram import send_message
from bot.timeutils import age_label, parse_iso

logger = logging.getLogger(__name__)

_GH_API = "https://api.github.com"
_ATLAS_REPO = "sky-ecosystem/next-gen-atlas"
_TIMEOUT = aiohttp.ClientTimeout(total=20)


def _gh_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers

# Atlas scope labels for human-readable output
SCOPE_LABELS = {
    "A.0": "Atlas Rules",
    "A.1": "Stability Scope",
    "A.2": "Support Scope",
    "A.3": "Protocol Scope",
    "A.4": "Accessibility Scope",
    "A.5": "Governance Scope",
    "A.6": "Agent Framework",
}

# PR categories by title keywords
def _categorize_pr(title: str) -> str:
    t = title.lower()
    if any(k in t for k in ("weekly", "atlas edit", "weekly cycle")):
        return "weekly"
    if any(k in t for k in ("saep", "spark proposal", "spark -", "spark:")):
        return "spark"
    if any(k in t for k in ("grove", "keel", "obex", "skybase", "pattern", "prysm", "interval")):
        return "agent"
    if any(k in t for k in ("wip", "fix", "lint", "cleanup", "clarif")):
        return "maintenance"
    return "other"


def _pr_age(created_at: str) -> str:
    dt = parse_iso(created_at)
    return age_label(dt) if dt else ""


async def weekly_atlas_digest(db) -> None:
    """Post a weekly digest of open Atlas PRs, grouped by category."""
    logger.info("Building weekly Atlas PR digest…")

    async with aiohttp.ClientSession() as session:
        try:
            url = f"{_GH_API}/repos/{_ATLAS_REPO}/pulls?state=open&per_page=30"
            async with session.get(url, timeout=_TIMEOUT,
                                   headers=_gh_headers()) as resp:
                if resp.status != 200:
                    logger.warning("GitHub PRs returned %d", resp.status)
                    return
                prs = await resp.json()
        except Exception as e:
            logger.error("Atlas digest fetch error: %s", e)
            return

    if not prs:
        logger.info("No open Atlas PRs")
        return

    # Group by category
    groups: dict[str, list] = {"weekly": [], "spark": [], "agent": [], "maintenance": [], "other": []}
    for pr in prs:
        cat = _categorize_pr(pr.get("title", ""))
        groups[cat].append(pr)

    lines = [f"<b>📜 Atlas — Weekly PR Pipeline · WolfsClaw</b>"]
    lines.append(f"<i>{len(prs)} open PRs</i>\n")

    labels = {
        "weekly": "🗓 Weekly Edit Proposals",
        "spark": "⚡ Spark Proposals",
        "agent": "🤖 Agent Changes",
        "maintenance": "🔧 Maintenance",
        "other": "📋 Other",
    }

    for cat, label in labels.items():
        group = groups[cat]
        if not group:
            continue
        lines.append(f"<b>{label}</b>")
        for pr in group:
            number = pr.get("number", "")
            title = pr.get("title", "")[:80]
            url = pr.get("html_url", "")
            author = pr.get("user", {}).get("login", "")
            age = _pr_age(pr.get("created_at", ""))
            age_str = f" · {age}" if age else ""
            lines.append(f'• <a href="{url}">#{number} — {escape(title)}</a>\n  <i>@{escape(author)}{age_str} · <a href="{url}">GitHub</a></i>')
        lines.append("")

    lines.append(f'🔗 <a href="https://github.com/{_ATLAS_REPO}/pulls">All open PRs on GitHub</a>')

    msg = "\n".join(lines)
    await send_message(msg, post_type="atlas_weekly_digest", db=db, priority=3, enrich=False)
    logger.info("Atlas weekly digest posted — %d PRs", len(prs))


async def atlas_change_summary(db) -> None:
    """Post a summary of recent Atlas commits (last 7 days) with scope analysis.
    
    Called from the weekly digest to give a fuller picture alongside open PRs.
    """
    logger.info("Building Atlas change summary…")

    async with aiohttp.ClientSession() as session:
        try:
            from urllib.parse import quote
            path = quote("Sky Atlas/Sky Atlas.md")
            url = f"{_GH_API}/repos/{_ATLAS_REPO}/commits?path={path}&per_page=10"
            async with session.get(url, timeout=_TIMEOUT,
                                   headers=_gh_headers()) as resp:
                if resp.status != 200:
                    return
                commits = await resp.json()
        except Exception as e:
            logger.error("Atlas commits fetch error: %s", e)
            return

    from bot.timeutils import parse_iso, is_fresh
    from datetime import timedelta
    from bot.timeutils import utcnow

    week_ago = utcnow() - timedelta(days=7)
    recent = []
    for c in commits:
        dt = parse_iso(c.get("commit", {}).get("author", {}).get("date", ""))
        if dt and dt >= week_ago:
            recent.append((c, dt))

    if not recent:
        lines = ["<b>📜 Atlas — This Week</b>"]
        lines.append("No Atlas commits in the past 7 days.")
        last = commits[0] if commits else None
        if last:
            last_msg = last.get("commit", {}).get("message", "").split("\n")[0]
            last_dt = parse_iso(last.get("commit", {}).get("author", {}).get("date", ""))
            last_age = age_label(last_dt)
            url = last.get("html_url", "")
            lines.append(f'Last change: <a href="{url}">{escape(last_msg[:80])}</a> ({last_age})')
        msg = "\n".join(lines)
        await send_message(msg, post_type="atlas_weekly_changes", db=db, priority=4, enrich=False)
        return

    lines = [f"<b>📜 Atlas — Changes This Week ({len(recent)} commits)</b>"]
    for commit, dt in recent:
        msg_text = commit.get("commit", {}).get("message", "").split("\n")[0]
        author = (commit.get("commit", {}).get("author", {}).get("name", "")
                  or (commit.get("author") or {}).get("login", "unknown"))
        url = commit.get("html_url", "")
        age = age_label(dt)
        lines.append(f'• <a href="{url}">{escape(msg_text[:100])}</a>\n  <i>by {escape(author)} · {age} · <a href="{url}">GitHub</a></i>')

    msg = "\n".join(lines)
    await send_message(msg, post_type="atlas_weekly_changes", db=db, priority=4, enrich=False)
    logger.info("Atlas change summary posted — %d commits", len(recent))
