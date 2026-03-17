"""Message queue with rate-limited dispatcher.

Ensures posts stream gracefully:
- Minimum 45 seconds between any two posts
- After every 2 consecutive posts, a 3-minute cooldown
- Within same priority tier, newer source timestamps go first
- Content older than 24h is rejected at push time
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bot.timeutils import is_fresh, source_epoch, MAX_AGE_HOURS

logger = logging.getLogger(__name__)

MIN_INTERVAL_S: float = 45.0
BURST_COOLDOWN_S: float = 180.0
BURST_LIMIT: int = 2


@dataclass
class QueuedMessage:
    text: str
    post_type: str = "general"
    priority: int = 5
    source_time: Optional[datetime] = None  # verified from the source


class MessageQueue:
    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running = False
        self._task: asyncio.Task | None = None
        self._consecutive_count = 0
        self._last_sent_at: float = 0.0

    async def push(
        self,
        text: str,
        post_type: str = "general",
        priority: int = 5,
        source_time: Optional[datetime] = None,
    ) -> bool:
        """Add a message to the queue.

        Returns False and drops the message if source_time is stale (>24h).
        Sorts within priority tier by source_time descending (newest first).
        """
        # Reject stale content — must be verified from the source
        if source_time is not None and not is_fresh(source_time):
            logger.info(
                "Dropped stale content (%s, source_time=%s)",
                post_type,
                source_time.isoformat(),
            )
            return False

        msg = QueuedMessage(
            text=text,
            post_type=post_type,
            priority=priority,
            source_time=source_time,
        )
        # Sort key: (priority, -source_epoch) → highest priority + newest source first
        sort_key = (priority, source_epoch(source_time))
        await self._queue.put((sort_key, time.monotonic(), msg))
        logger.debug(
            "Queued %s [pri=%d src=%s] (qsize=%d)",
            post_type,
            priority,
            source_time.isoformat() if source_time else "unknown",
            self._queue.qsize(),
        )
        return True

    def qsize(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop(), name="message-dispatcher")
        logger.info("Message queue dispatcher started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Message queue dispatcher stopped")

    async def _dispatch_loop(self) -> None:
        from bot.telegram import _send_raw
        from bot.db import get_db

        # Dispatcher owns its own DB connection — never relies on caller's
        db = await get_db()

        try:
            while self._running:
                try:
                    try:
                        _, _, msg = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                    except asyncio.TimeoutError:
                        continue

                    now = time.monotonic()
                    elapsed = now - self._last_sent_at

                    if elapsed < MIN_INTERVAL_S:
                        wait = MIN_INTERVAL_S - elapsed
                        logger.debug("Rate limit: waiting %.0fs before next post", wait)
                        await asyncio.sleep(wait)

                    if self._consecutive_count >= BURST_LIMIT:
                        logger.info(
                            "Burst limit (%d posts) — cooling down %.0fs",
                            self._consecutive_count,
                            BURST_COOLDOWN_S,
                        )
                        await asyncio.sleep(BURST_COOLDOWN_S)
                        self._consecutive_count = 0

                    success = await _send_raw(msg.text, msg.post_type, db)

                    if success:
                        self._last_sent_at = time.monotonic()
                        self._consecutive_count += 1
                        remaining = self._queue.qsize()
                        if remaining > 0:
                            logger.info("Sent %s — %d more in queue", msg.post_type, remaining)

                    self._queue.task_done()

                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("Dispatcher loop error")
                    await asyncio.sleep(5)
        finally:
            await db.close()


_message_queue: MessageQueue | None = None


def get_queue() -> MessageQueue:
    global _message_queue
    if _message_queue is None:
        _message_queue = MessageQueue()
    return _message_queue
