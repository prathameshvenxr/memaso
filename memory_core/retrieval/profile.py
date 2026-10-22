from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..tokens import count_tokens
from ..types import Hit, Kind, Scope, SearchResult
from .pack import pack
from .salience import salience

STABLE_KINDS = (Kind.fact, Kind.preference)


async def profile(c, scope: Scope, token_budget: int = 800) -> SearchResult:
    """Static = settled facts and preferences; dynamic = the latest other memories. Cheap to inject every turn."""
    current = await c.mems.list(scope, limit=1000, offset=0)
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=7)
    static_ids = {m.id for m in current
                  if m.kind in STABLE_KINDS and m.confidence >= 0.7 - 1e-6 and m.valid_from <= cutoff}
    half_life = float(c.cfg.consolidation.get("half_life_days", 90))
    # the token budget is the cap, so what is kept is what matters most (importance, recency, use), not what is newest
    hits = sorted((Hit(m, salience(m, now, half_life)) for m in current), key=lambda h: -h.score)
    context, kept, tokens = pack(hits, token_budget, count_tokens,
                                 section_of=lambda h: "static" if h.memory.id in static_ids else "dynamic")
    return SearchResult(context=context, results=kept, tokens=tokens)
