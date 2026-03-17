"""Sky Insights page scraper."""

import logging
from html import escape
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from bot.db import is_report_seen, mark_report_seen
from bot.telegram import send_message

logger = logging.getLogger(__name__)

INSIGHTS_URL = "https://insights.skyeco.com/insights"
_TIMEOUT = aiohttp.ClientTimeout(total=30)


async def poll_insights(db) -> None:
    """Scrape the Sky Insights page for new reports."""
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

    # Look for links that look like reports (anchor tags with href)
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)

        # Skip navigation / non-report links
        if not text or len(text) < 5:
            continue
        if href.startswith("#") or href.startswith("javascript:"):
            continue

        url = urljoin(INSIGHTS_URL, href)

        # Only track links that look like they're on the insights domain
        if "insights.skyeco.com" not in url and not href.startswith("/"):
            continue

        if await is_report_seen(db, url):
            continue

        msg = (
            f"<b>📈 New Report</b>\n"
            f'<a href="{escape(url)}">{escape(text[:200])}</a>'
        )
        await send_message(msg, post_type="insights_report", db=db)
        await mark_report_seen(db, url, text)
        posted += 1

    logger.info("Insights poll done — %d new reports posted", posted)
