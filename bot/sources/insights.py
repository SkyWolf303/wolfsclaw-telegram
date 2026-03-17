"""Sky Insights page scraper — finds actual reports only."""

import logging
from html import escape
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from bot.db import is_report_seen, mark_report_seen
from bot.telegram import send_message

logger = logging.getLogger(__name__)

INSIGHTS_BASE = "https://insights.skyeco.com"
INSIGHTS_URL = f"{INSIGHTS_BASE}/insights"
_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Nav/footer paths to ignore
_SKIP_PATHS = {"/", "/about", "/contact", "/insights", "/legal/terms-of-use",
               "/legal/privacy-policy", "/legal/cookie-policy"}


async def _fetch_report_title(session: aiohttp.ClientSession, url: str) -> str | None:
    """Fetch the actual report page and extract its title."""
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
        soup = BeautifulSoup(html, "html.parser")
        # Try og:title first, then <h1>, then <title>
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            return og["content"].strip()
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        title = soup.find("title")
        if title:
            return title.get_text(strip=True)
    except Exception:
        pass
    return None


async def poll_insights(db) -> None:
    """Scrape Sky Insights for new report pages (paths starting with /insights/)."""
    logger.info("Polling Sky Insights…")
    posted = 0

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(INSIGHTS_URL, timeout=_TIMEOUT) as resp:
                if resp.status != 200:
                    logger.warning("Sky Insights returned %d", resp.status)
                    return
                html = await resp.text()
        except Exception as e:
            logger.error("Sky Insights fetch error: %s", e)
            return

        soup = BeautifulSoup(html, "html.parser")
        seen_hrefs: set[str] = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]

            # Only actual report paths: /insights/some-slug
            if not href.startswith("/insights/"):
                continue
            # Must have content after /insights/
            slug = href[len("/insights/"):]
            if not slug or slug in _SKIP_PATHS:
                continue

            url = urljoin(INSIGHTS_BASE, href)

            # Deduplicate within this scrape pass
            if url in seen_hrefs:
                continue
            seen_hrefs.add(url)

            if await is_report_seen(db, url):
                continue

            # Fetch the report page to get a real title
            title = await _fetch_report_title(session, url)
            if not title:
                # Derive from slug as fallback
                title = slug.replace("-", " ").title()

            msg = (
                f"<b>📈 New Sky Insights Report</b>\n"
                f"{escape(title)}\n"
                f'🔗 <a href="{escape(url)}">Read report</a>'
            )

            await send_message(msg, post_type="insights_report", priority=7, db=db)
            await mark_report_seen(db, url, title)
            posted += 1
            logger.info("Posted insights report: %s", title)

    logger.info("Insights poll done — %d new reports posted", posted)
