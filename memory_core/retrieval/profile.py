from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..tokens import count_tokens
from ..types import Hit, Kind, Scope, SearchResult
from .pack import pack

STABLE_KINDS = (Kind.fact, Kind.preference)


async def profile(c, scope: Scope, token_budget: int = 800) -> SearchResult:
    """Static = settled facts and preferences; dynamic = the latest other memories. Cheap to inject every turn."""
    current = await c.mems.list(scope, limit=1000, offset=0)
    cutoff = datetime.now(UTC) - timedelta(days=7)
    static = [m for m in current if m.kind in STABLE_KINDS and m.confidence >= 0.7 - 1e-6 and m.valid_from <= cutoff]
    static_ids = {m.id for m in static}
    dynamic = [m for m in current if m.id not in static_ids]  # newest first; the token budget is the cap
    hits = [Hit(m, 1.0) for m in static] + [Hit(m, 0.5) for m in dynamic]
    context, kept, tokens = pack(hits, token_budget, count_tokens,
                                 section_of=lambda h: "static" if h.memory.id in static_ids else "dynamic")
    return SearchResult(context=context, results=kept, tokens=tokens)
