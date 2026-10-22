"""Per-tenant token metering: wrappers around LLM and Embedder that bill the tenant in `current_tenant`."""
from __future__ import annotations

import json
from contextvars import ContextVar

from .errors import BudgetExceeded
from .tokens import count_tokens

current_tenant: ContextVar[str | None] = ContextVar("current_tenant", default=None)


class MeteredLLM:
    def __init__(self, inner, aux):
        self.inner, self.aux = inner, aux

    async def complete(self, messages, schema):
        out = await self.inner.complete(messages, schema)
        t = current_tenant.get()
        if t:
            await self.aux.add_usage(t, sum(count_tokens(m["content"]) for m in messages)
                                     + count_tokens(json.dumps(out)))
        return out


class MeteredEmbedder:
    def __init__(self, inner, aux):
        self.inner, self.aux = inner, aux
        self.model_id, self.dim = inner.model_id, inner.dim

    async def embed(self, texts):
        out = await self.inner.embed(texts)
        t = current_tenant.get()
        if t:
            await self.aux.add_usage(t, sum(count_tokens(x) for x in texts))
        return out


async def check_budget(aux, tenant: str) -> None:
    budget = await aux.budget(tenant)
    if budget is not None and await aux.month_usage(tenant) >= budget:
        raise BudgetExceeded("monthly token budget exhausted")
