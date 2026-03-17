"""Sky Atlas GitHub commits + PR poller."""

import logging
from html import escape
from urllib.parse import quote

import aiohttp

from bot.config import ATLAS_FILE_PATH, ATLAS_REPO_NAME, ATLAS_REPO_OWNER, GITHUB_TOKEN
from bot.db import is_commit_seen, is_pr_seen, mark_commit_seen, mark_pr_seen
from bot.telegram import send_message

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_API_BASE = "https://api.github.com"


def _gh_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


async def poll_atlas(db) -> None:
    """Poll GitHub for new Atlas commits and open PRs."""
    logger.info("Polling Sky Atlas (GitHub)…")
    posted = 0

    async with aiohttp.ClientSession() as session:
        # ── Commits ──────────────────────────────────────────────
        encoded_path = quote(ATLAS_FILE_PATH)
        commits_url = (
            f"{_API_BASE}/repos/{ATLAS_REPO_OWNER}/{ATLAS_REPO_NAME}"
            f"/commits?path={encoded_path}&per_page=5"
        )
        try:
            async with session.get(commits_url, headers=_gh_headers(), timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    logger.warning("GitHub commits API returned %d", resp.status)
                else:
                    commits = await resp.json()
                    for c in commits:
                        sha = c.get("sha", "")
                        if not sha or await is_commit_seen(db, sha):
                            continue
                        message = (c.get("commit", {}).get("message", "") or "").split("\n")[0]
                        html_url = c.get("html_url", "")
                        msg = (
                            f"<b>📜 Atlas Change</b>\n"
                            f'New commit: "{escape(message[:200])}"\n'
                            f'<a href="{html_url}">View on GitHub</a>'
                        )
                        await send_message(msg, post_type="atlas_commit", db=db)
                        await mark_commit_seen(db, sha, message)
                        posted += 1
        except Exception as e:
            logger.error("GitHub commits fetch error: %s", e)

        # ── Open PRs ────────────────────────────────────────────
        prs_url = (
            f"{_API_BASE}/repos/{ATLAS_REPO_OWNER}/{ATLAS_REPO_NAME}"
            f"/pulls?state=open"
        )
        try:
            async with session.get(prs_url, headers=_gh_headers(), timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    logger.warning("GitHub PRs API returned %d", resp.status)
                else:
                    prs = await resp.json()
                    for pr in prs:
                        pr_number = pr.get("number")
                        if not pr_number or await is_pr_seen(db, pr_number):
                            continue
                        title = pr.get("title", "Untitled PR")
                        html_url = pr.get("html_url", "")
                        user = pr.get("user", {}).get("login", "unknown")
                        msg = (
                            f"<b>📜 Atlas — New PR</b>\n"
                            f'<a href="{html_url}">#{pr_number}: {escape(title[:200])}</a>\n'
                            f"by {escape(user)}"
                        )
                        await send_message(msg, post_type="atlas_pr", db=db)
                        await mark_pr_seen(db, pr_number, title)
                        posted += 1
        except Exception as e:
            logger.error("GitHub PRs fetch error: %s", e)

    logger.info("Atlas poll done — %d new items posted", posted)
