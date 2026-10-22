from __future__ import annotations

import asyncio
import dataclasses
import re
from datetime import UTC, datetime

from ..metering import current_tenant
from ..tokens import count_tokens
from ..types import ChunkHit, Hit, Scope, SearchResult
from .fuse import rrf
from .pack import pack
from .salience import boost, salience

_STOP = {"the", "and", "for", "what", "which", "who", "how", "does", "did", "are", "was", "were", "with", "that",
         "this", "about", "from", "have", "has", "should", "would", "could", "you", "your", "our", "tell", "when"}


def redact_spans(text: str, spans: list[str]) -> str | None:
    """Replace each superseded fact's words with [superseded]; None when nothing meaningful is left."""
    for span in spans:
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(tok) for tok in span.split()) + r"(?!\w|[.,]\d)"
        text = re.sub(pattern, "[superseded]", text, flags=re.I) if pattern else text
    rest = re.sub(r"\[superseded\]|user:|assistant:|\W+", "", text, flags=re.I)
    return text if len(rest) >= 8 else None


async def _graph_legs(c, scope: Scope, query: str, wide: int, f: dict, as_of: datetime | None) -> list[list[str]]:
    """Entity graph, no extra store: facts that name an entity the question names (hop 1), then facts that share an
    entity with those (hop 2). Both are GIN-indexed lookups, and a question that names no known entity costs one."""
    hops = int(c.cfg.retrieval.get("graph_hops", 2))
    if hops < 1:
        return []
    toks = re.findall(r"\w+", query.lower())[:30]
    words = [w for w in dict.fromkeys(toks) if len(w) > 2 and w not in _STOP][:20]
    grams = [" ".join(toks[i:i + n]) for n in (2, 3) for i in range(len(toks) - n + 1)][:20]  # "acme corp"
    first = await c.mems.by_entities(scope, words + grams, wide, f, as_of)
    legs = [[f"m:{i}" for i, _ in first]]
    if hops >= 2 and first:
        seeds = await c.mems.get_many(scope.tenant, [i for i, _ in first[:5]])
        nxt = list(dict.fromkeys(e.lower() for m in seeds for e in m.entities if e.lower() not in words))[:8]
        seen = {i for i, _ in first}
        legs.append([f"m:{i}" for i, _ in await c.mems.by_entities(scope, nxt, wide, f, as_of) if i not in seen])
    return legs


async def search(c, scope: Scope, query: str, k: int = 10, token_budget: int = 1500, filters: dict | None = None,
                 as_of: datetime | None = None, include_history: bool = False) -> SearchResult:
    if not query or not query.strip():
        raise ValueError("query is empty")
    if k < 1:
        raise ValueError("k must be >= 1")
    if as_of is not None and as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)  # a naive timestamp means UTC
    f = dict(filters or {})
    f.pop("container_is", None)  # internal-only: callers cannot pass it
    f["include_history"] = include_history
    token = current_tenant.set(scope.tenant)
    try:
        (vec,) = await c.embedder.embed([query])
    finally:
        current_tenant.reset(token)
    wide = k * 3
    floor = float(c.cfg.retrieval["min_similarity"])  # below this a vector match is noise: better to abstain
    chunk_ok = c.chunks is not None and not ({"kind", "entity", "min_confidence"} & {x for x in f if f[x]})
    legs = [c.vectors.query(scope, vec, c.embedder.model_id, wide, f, as_of),
            c.keywords.query(scope, query, wide, f, as_of)]
    if chunk_ok:
        legs += [c.chunks.query(scope, vec, c.embedder.model_id, wide, f, as_of),
                 c.chunk_keywords.query(scope, query, wide, f, as_of)]
    got = await asyncio.gather(*legs)
    graph = await _graph_legs(c, scope, query, wide, f, as_of)
    rankings = [[f"m:{i}" for i, sc in got[0] if sc >= floor], [f"m:{i}" for i, _ in got[1]]]
    weights = [1.0, 1.0]
    if chunk_ok:  # verbatim passages rank a little below the facts distilled from them
        rankings += [[f"c:{i}" for i, sc in got[2] if sc >= floor], [f"c:{i}" for i, _ in got[3]]]
        weights += [0.8, 0.8]
    rankings += graph
    weights += [0.6, 0.35][:len(graph)]  # entity-linked facts rank below direct matches
    fused = rrf(rankings, weights=weights)[:k]
    mems = {m.id: m for m in await c.mems.get_many(scope.tenant, [i[2:] for i, _ in fused if i[0] == "m"])}
    now = datetime.now(UTC)  # salience nudges the order of the facts; relevance still decides which ones are here
    half_life = float(c.cfg.consolidation.get("half_life_days", 90))
    fused.sort(key=lambda x: -(x[1] * boost(salience(mems[x[0][2:]], now, half_life))
                               if x[0][0] == "m" and x[0][2:] in mems else x[1]))
    chunks = {x.id: x for x in await c.chunks.get_many(scope.tenant, [i[2:] for i, _ in fused if i[0] == "c"])} \
        if chunk_ok else {}
    if chunks and not include_history:  # redact superseded values inside chunks, keep what is still true
        spans = await c.chunks.retired_spans(scope.tenant, list(chunks), as_of)
        for cid, texts in spans.items():
            redacted = redact_spans(chunks[cid].text, texts)
            if redacted is None:
                del chunks[cid]
            else:
                chunks[cid] = dataclasses.replace(chunks[cid], text=redacted)
    items = [Hit(mems[i[2:]], sc) if i[0] == "m" else ChunkHit(chunks[i[2:]], sc)
             for i, sc in fused if (i[2:] in mems if i[0] == "m" else i[2:] in chunks)]
    context, kept, tokens = pack(items, token_budget, count_tokens)
    await c.mems.touch(scope.tenant, [h.memory.id for h in kept if isinstance(h, Hit)])
    return SearchResult(context=context, results=[h for h in kept if isinstance(h, Hit)], tokens=tokens,
                        chunks=[h for h in kept if isinstance(h, ChunkHit)])
