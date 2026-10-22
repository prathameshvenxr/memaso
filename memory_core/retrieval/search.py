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


def redact_spans(text: str, spans: list[str]) -> str | None:
    """Replace each superseded fact's words with [superseded]; None when nothing meaningful is left."""
    for span in spans:
        pattern = r"(?<!\w)" + r"\s+".join(re.escape(tok) for tok in span.split()) + r"(?!\w|[.,]\d)"
        text = re.sub(pattern, "[superseded]", text, flags=re.I) if pattern else text
    rest = re.sub(r"\[superseded\]|user:|assistant:|\W+", "", text, flags=re.I)
    return text if len(rest) >= 8 else None


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
    rankings = [[f"m:{i}" for i, sc in got[0] if sc >= floor], [f"m:{i}" for i, _ in got[1]]]
    weights = [1.0, 1.0]
    if chunk_ok:  # verbatim passages rank a little below the facts distilled from them
        rankings += [[f"c:{i}" for i, sc in got[2] if sc >= floor], [f"c:{i}" for i, _ in got[3]]]
        weights += [0.8, 0.8]
    fused = rrf(rankings, weights=weights)[:k]
    mems = {m.id: m for m in await c.mems.get_many(scope.tenant, [i[2:] for i, _ in fused if i[0] == "m"])}
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
