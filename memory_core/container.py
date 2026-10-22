from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

from .config import Config

log = logging.getLogger("memaso.container")


@dataclass
class Container:
    """Every port, wired once. Core functions take this and nothing else. `pg` is only a start()/close()
    lifecycle handle and `lock(tenant, subject)` an async context manager (a cross-process mutex): both come
    from whichever store adapter bootstrap picked."""

    cfg: Config
    pg: object
    llm: object
    embedder: object
    docs: object
    mems: object      # MemoryStore
    vectors: object   # VectorStore
    keywords: object  # KeywordIndex
    queue: object
    parser: object
    auth: object
    aux: object
    lock: Callable
    chunks: object = None           # ChunkStore (verbatim layer)
    chunk_keywords: object = None   # KeywordIndex over chunks
    _sweep: asyncio.Task | None = field(default=None, repr=False)
    _clean: set = field(default_factory=set, repr=False)  # tenants known to have every vector for the current model

    async def start(self) -> None:
        from .ingest import pipeline

        await self.pg.start()
        try:
            probe = (await self.embedder.embed(["dimension probe"]))[0]
        except Exception as e:  # noqa: BLE001 - an embedder that is down at boot is retried per request
            log.warning("embedder probe failed (%s); skipping dimension check", type(e).__name__)
        else:
            if len(probe) != self.embedder.dim:
                await self.pg.close()
                raise RuntimeError(f"embedder returns {len(probe)}-dimension vectors but is configured for "
                                   f"dimension {self.embedder.dim}; fix embedder.dim")

        async def handler(job: dict) -> None:
            await pipeline.process(self, job)

        async def on_dead(job: dict, err: Exception) -> None:
            await pipeline.mark_dead(self, job, err)

        await self.queue.start(handler, on_dead)
        self._sweep = asyncio.create_task(pipeline.startup_sweep(self))

    async def close(self) -> None:
        if self._sweep:
            self._sweep.cancel()
            await asyncio.gather(self._sweep, return_exceptions=True)
        await self.queue.close()
        await self.pg.close()
