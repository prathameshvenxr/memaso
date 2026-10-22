from __future__ import annotations

import uuid

from contextlib import asynccontextmanager

from ..types import DeletionReceipt


@asynccontextmanager
async def _nolock():
    yield


async def hard_delete(c, tenant: str, *, memory_id: str | None = None, doc_id: str | None = None,
                      subject: str | None = None, everything: bool = False, actor: str = "api") -> DeletionReceipt:
    """Real deletion: rows, embeddings and links go in one cascade. Exactly one selector is required."""
    chosen = [x for x in (memory_id, doc_id, subject) if x]
    if len(chosen) + (1 if everything else 0) != 1:
        raise ValueError("choose exactly one of memory_id, doc_id, subject, everything")
    counts = {"memories": 0, "documents": 0}
    if memory_id:
        m = await c.mems.get(tenant, memory_id)
        counts["memories"] = await c.mems.delete_where(tenant, memory_id=memory_id)
        # the raw source text of a forgotten memory must not outlive it
        counts["documents"] = await c.docs.delete_orphans(tenant, m.source_doc_ids if m else [])
        if m is not None:  # a source document that survives (it backs other facts) must not keep this fact's words
            await c.docs.redact(tenant, m.source_doc_ids, m.text)
            if c.chunks is not None:
                await c.chunks.redact(tenant, m.source_doc_ids, m.text)
    elif doc_id:
        d = await c.docs.get(tenant, doc_id)
        async with (c.lock(tenant, d.scope.subject) if d else _nolock()):  # no ingest between the two steps
            counts["memories"] = await c.mems.delete_where(tenant, doc_id=doc_id)
            counts["documents"] = await c.docs.delete_where(tenant, doc_id=doc_id)
    elif subject:
        # documents first: an ingest in flight for these documents then fails its own insert (foreign key)
        # instead of leaving a memory behind after the receipt says the subject is gone
        counts["documents"] = await c.docs.delete_where(tenant, subject=subject)
        counts["memories"] = await c.mems.delete_where(tenant, subject=subject)
    else:
        counts["documents"] = await c.docs.delete_where(tenant)
        counts["memories"] = await c.mems.delete_where(tenant)
    requested = {k: v for k, v in (("memory_id", memory_id), ("doc_id", doc_id), ("subject", subject),
                                   ("everything", everything or None)) if v}
    rid = str(uuid.uuid4())
    at = await c.aux.receipt(tenant, rid, requested, counts)
    await c.aux.audit(tenant, actor, "delete", rid, {"requested": requested, "counts": counts})
    return DeletionReceipt(id=rid, counts=counts, at=at)
