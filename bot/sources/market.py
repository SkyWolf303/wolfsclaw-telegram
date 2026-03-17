"""Sky Live Data + DefiLlama market poller."""

import logging
from html import escape

import aiohttp

from bot.config import DEFILLAMA_SLUGS
from bot.db import (
    get_last_market_snapshot,
    get_last_tvl_snapshot,
    save_market_snapshot,
    save_tvl_snapshot,
)
from bot.telegram import send_message

logger = logging.getLogger(__name__)

SKY_LIVE_URL = "https://sky-ten-alpha.vercel.app/api/get-sky-live"
DEFILLAMA_TVL_URL = "https://api.llama.fi/tvl"
DEFILLAMA_FEES_URL = "https://api.llama.fi/summary/fees"
DEFILLAMA_PROTOCOL_URL = "https://api.llama.fi/protocol"
DEFILLAMA_OVERVIEW_FEES_URL = "https://api.llama.fi/overview/fees?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true"
DEFILLAMA_REVENUE_URL = "https://api.llama.fi/summary/fees/sky-lending?dataType=dailyRevenue"

_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Revenue alert thresholds
REVENUE_MILESTONE_THRESHOLD = 500_000  # $500K daily revenue
REVENUE_RANK_ALERT = 3  # Alert if Sky ranks top-3 on any timeframe


def _fmt_number(n: float | None, prefix: str = "", suffix: str = "") -> str:
    if n is None:
        return "N/A"
    if abs(n) >= 1_000_000_000:
        return f"{prefix}{n / 1_000_000_000:.2f}B{suffix}"
    if abs(n) >= 1_000_000:
        return f"{prefix}{n / 1_000_000:.2f}M{suffix}"
    if abs(n) >= 1_000:
        return f"{prefix}{n / 1_000:.2f}K{suffix}"
    return f"{prefix}{n:.4f}{suffix}"


def _pct_change(old: float | None, new: float | None) -> float | None:
    if old is None or new is None or old == 0:
        return None
    return ((new - old) / abs(old)) * 100


async def _fetch_sky_live(session: aiohttp.ClientSession) -> tuple[dict | None, dict | None]:
    """Returns (usds_data, sky_data) or (None, None) on failure."""
    try:
        async with session.get(SKY_LIVE_URL, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning("Sky Live API returned %d", resp.status)
                return None, None
            data = await resp.json()
    except Exception as e:
        logger.error("Sky Live fetch error: %s", e)
        return None, None

    if isinstance(data, list) and len(data) >= 2:
        return data[0], data[1]
    logger.warning("Sky Live API unexpected format: %s", type(data))
    return None, None


async def _fetch_tvl(session: aiohttp.ClientSession, slug: str) -> float | None:
    url = f"{DEFILLAMA_TVL_URL}/{slug}"
    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning("DefiLlama %s returned %d", slug, resp.status)
                return None
            val = await resp.json()
            if isinstance(val, (int, float)):
                return float(val)
            return None
    except Exception as e:
        logger.error("DefiLlama fetch error for %s: %s", slug, e)
        return None


async def poll_market(db) -> None:
    """Fetch Sky Live data and check alert conditions."""
    logger.info("Polling market data…")

    async with aiohttp.ClientSession() as session:
        usds_data, sky_data = await _fetch_sky_live(session)

    if usds_data is None or sky_data is None:
        logger.warning("Skipping market poll — no data")
        return

    usds_supply = usds_data.get("supply") or usds_data.get("totalSupply")
    sky_price = sky_data.get("price")
    sky_mcap = sky_data.get("marketCap") or sky_data.get("market_cap")

    # Convert to float safely
    usds_supply = float(usds_supply) if usds_supply is not None else None
    sky_price = float(sky_price) if sky_price is not None else None
    sky_mcap = float(sky_mcap) if sky_mcap is not None else None

    # Check against last snapshot
    last_usds = await get_last_market_snapshot(db, "USDS")
    last_sky = await get_last_market_snapshot(db, "SKY")

    usds_change = _pct_change(last_usds["supply"] if last_usds else None, usds_supply)
    sky_change = _pct_change(last_sky["price"] if last_sky else None, sky_price)

    alert_parts: list[str] = []

    if usds_change is not None and abs(usds_change) > 1:
        arrow = "📈" if usds_change > 0 else "📉"
        alert_parts.append(
            f"USDS Supply: {_fmt_number(usds_supply)} ({arrow} {usds_change:+.1f}% since last check)"
        )

    if sky_change is not None and abs(sky_change) > 5:
        arrow = "📈" if sky_change > 0 else "📉"
        alert_parts.append(
            f"SKY Price: {_fmt_number(sky_price, prefix='$')} ({arrow} {sky_change:+.1f}% since last check)"
        )

    if alert_parts:
        msg = "<b>📊 Market Update</b>\n" + "\n".join(alert_parts)
        if sky_mcap is not None:
            msg += f"\nSKY Market Cap: {_fmt_number(sky_mcap, prefix='$')}"
        msg += '\n🔗 <a href="https://sky-ten-alpha.vercel.app">Sky Live Data</a> · <a href="https://defillama.com/protocol/sky-lending">DefiLlama</a>'
        await send_message(msg, post_type="market_update", db=db)

    # Save snapshots
    await save_market_snapshot(db, "USDS", None, usds_supply, None)
    await save_market_snapshot(db, "SKY", sky_price, None, sky_mcap)

    logger.info("Market poll done")


async def poll_tvl(db) -> None:
    """Fetch DefiLlama TVL for all Sky ecosystem protocols."""
    logger.info("Polling TVL data…")
    alerts: list[str] = []

    async with aiohttp.ClientSession() as session:
        for slug in DEFILLAMA_SLUGS:
            tvl = await _fetch_tvl(session, slug)
            if tvl is None:
                continue

            last = await get_last_tvl_snapshot(db, slug)
            pct = _pct_change(last["tvl"] if last else None, tvl)

            if pct is not None and abs(pct) > 5:
                arrow = "📈" if pct > 0 else "📉"
                alerts.append(
                    f"  {escape(slug)}: {_fmt_number(tvl, prefix='$')} ({arrow} {pct:+.1f}%)"
                )

            await save_tvl_snapshot(db, slug, tvl)

    if alerts:
        alert_lines = "\n".join(alerts)
        links = "\n".join(
            f'🔗 <a href="https://defillama.com/protocol/{slug}">{escape(slug)}</a>'
            for slug in DEFILLAMA_SLUGS
            if any(slug in a for a in alerts)
        )
        msg = f"<b>🏦 TVL Update</b>\n{alert_lines}\n{links}"
        await send_message(msg, post_type="tvl_update", db=db)

    logger.info("TVL poll done — %d alerts", len(alerts))


async def daily_summary(db) -> None:
    """Post a daily market summary at 09:00 UTC."""
    logger.info("Building daily summary…")

    async with aiohttp.ClientSession() as session:
        usds_data, sky_data = await _fetch_sky_live(session)

    if usds_data is None or sky_data is None:
        logger.warning("Skipping daily summary — no data")
        return

    usds_supply = usds_data.get("supply") or usds_data.get("totalSupply")
    sky_price = sky_data.get("price")
    sky_mcap = sky_data.get("marketCap") or sky_data.get("market_cap")

    usds_supply = float(usds_supply) if usds_supply is not None else None
    sky_price = float(sky_price) if sky_price is not None else None
    sky_mcap = float(sky_mcap) if sky_mcap is not None else None

    last_usds = await get_last_market_snapshot(db, "USDS")
    last_sky = await get_last_market_snapshot(db, "SKY")
    usds_change = _pct_change(last_usds["supply"] if last_usds else None, usds_supply)
    sky_change = _pct_change(last_sky["price"] if last_sky else None, sky_price)

    lines = ["<b>📊 Daily Market Summary</b>"]
    usds_delta = f" ({usds_change:+.1f}%)" if usds_change is not None else ""
    lines.append(f"USDS Supply: {_fmt_number(usds_supply)}{usds_delta}")
    sky_delta = f" ({sky_change:+.1f}%)" if sky_change is not None else ""
    lines.append(f"SKY Price: {_fmt_number(sky_price, prefix='$')}{sky_delta}")
    if sky_mcap is not None:
        lines.append(f"SKY Market Cap: {_fmt_number(sky_mcap, prefix='$')}")

    # Add 24h revenue
    try:
        async with aiohttp.ClientSession() as rev_session:
            async with rev_session.get(DEFILLAMA_REVENUE_URL, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    rev_data = await resp.json()
                    chart = rev_data.get("totalDataChart", [])
                    if chart:
                        latest_rev = chart[-1][1] if chart[-1] else 0
                        if latest_rev:
                            lines.append(f"Sky 24h Revenue: {_fmt_number(latest_rev, prefix='$')}")
    except Exception:
        pass

    lines.append('🔗 <a href="https://insights.skyeco.com">Sky Insights</a> · <a href="https://defillama.com/protocol/sky-lending">DefiLlama</a>')
    msg = "\n".join(lines)
    await send_message(msg, post_type="daily_summary", db=db)
    await save_market_snapshot(db, "USDS", None, usds_supply, None)
    await save_market_snapshot(db, "SKY", sky_price, None, sky_mcap)
    logger.info("Daily summary posted")


async def poll_fees(db) -> None:
    """Fetch Sky protocol fees/revenue from DefiLlama — alerts on significant changes."""
    logger.info("Polling protocol fees…")
    FEE_SLUGS = ["sky-lending", "sparklend"]

    async with aiohttp.ClientSession() as session:
        for slug in FEE_SLUGS:
            try:
                url = f"{DEFILLAMA_FEES_URL}/{slug}?dataType=dailyFees"
                async with session.get(url, timeout=_TIMEOUT) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    # totalDataChart gives daily array, last entry = most recent
                    chart = data.get("totalDataChart", [])
                    if len(chart) >= 2:
                        latest_fees = chart[-1][1] if chart[-1] else 0
                        prev_fees = chart[-2][1] if chart[-2] else 0
                        if prev_fees and latest_fees:
                            pct = ((latest_fees - prev_fees) / abs(prev_fees)) * 100
                            if abs(pct) > 20:
                                arrow = "📈" if pct > 0 else "📉"
                                msg = (
                                    f"<b>💰 Protocol Fees — {escape(slug)}</b>\n"
                                    f"Daily fees: {_fmt_number(latest_fees, '$')} "
                                    f"({arrow} {pct:+.1f}% vs prior day)\n"
                                    f'🔗 <a href="https://defillama.com/protocol/{escape(slug)}">DefiLlama</a>'
                                )
                                await send_message(msg, post_type="fees_alert", db=db, priority=4)
            except Exception as e:
                logger.debug("Fees fetch error for %s: %s", slug, e)

    logger.info("Fees poll done")


async def poll_revenue(db) -> None:
    """Check Sky protocol revenue ranking and daily revenue milestones."""
    logger.info("Polling protocol revenue…")

    async with aiohttp.ClientSession() as session:
        # 1. Check fee/revenue ranking across all protocols
        try:
            async with session.get(DEFILLAMA_OVERVIEW_FEES_URL, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    protocols = data.get("protocols", [])
                    # Find Sky in the ranking
                    for proto in protocols:
                        name = (proto.get("name", "") or "").lower()
                        if name in ("sky", "sky-lending", "sky lending"):
                            # Check various timeframe rankings
                            for tf_key, tf_label in [("dailyFees", "24h"), ("total7d", "7d"), ("total30d", "30d")]:
                                val = proto.get(tf_key)
                                if val is None:
                                    continue
                                # Calculate rank for this timeframe
                                rank = 1
                                for other in protocols:
                                    other_val = other.get(tf_key)
                                    if other_val is not None and other_val > val:
                                        rank += 1
                                if rank <= REVENUE_RANK_ALERT:
                                    rank_key = f"revenue_rank_{tf_label}_{rank}"
                                    from bot.db import is_post_duplicate, log_post
                                    if not await is_post_duplicate(db, rank_key):
                                        msg = (
                                            f"<b>🏆 Sky Revenue Ranking</b>\n"
                                            f"Sky is #{rank} by protocol fees ({tf_label} timeframe)\n"
                                            f"Fees: {_fmt_number(val, prefix='$')}\n"
                                            f'🔗 <a href="https://defillama.com/fees">DefiLlama Fees</a>'
                                        )
                                        await send_message(msg, post_type="revenue_rank", db=db, priority=2)
                                        await log_post(db, "revenue_rank", rank_key)
                            break
        except Exception as e:
            logger.debug("Revenue ranking fetch error: %s", e)

        # 2. Check daily revenue milestones
        try:
            async with session.get(DEFILLAMA_REVENUE_URL, timeout=_TIMEOUT) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    chart = data.get("totalDataChart", [])
                    if chart:
                        latest = chart[-1][1] if chart[-1] else 0
                        if latest and latest > REVENUE_MILESTONE_THRESHOLD:
                            milestone_key = f"revenue_milestone_{int(latest // REVENUE_MILESTONE_THRESHOLD)}"
                            from bot.db import is_post_duplicate, log_post
                            if not await is_post_duplicate(db, milestone_key):
                                msg = (
                                    f"<b>💰 Revenue Milestone</b>\n"
                                    f"Sky daily revenue: {_fmt_number(latest, prefix='$')}\n"
                                    f'🔗 <a href="https://defillama.com/protocol/sky-lending">DefiLlama</a>'
                                )
                                await send_message(msg, post_type="revenue_milestone", db=db, priority=3)
                                await log_post(db, "revenue_milestone", milestone_key)
        except Exception as e:
            logger.debug("Revenue milestone fetch error: %s", e)

    logger.info("Revenue poll done")


async def weekly_tvl_summary(db) -> None:
    """Post a weekly TVL summary on Mondays 09:00 UTC."""
    logger.info("Building weekly TVL summary…")
    lines = ["<b>🏦 Weekly TVL Summary</b>"]

    async with aiohttp.ClientSession() as session:
        for slug in DEFILLAMA_SLUGS:
            tvl = await _fetch_tvl(session, slug)
            if tvl is None:
                lines.append(f"  {escape(slug)}: unavailable")
                continue
            last = await get_last_tvl_snapshot(db, slug)
            pct = _pct_change(last["tvl"] if last else None, tvl)
            delta = f" ({pct:+.1f}%)" if pct is not None else ""
            lines.append(f"  {escape(slug)}: {_fmt_number(tvl, prefix='$')}{delta}")
            await save_tvl_snapshot(db, slug, tvl)

    lines.append('🔗 <a href="https://defillama.com/protocol/sky-lending">DefiLlama — Sky</a>')
    msg = "\n".join(lines)
    await send_message(msg, post_type="weekly_tvl", db=db)
    logger.info("Weekly TVL summary posted")
