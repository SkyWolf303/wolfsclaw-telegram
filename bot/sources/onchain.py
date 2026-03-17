"""Free on-chain poller using public Ethereum RPCs and governance APIs.

No API keys required. Uses:
- Ethereum public RPC (publicnode.com) for contract reads
- vote.sky.money governance API for executive votes and polls
- DefiLlama stablecoins API for USDS chain breakdown
"""

import hashlib
import logging
from html import escape

import aiohttp

from bot.db import is_post_duplicate, log_post
from bot.telegram import send_message

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Free public Ethereum RPC (no key needed)
PUBLIC_RPC = "https://ethereum.publicnode.com"

# Key contract addresses
USDS_ADDRESS = "0xdC035D45d973E3EC169d2276DDab16f1e407384F"
SKY_ADDRESS = "0x56072C171D33Ad39B32D59aD616e0bCcAb0c4051"
SUSDS_ADDRESS = "0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD"

# Sky governance API (vote.sky.money)
GOV_API_BASE = "https://vote.sky.money/api"

# DefiLlama stablecoins
STABLECOINS_API = "https://stablecoins.llama.fi"
USDS_LLAMA_ID = "195"  # USDS on DefiLlama stablecoins

# Sky Savings Rate contract (DSR/SSR)
SKY_SAVINGS_ADDRESS = "0xa3931d71877C0E7a3148CB7Eb4463524FEc27fbD"


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _eth_call(session: aiohttp.ClientSession, to: str, data: str) -> str | None:
    """Make a free eth_call to read contract state."""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": to, "data": data}, "latest"],
        "id": 1,
    }
    try:
        async with session.post(PUBLIC_RPC, json=payload, timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                data_resp = await resp.json()
                return data_resp.get("result")
    except Exception as e:
        logger.debug("eth_call failed: %s", e)
    return None


def _decode_uint256(hex_val: str | None) -> int | None:
    if not hex_val or hex_val == "0x":
        return None
    try:
        return int(hex_val, 16)
    except ValueError:
        return None


def _fmt(n: float, prefix: str = "", suffix: str = "") -> str:
    if n >= 1_000_000_000:
        return f"{prefix}{n / 1_000_000_000:.2f}B{suffix}"
    if n >= 1_000_000:
        return f"{prefix}{n / 1_000_000:.2f}M{suffix}"
    if n >= 1_000:
        return f"{prefix}{n / 1_000:.2f}K{suffix}"
    return f"{prefix}{n:.4f}{suffix}"


# totalSupply() selector
TOTAL_SUPPLY_DATA = "0x18160ddd"
# chi() selector for sUSDS rate
CHI_DATA = "0xc92aecc4"


async def _fetch_usds_supply(session: aiohttp.ClientSession) -> float | None:
    """Read USDS totalSupply() directly from contract."""
    raw = await _eth_call(session, USDS_ADDRESS, TOTAL_SUPPLY_DATA)
    val = _decode_uint256(raw)
    if val is not None:
        return val / 1e18  # 18 decimals
    return None


async def _fetch_governance_execs(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch recent executive votes from Sky governance API."""
    try:
        url = f"{GOV_API_BASE}/executive?network=mainnet&start=0&limit=5&sortBy=active"
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logger.debug("Gov execs fetch error: %s", e)
    return []


async def _fetch_governance_polls(session: aiohttp.ClientSession) -> list[dict]:
    """Fetch active governance polls."""
    try:
        url = f"{GOV_API_BASE}/polling/all-polls?network=mainnet&pageSize=5&page=1"
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                data = await resp.json()
                # API returns {polls: [...]} or just [...]
                if isinstance(data, dict):
                    return data.get("polls", [])
                return data
    except Exception as e:
        logger.debug("Gov polls fetch error: %s", e)
    return []


async def _fetch_usds_chains(session: aiohttp.ClientSession) -> dict | None:
    """Get USDS supply breakdown by chain from DefiLlama stablecoins API."""
    try:
        url = f"{STABLECOINS_API}/stablecoin/{USDS_LLAMA_ID}"
        async with session.get(url, timeout=_TIMEOUT) as resp:
            if resp.status == 200:
                return await resp.json()
    except Exception as e:
        logger.debug("USDS chains fetch error: %s", e)
    return None


async def poll_onchain(db) -> None:
    """Poll free on-chain and governance data sources."""
    logger.info("Polling on-chain data…")

    async with aiohttp.ClientSession() as session:
        # ── 1. USDS on-chain supply ────────────────────────────
        usds_supply = await _fetch_usds_supply(session)
        if usds_supply is not None:
            logger.info("On-chain USDS supply: %.2fB", usds_supply / 1e9)

        # ── 2. USDS chain distribution (DefiLlama stablecoins) ─
        usds_chains = await _fetch_usds_chains(session)
        if usds_chains:
            chain_data = usds_chains.get("chainBalances", {})
            if chain_data:
                top_chains = sorted(
                    ((chain, info.get("tokens", [{}])[-1].get("circulating", {}).get("peggedUSD", 0))
                     for chain, info in chain_data.items()),
                    key=lambda x: x[1], reverse=True
                )[:5]

                lines = ["<b>🔗 USDS Chain Distribution</b>"]
                total = sum(v for _, v in top_chains)
                for chain, amount in top_chains:
                    if amount > 0:
                        pct = (amount / total * 100) if total > 0 else 0
                        lines.append(f"  {escape(chain)}: {_fmt(amount, '$')} ({pct:.1f}%)")

                if len(lines) > 1:
                    msg = "\n".join(lines)
                    if not await is_post_duplicate(db, msg):
                        await send_message(msg, post_type="onchain_chains", db=db, priority=6)
                        await log_post(db, "onchain_chains", msg)

        # ── 3. Active Governance Executives ───────────────────
        execs = await _fetch_governance_execs(session)
        for exec_vote in execs[:3]:
            title = exec_vote.get("title", "")
            address = exec_vote.get("address", "")
            passed = exec_vote.get("passed", False)
            active = exec_vote.get("active", False)
            url = f"https://vote.sky.money/executive/{address}" if address else "https://vote.sky.money"

            if not title:
                continue

            status = "✅ Passed" if passed else ("🗳️ Active" if active else "⏳ Pending")
            content_hash = _content_hash(f"exec_{address}_{passed}")

            if await is_post_duplicate(db, content_hash):
                continue

            msg = (
                f"<b>🗳️ Governance Executive</b>\n"
                f"{status}: {escape(title[:200])}\n"
                f'🔗 <a href="{escape(url)}">View vote</a>'
            )
            await send_message(msg, post_type="gov_exec", db=db, priority=3)
            await log_post(db, "gov_exec", content_hash)

        # ── 4. Active Governance Polls ─────────────────────────
        polls = await _fetch_governance_polls(session)
        for poll in polls[:3]:
            poll_id = poll.get("pollId") or poll.get("id")
            title = poll.get("title", "")
            end_date = poll.get("endDate", "")
            url = f"https://vote.sky.money/polling/{poll_id}" if poll_id else "https://vote.sky.money"

            if not title or not poll_id:
                continue

            content_hash = _content_hash(f"poll_{poll_id}")

            if await is_post_duplicate(db, content_hash):
                continue

            end_str = f" · ends {end_date[:10]}" if end_date else ""
            msg = (
                f"<b>🗳️ Governance Poll</b>\n"
                f"{escape(title[:200])}{escape(end_str)}\n"
                f'🔗 <a href="{escape(url)}">Vote now</a>'
            )
            await send_message(msg, post_type="gov_poll", db=db, priority=3)
            await log_post(db, "gov_poll", content_hash)

        # ── 5. Poll Outcome Tracking ───────────────────────────
        # Check if any recently closed polls need outcome reporting
        for poll in polls:
            poll_id = poll.get("pollId") or poll.get("id")
            if not poll_id:
                continue

            end_date_str = poll.get("endDate", "")
            if not end_date_str:
                continue

            from bot.timeutils import parse_iso, utcnow
            end_date = parse_iso(end_date_str)
            if not end_date or end_date > utcnow():
                continue  # Still active

            outcome_hash = _content_hash(f"poll_outcome_{poll_id}")
            if await is_post_duplicate(db, outcome_hash):
                continue

            title = poll.get("title", "Governance Poll")
            slug = poll.get("slug", str(poll_id))
            poll_url = f"https://vote.sky.money/polling/{slug}"

            # Fetch poll detail for tally
            winning_option = "Results pending"
            sky_votes_str = ""
            voters_str = ""
            try:
                detail_url = f"{GOV_API_BASE}/polling/{poll_id}"
                async with session.get(detail_url, timeout=_TIMEOUT) as resp:
                    if resp.status == 200:
                        detail = await resp.json()
                        tally = detail.get("tally", {})
                        options = detail.get("options", {})
                        if tally:
                            max_votes = 0
                            for opt_id, vote_count in tally.items():
                                try:
                                    vc = float(vote_count)
                                    if vc > max_votes:
                                        max_votes = vc
                                        winning_option = options.get(str(opt_id), f"Option {opt_id}")
                                except (ValueError, TypeError):
                                    pass
                            if max_votes > 0:
                                sky_votes_str = f"\nSKY votes: {_fmt(max_votes)}"
                        num_voters = detail.get("numUniqueVoters", "")
                        if num_voters:
                            voters_str = f"\nVoters: {num_voters}"
            except Exception:
                pass

            msg = (
                f"<b>✅ Poll Result</b>\n"
                f"{escape(title[:200])}\n"
                f"Winner: <b>{escape(winning_option)}</b>"
                f"{escape(sky_votes_str)}{escape(voters_str)}\n"
                f'🔗 <a href="{escape(poll_url)}">View result</a>'
            )
            await send_message(msg, post_type="poll_outcome", db=db, priority=3)
            await log_post(db, "poll_outcome", outcome_hash)

    logger.info("On-chain poll done")
