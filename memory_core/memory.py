"""Library mode: the whole engine behind one object, for embedding in an application's backend.

    mem = await Memory.create(cfg, llm=my_llm)   # llm: the app's own model call, or None for no model at all
    ctx = await mem.context(scope, user_text, 1500)   # small ranked block to put in the prompt
    await mem.ingest_turn(scope, messages)            # verbatim, no model call
    await mem.remember(scope, "CPA target is 420")    # a fact the agent already extracted
"""
from __future__ import annotations

from datetime import datetime

from .bootstrap import build
from .config import Config, load_config
from .ingest import pipeline
from .manage.consolidate import consolidate, restore
from .manage.delete import hard_delete
from .manage.export import export_scope
from .retrieval.profile import profile
from .retrieval.search import search
from .tokens import count_tokens
from .types import Scope, SearchResult


class Memory:
    def __init__(self, container):
        self.c = container

    @classmethod
    async def create(cls, cfg: Config | str | None = None, llm=None) -> "Memory":
        if not isinstance(cfg, Config):
            cfg = load_config(cfg)
        m = cls(build(cfg, llm=llm))
        await m.c.start()
        return m

    async def close(self) -> None:
        await self.c.close()

    async def __aenter__(self) -> "Memory":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def ingest_turn(self, scope: Scope, messages, metadata: dict | None = None,
                          observed_at: datetime | None = None):
        """Queue a finished turn. Stored verbatim; facts are derived by the configured mode. Returns at once."""
        return await pipeline.add(self.c, scope, messages, metadata, observed_at=observed_at)

    async def archive(self, scope: Scope, turns, observed_at: datetime | None = None):
        """Call before compacting old turns into a summary, so the detail stays retrievable."""
        return await pipeline.add(self.c, scope, turns, {"archived": True}, observed_at=observed_at)

    async def remember(self, scope: Scope, text: str, kind: str = "fact", entities: list[str] | None = None,
                       observed_at: datetime | None = None):
        return await pipeline.remember(self.c, scope, text, kind, entities, observed_at)

    async def recall(self, scope: Scope, query: str, k: int = 10, token_budget: int = 1500,
                     filters: dict | None = None, as_of: datetime | None = None,
                     include_history: bool = False) -> SearchResult:
        return await search(self.c, scope, query, k, token_budget, filters, as_of, include_history)

    async def context(self, scope: Scope, query: str, token_budget: int = 1500) -> str:
        """Profile (a third of the budget) plus query-specific recall, never over `token_budget`."""
        prof = await profile(self.c, scope, token_budget // 3)
        parts = [prof.context] if prof.context else []
        if query and query.strip():
            rec = await search(self.c, scope, query, 10, max(token_budget - prof.tokens, 1))
            if rec.context:
                parts.append(rec.context)
        text = "\n".join(parts)
        return text if count_tokens(text) <= token_budget else ""

    async def consolidate(self, scope: Scope) -> dict:
        """Merge duplicate facts and archive cold, low-value ones (kept, not deleted). Run it from a scheduler."""
        return await consolidate(self.c, scope)

    async def restore(self, tenant: str, ids: list[str]) -> int:
        """Bring archived memories back into current search, unless a current fact already states the same attribute."""
        return await restore(self.c, tenant, ids)

    async def forget(self, tenant: str, **selector):
        return await hard_delete(self.c, tenant, **selector)

    async def export(self, scope: Scope) -> dict:
        return await export_scope(self.c, scope)

    async def drain(self) -> None:
        """Wait for queued ingestion to finish (tests, graceful shutdown)."""
        await self.c.queue.join()
