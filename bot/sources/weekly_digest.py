"""Friday 16:00 UTC — weekly Sky ecosystem intelligence digest for Telegram."""

import logging
from datetime import timedelta
from html import escape

import aiohttp

from bot.config import DEFILLAMA_SLUGS, SKY_FORUM_BASE_URL, SKY_FORUM_API_KEY
from bot.db import get_last_market_snapshot, get_last_tvl_snapshot
from bot.telegram import send_message

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=30)
_GOV_API = "https://vote.sky.money/api"
_DEFILLAMA_TVL_URL = "https://api.llama.fi/tvl"
_DEFILLAMA_REVENUE_URL = "https://api.llama.fi/summary/fees/sky-lending?dataType=dailyRevenue"
SKY_LIVE_URL = "https://sky-ten-alpha.vercel.app/api/get-sky-live"


def _fmt(n: float, prefix: str = "", suffix: str = "") -> str:
    if n >= 1_000_000_000:
        return f"{prefix}{n / 1_000_000_000:.2f}B{suffix}"
    if n >= 1_000_000:
        return f"{prefix}{n / 1_000_000:.2f}M{suffix}"
    if n >= 1_000:
        return f"{prefix}{n / 1_000:.2f}K{suffix}"
    return f"{prefix}{n:.2f}{suffix}"


async def weekly_digest(db) -> None:
    """Post the Friday 16:00 UTC weekly intelligence digest."""
    logger.info("Building weekly digest…")

    from bot.timeutils import utcnow
    now = utcnow()
    week_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    sections: list[str] = []
    sections.append(f"<b>WEEKLY SKY DIGEST — WolfsClaw</b>")
    sections.append(f"<i>{week_start} to {now.strftime('%Y-%m-%d')}</i>")

    # ── T1: Governance — Active Votes & Polls ───────────────────
    gov_lines: list[str] = []
    async with aiohttp.ClientSession() as session:
        # Executive votes
        try:
            url = f"{_GOV_API}/executive?network=mainnet&start=0&limit=5&sortBy=active"
            async with session.get(url, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    proposals = data if isinstance(data, list) else data.get("proposals", data.get("data", []))
                    if isinstance(proposals, list):
                        active = [p for p in proposals if p.get("active")]
                        if active:
                            gov_lines.append(f"  Executive votes active: {len(active)}")
                            for p in active[:3]:
                                title = escape(p.get("title", "")[:60])
                                gov_lines.append(f"  • {title}")
        except Exception:
            logger.debug("Error fetching execs for digest")

        # Polls closing soon (within 48h)
        try:
            url = f"{_GOV_API}/polling/all-polls?network=mainnet&pageSize=10&page=1"
            async with session.get(url, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    polls = data if isinstance(data, list) else data.get("polls", data.get("data", []))
                    if isinstance(polls, list):
                        from bot.timeutils import parse_iso
                        closing_soon = []
                        for p in polls:
                            end = parse_iso(p.get("endDate", ""))
                            if end and (end - now).total_seconds() < 48 * 3600 and end > now:
                                closing_soon.append(p)
                        if closing_soon:
                            gov_lines.append(f"  Polls closing within 48h: {len(closing_soon)}")
                            for p in closing_soon[:3]:
                                title = escape(p.get("title", "")[:60])
                                gov_lines.append(f"  • {title}")
                        elif polls:
                            gov_lines.append(f"  Active polls: {len(polls)}")
        except Exception:
            logger.debug("Error fetching polls for digest")

    if gov_lines:
        sections.append("\n<b>🗳️ Governance</b>")
        sections.append("\n".join(gov_lines))

    # ── T2: This Week — Spell, Atlas PRs, Market ────────────────
    week_lines: list[str] = []

    # Next spell (reuse Slack schedule data)
    try:
        from bot.sources.spells import get_next_spell
        spell_info = get_next_spell()
        if spell_info:
            spell_date, crafter, days_until = spell_info
            week_lines.append(f"  Next spell: {spell_date.strftime('%Y-%m-%d')} ({crafter}) — {days_until}d")
    except ImportError:
        pass

    # Open Atlas PRs count
    try:
        from bot.config import GITHUB_TOKEN
        gh_headers: dict[str, str] = {"Accept": "application/vnd.github.v3+json"}
        if GITHUB_TOKEN:
            gh_headers["Authorization"] = f"token {GITHUB_TOKEN}"
        async with aiohttp.ClientSession(headers=gh_headers) as gh_session:
            async with gh_session.get(
                "https://api.github.com/repos/sky-ecosystem/next-gen-atlas/pulls?state=open&per_page=1",
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    # GitHub returns Link header with total count info
                    prs = await resp.json()
                    link = resp.headers.get("Link", "")
                    # Simple count from response
                    if prs:
                        week_lines.append(f"  Open Atlas PRs: {len(prs)}+")
    except Exception:
        logger.debug("Error fetching Atlas PRs for digest")

    if week_lines:
        sections.append("\n<b>📋 This Week</b>")
        sections.append("\n".join(week_lines))

    # ── T2b: Protocol Metrics ────────────────────────────────────
    metric_lines: list[str] = []
    async with aiohttp.ClientSession() as session:
        # USDS/SKY from Sky Live
        try:
            async with session.get(SKY_LIVE_URL, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if isinstance(data, list) and len(data) >= 2:
                        usds_supply = data[0].get("supply") or data[0].get("totalSupply")
                        sky_price = data[1].get("price")
                        sky_mcap = data[1].get("marketCap") or data[1].get("market_cap")
                        if usds_supply:
                            last = await get_last_market_snapshot(db, "USDS")
                            delta = ""
                            if last and last["supply"]:
                                pct = ((float(usds_supply) - last["supply"]) / abs(last["supply"])) * 100
                                delta = f" ({pct:+.1f}% this week)"
                            metric_lines.append(f"  USDS Supply: {_fmt(float(usds_supply))}{delta}")
                        if sky_price:
                            last = await get_last_market_snapshot(db, "SKY")
                            delta = ""
                            if last and last["price"]:
                                pct = ((float(sky_price) - last["price"]) / abs(last["price"])) * 100
                                delta = f" ({pct:+.1f}%)"
                            metric_lines.append(f"  SKY Price: {_fmt(float(sky_price), '$')}{delta}")
                        if sky_mcap:
                            metric_lines.append(f"  SKY MCap: {_fmt(float(sky_mcap), '$')}")
        except Exception:
            logger.debug("Error fetching Sky Live for digest")

        # TVL per protocol
        for slug in DEFILLAMA_SLUGS[:3]:
            try:
                async with session.get(f"{_DEFILLAMA_TVL_URL}/{slug}", timeout=_TIMEOUT) as resp:
                    if resp.status == 200:
                        tvl = await resp.json()
                        if isinstance(tvl, (int, float)):
                            last = await get_last_tvl_snapshot(db, slug)
                            delta = ""
                            if last and last["tvl"]:
                                pct = ((float(tvl) - last["tvl"]) / abs(last["tvl"])) * 100
                                delta = f" ({pct:+.1f}%)"
                            metric_lines.append(f"  {escape(slug)}: {_fmt(float(tvl), '$')}{delta}")
            except Exception:
                pass

        # Daily revenue
        try:
            async with session.get(_DEFILLAMA_REVENUE_URL, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    chart = data.get("totalDataChart", [])
                    if chart:
                        latest = chart[-1][1] if chart[-1] else 0
                        if latest:
                            metric_lines.append(f"  24h Revenue: {_fmt(latest, '$')}")
        except Exception:
            pass

    if metric_lines:
        sections.append("\n<b>📊 Protocol Metrics</b>")
        sections.append("\n".join(metric_lines))

    # ── T3: Forum Activity ──────────────────────────────────────
    forum_lines: list[str] = []
    try:
        headers = {"Accept": "application/json"}
        if SKY_FORUM_API_KEY:
            headers["Api-Key"] = SKY_FORUM_API_KEY
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                f"{SKY_FORUM_BASE_URL}/top.json?period=weekly",
                timeout=_TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    topics = data.get("topic_list", {}).get("topics", [])
                    for t in topics[:5]:
                        title = escape(t.get("title", "")[:60])
                        replies = t.get("posts_count", 0) - 1
                        forum_lines.append(f"  • {title} ({replies} replies)")
    except Exception:
        logger.debug("Error fetching forum top for digest")

    if forum_lines:
        sections.append("\n<b>🔔 Top Forum Topics This Week</b>")
        sections.append("\n".join(forum_lines))

    # ── Footer ──────────────────────────────────────────────────
    sections.append(
        '\n🔗 <a href="https://vote.sky.money">Governance</a> · '
        '<a href="https://defillama.com/protocol/sky-lending">DefiLlama</a> · '
        f'<a href="{SKY_FORUM_BASE_URL}">Forum</a>'
    )

    msg = "\n".join(sections)
    await send_message(msg, post_type="weekly_digest", db=db, priority=4, enrich=False)
    logger.info("Weekly digest posted")
