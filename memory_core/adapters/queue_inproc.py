from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("memaso.queue")


class InProcQueue:
    """At-least-once within one process: a failing handler is retried with backoff, then on_dead gets the job."""

    def __init__(self, workers: int = 2, max_attempts: int = 5, retry_delay: float = 1.0):
        self.workers, self.max_attempts, self.retry_delay = workers, max_attempts, retry_delay
        self._q: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    async def enqueue(self, job: dict) -> None:
        self._q.put_nowait((job, 1))

    async def start(self, handler, on_dead=None) -> None:
        async def worker():
            while True:
                job, attempt = await self._q.get()
                try:
                    await handler(job)
                except Exception as e:  # noqa: BLE001 - any handler failure means retry
                    if attempt < self.max_attempts:
                        await asyncio.sleep(self.retry_delay * 2 ** (attempt - 1))
                        self._q.put_nowait((job, attempt + 1))
                    else:
                        log.warning("job dead after %d attempts: %s", attempt, type(e).__name__)
                        if on_dead:
                            try:
                                await on_dead(job, e)
                            except Exception:  # noqa: BLE001
                                log.exception("on_dead failed")
                finally:
                    self._q.task_done()

        self._tasks = [asyncio.create_task(worker()) for _ in range(self.workers)]

    async def join(self) -> None:
        """Wait until every enqueued job (including retries) is finished. For tests and graceful shutdown."""
        await self._q.join()

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
