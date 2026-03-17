"""Message queue with rate-limited dispatcher.

Ensures posts are streamed gracefully to the channel:
- Minimum 45 seconds between any two posts
- After every 2 consecutive posts, a 3-minute cooldown
- All sources push to this queue; dispatcher sends at controlled pace
- Queue is unbounded — messages are never dropped, just delayed
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Timing constants
MIN_INTERVAL_S: float = 45.0       # minimum gap between any two posts
BURST_COOLDOWN_S: float = 180.0    # cooldown after 2 consecutive posts
BURST_LIMIT: int = 2               # max posts before cooldown kicks in


@dataclass
class QueuedMessage:
    text: str
    post_type: str = "general"
    db: object = field(default=None, repr=False)
    priority: int = 5  # lower = higher priority (1=urgent, 5=normal, 9=low)


class MessageQueue:
    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._running = False
        self._task: asyncio.Task | None = None
        self._consecutive_count = 0
        self._last_sent_at: float = 0.0

    async def push(self, text: str, post_type: str = "general", db=None, priority: int = 5) -> None:
        """Add a message to the queue."""
        msg = QueuedMessage(text=text, post_type=post_type, db=db, priority=priority)
        # PriorityQueue sorts by first element of tuple
        await self._queue.put((priority, time.monotonic(), msg))
        logger.debug("Queued %s message (qsize=%d)", post_type, self._queue.qsize())

    def qsize(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        """Start the background dispatcher."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._dispatch_loop(), name="message-dispatcher")
        logger.info("Message queue dispatcher started")

    async def stop(self) -> None:
        """Stop the dispatcher gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Message queue dispatcher stopped")

    async def _dispatch_loop(self) -> None:
        """Main dispatcher loop — pulls from queue and sends with rate limiting."""
        from bot.telegram import _send_raw  # imported here to avoid circular

        while self._running:
            try:
                # Wait for a message (with timeout so we can check _running)
                try:
                    _, _, msg = await asyncio.wait_for(self._queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue

                now = time.monotonic()
                elapsed = now - self._last_sent_at

                # Enforce minimum interval between posts
                if elapsed < MIN_INTERVAL_S:
                    wait = MIN_INTERVAL_S - elapsed
                    logger.debug("Rate limit: waiting %.0fs before next post", wait)
                    await asyncio.sleep(wait)

                # After burst limit, enforce cooldown
                if self._consecutive_count >= BURST_LIMIT:
                    logger.info(
                        "Burst limit hit (%d posts) — cooling down %.0fs",
                        self._consecutive_count,
                        BURST_COOLDOWN_S,
                    )
                    await asyncio.sleep(BURST_COOLDOWN_S)
                    self._consecutive_count = 0

                # Send it
                success = await _send_raw(msg.text, msg.post_type, msg.db)

                if success:
                    self._last_sent_at = time.monotonic()
                    self._consecutive_count += 1
                    remaining = self._queue.qsize()
                    if remaining > 0:
                        logger.info(
                            "Sent %s — %d more in queue",
                            msg.post_type,
                            remaining,
                        )
                else:
                    # On failure, don't count toward burst
                    pass

                self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Dispatcher loop error")
                await asyncio.sleep(5)


# Global singleton queue
_message_queue: MessageQueue | None = None


def get_queue() -> MessageQueue:
    global _message_queue
    if _message_queue is None:
        _message_queue = MessageQueue()
    return _message_queue
