from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger("memaso.queue")


class RedisQueue:
    """Redis list queue with a processing list: a job is only dropped after the handler succeeds (at-least-once).
    ponytail: no visibility timeout; a crashed worker's jobs stay in `<name>:processing` until requeued by hand."""

    def __init__(self, url: str = "redis://localhost:6379/0", name: str = "memaso:jobs", workers: int = 2,
                 max_attempts: int = 5, retry_delay: float = 1.0):
        import redis.asyncio as aioredis  # optional dependency: pip install memaso[redis]

        self.r = aioredis.from_url(url, decode_responses=True)
        self.name, self.workers = name, workers
        self.max_attempts, self.retry_delay = max_attempts, retry_delay
        self._tasks: list[asyncio.Task] = []

    async def enqueue(self, job: dict) -> None:
        await self.r.lpush(self.name, json.dumps({"job": job, "attempt": 1}))

    async def start(self, handler, on_dead=None) -> None:
        async def worker():
            while True:
                try:
                    raw = await self.r.brpoplpush(self.name, self.name + ":processing", timeout=1)
                    if raw is None:
                        continue
                    msg = json.loads(raw)
                    try:
                        await handler(msg["job"])
                    except Exception as e:  # noqa: BLE001
                        if msg["attempt"] < self.max_attempts:
                            await asyncio.sleep(self.retry_delay * 2 ** (msg["attempt"] - 1))
                            await self.r.lpush(self.name, json.dumps({"job": msg["job"], "attempt": msg["attempt"] + 1}))
                        elif on_dead:
                            await on_dead(msg["job"], e)
                    await self.r.lrem(self.name + ":processing", 1, raw)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - a Redis hiccup or a failing on_dead must not kill the worker
                    log.exception("queue worker error; backing off")
                    await asyncio.sleep(min(5.0, 0.5 + self.retry_delay))

        self._tasks = [asyncio.create_task(worker()) for _ in range(self.workers)]

    async def join(self) -> None:
        while await self.r.llen(self.name) or await self.r.llen(self.name + ":processing"):
            await asyncio.sleep(0.05)

    async def close(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.r.aclose()
