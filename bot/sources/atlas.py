"""Sky Atlas GitHub commits + PR poller — with section-level diff analysis."""

import logging
import re
from html import escape
from urllib.parse import quote

import aiohttp

from bot.config import ATLAS_FILE_PATH, ATLAS_REPO_NAME, ATLAS_REPO_OWNER, GITHUB_TOKEN
from bot.db import is_commit_seen, is_pr_seen, mark_commit_seen, mark_pr_seen
from bot.telegram import send_message

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_API_BASE = "https://api.github.com"

# Atlas section prefixes by scope — helps label what changed
ATLAS_SCOPE_LABELS = {
    "A.0": "Scope: Atlas Rules",
    "A.1": "Scope: Stability",
    "A.2": "Scope: Support",
    "A.3": "Scope: Protocol",
    "A.4": "Scope: Accessibility",
    "A.5": "Scope: Governance",
    "A.6": "Scope: Agent Framework",
}

# High-priority Atlas sections (governance, agents, executors)
HIGH_PRIORITY_SECTIONS = {"A.5", "A.6", "A.3"}


def _gh_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return headers


def _extract_changed_sections(patch: str) -> list[str]:
    """Extract Atlas section IDs (like A.6.1.2) mentioned in a diff patch."""
    if not patch:
        return []
    # Match Atlas section numbers in diff lines
    sections = re.findall(r'\bA\.\d+(?:\.\d+)*\b', patch)
    # Deduplicate, keep top-level scope (A.x)
    seen: set[str] = set()
    result: list[str] = []
    for s in sections:
        top = ".".join(s.split(".")[:2])  # e.g. A.6
        if top not in seen:
            seen.add(top)
            result.append(top)
    return result[:5]  # max 5 sections


async def _fetch_commit_diff(session: aiohttp.ClientSession, sha: str) -> str:
    """Fetch the diff for a specific commit to extract changed Atlas sections."""
    url = f"{_API_BASE}/repos/{ATLAS_REPO_OWNER}/{ATLAS_REPO_NAME}/commits/{sha}"
    try:
        async with session.get(
            url,
            headers={**_gh_headers(), "Accept": "application/vnd.github.diff"},
            timeout=_TIMEOUT
        ) as resp:
            if resp.status == 200:
                return await resp.text()
    except Exception as e:
        logger.debug("Failed to fetch diff for %s: %s", sha, e)
    return ""


async def _fetch_pr_files(session: aiohttp.ClientSession, pr_number: int) -> list[str]:
    """Get list of changed files in a PR."""
    url = f"{_API_BASE}/repos/{ATLAS_REPO_OWNER}/{ATLAS_REPO_NAME}/pulls/{pr_number}/files"
    try:
        async with session.get(url, headers=_gh_headers(), timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                files = await resp.json()
                return [f.get("filename", "") for f in files]
    except Exception as e:
        logger.debug("Failed to fetch PR files for #%d: %s", pr_number, e)
    return []


def _sections_to_labels(sections: list[str]) -> str:
    """Convert section IDs to human-readable scope labels."""
    labels = []
    for s in sections:
        label = ATLAS_SCOPE_LABELS.get(s, s)
        labels.append(label)
    return ", ".join(labels) if labels else ""


def _is_high_priority(sections: list[str]) -> bool:
    return any(s in HIGH_PRIORITY_SECTIONS for s in sections)


async def poll_atlas(db) -> None:
    """Poll GitHub for new Atlas commits and open PRs, with section-level detail."""
    logger.info("Polling Sky Atlas (GitHub)…")
    posted = 0

    async with aiohttp.ClientSession() as session:
        # ── Commits ──────────────────────────────────────────────
        encoded_path = quote(ATLAS_FILE_PATH)
        commits_url = (
            f"{_API_BASE}/repos/{ATLAS_REPO_OWNER}/{ATLAS_REPO_NAME}"
            f"/commits?path={encoded_path}&per_page=10"
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
                        author = (
                            c.get("commit", {}).get("author", {}).get("name", "")
                            or c.get("author", {}).get("login", "unknown")
                        )
                        html_url = c.get("html_url", "")

                        # Fetch diff to find which Atlas sections changed
                        diff = await _fetch_commit_diff(session, sha)
                        sections = _extract_changed_sections(diff)
                        scope_label = _sections_to_labels(sections)
                        priority = 2 if _is_high_priority(sections) else 3

                        lines = [f"<b>📜 Atlas Change</b>"]
                        lines.append(f"<b>by {escape(author)}</b>")
                        lines.append(escape(message[:200]))
                        if scope_label:
                            lines.append(f"<i>Touches: {escape(scope_label)}</i>")
                        lines.append(f'🔗 <a href="{html_url}">View on GitHub</a>')

                        msg = "\n".join(lines)
                        await send_message(msg, post_type="atlas_commit", priority=priority, db=db)
                        await mark_commit_seen(db, sha, message)
                        posted += 1

        except Exception as e:
            logger.error("GitHub commits fetch error: %s", e)

        # ── Open PRs ────────────────────────────────────────────
        prs_url = (
            f"{_API_BASE}/repos/{ATLAS_REPO_OWNER}/{ATLAS_REPO_NAME}"
            f"/pulls?state=open&per_page=10"
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
                        body = (pr.get("body", "") or "")[:500]

                        # Get changed files to identify Atlas scope
                        files = await _fetch_pr_files(session, pr_number)
                        sections = _extract_changed_sections(" ".join(files) + " " + body)
                        scope_label = _sections_to_labels(sections)
                        priority = 2 if _is_high_priority(sections) else 3

                        lines = [f"<b>📜 Atlas — Open PR #{pr_number}</b>"]
                        lines.append(f"<b>by {escape(user)}</b>")
                        lines.append(escape(title[:200]))
                        if scope_label:
                            lines.append(f"<i>Scope: {escape(scope_label)}</i>")
                        lines.append(f'🔗 <a href="{html_url}">View PR</a>')

                        msg = "\n".join(lines)
                        await send_message(msg, post_type="atlas_pr", priority=priority, db=db)
                        await mark_pr_seen(db, pr_number, title)
                        posted += 1

        except Exception as e:
            logger.error("GitHub PRs fetch error: %s", e)

    logger.info("Atlas poll done — %d new items posted", posted)
