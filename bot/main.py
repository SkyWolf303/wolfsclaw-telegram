"""Entry point — scheduler setup and bot lifecycle."""

import asyncio
import logging
import random
import signal
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot.config import LOG_LEVEL, SKIP_STARTUP_POLL
from bot.db import get_db
from bot.queue import get_queue
from bot.sources.atlas import poll_atlas
from bot.sources.forum import poll_forum
from bot.sources.insights import poll_insights
from bot.sources.market import daily_summary, poll_market, poll_tvl, poll_fees, weekly_tvl_summary
from bot.sources.twitter import poll_twitter
from bot.sources.web import poll_web
from bot.sources.onchain import poll_onchain

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot.main")

POLLERS = [
    ("forum", poll_forum),
    ("market", poll_market),
    ("tvl", poll_tvl),
    ("atlas", poll_atlas),
    ("twitter", poll_twitter),
    ("insights", poll_insights),
    ("web", poll_web),
    ("onchain", poll_onchain),
    ("fees", poll_fees),
]


async def _run_poller(name: str, func, db) -> None:
    """Run a single poller with jitter and error protection."""
    jitter = random.randint(0, 300)
    logger.info("Running %s poller (jitter: %ds)…", name, jitter)
    await asyncio.sleep(jitter)
    try:
        await func(db)
    except Exception:
        logger.exception("Poller %s crashed — scheduler continues", name)


async def _run_all_pollers(db) -> None:
    """Run all pollers concurrently."""
    tasks = [_run_poller(name, func, db) for name, func in POLLERS]
    await asyncio.gather(*tasks)


async def _scheduled_poll() -> None:
    """Scheduled job: open DB, run all pollers, close DB."""
    db = await get_db()
    try:
        await _run_all_pollers(db)
    finally:
        await db.close()


async def _scheduled_daily_summary() -> None:
    db = await get_db()
    try:
        await daily_summary(db)
    finally:
        await db.close()


async def _scheduled_weekly_tvl() -> None:
    db = await get_db()
    try:
        await weekly_tvl_summary(db)
    finally:
        await db.close()


async def main() -> None:
    logger.info("Starting wolfsclaw-telegram bot…")

    scheduler = AsyncIOScheduler(timezone="UTC")

    # Every 4 hours: run all pollers
    scheduler.add_job(
        _scheduled_poll,
        IntervalTrigger(hours=1),
        id="poll_all",
        name="Poll all sources",
        max_instances=1,
    )

    # Daily at 09:00 UTC
    scheduler.add_job(
        _scheduled_daily_summary,
        CronTrigger(hour=9, minute=0),
        id="daily_summary",
        name="Daily market summary",
        max_instances=1,
    )

    # Every Monday at 09:00 UTC
    scheduler.add_job(
        _scheduled_weekly_tvl,
        CronTrigger(day_of_week="mon", hour=9, minute=0),
        id="weekly_tvl",
        name="Weekly TVL summary",
        max_instances=1,
    )

    scheduler.start()

    # Start message queue dispatcher
    queue = get_queue()
    await queue.start()

    # Startup poll
    if not SKIP_STARTUP_POLL:
        logger.info("Running startup poll…")
        db = await get_db()
        try:
            await _run_all_pollers(db)
        finally:
            await db.close()
    else:
        logger.info("Startup poll skipped (SKIP_STARTUP_POLL=true)")

    # Keep running until interrupted
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Bot is running. Press Ctrl+C to stop.")
    await stop_event.wait()

    scheduler.shutdown(wait=False)
    await queue.stop()
    logger.info("Bot stopped.")


if __name__ == "__main__":
    asyncio.run(main())
