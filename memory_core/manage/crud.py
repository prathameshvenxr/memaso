from __future__ import annotations

import uuid
from datetime import UTC, datetime

from ..metering import current_tenant
from ..types import Memory, Scope


async def get_memory(c, tenant: str, id: str) -> Memory | None:
    return await c.mems.get(tenant, id)


async def list_memories(c, scope: Scope, limit: int = 50, offset: int = 0, include_history: bool = False):
    return await c.mems.list(scope, limit, offset, include_history)


async def edit_memory(c, tenant: str, id: str, text: str, actor: str = "api") -> Memory | None:
    """An edit is a new version: the old memory is closed and linked, never overwritten. Serialised per subject,
    so concurrent edits of one memory leave exactly one current version."""
    if not text or not text.strip() or "\x00" in text:
        raise ValueError("text is empty or contains NUL bytes")
    old = await c.mems.get(tenant, id)
    if old is None:
        return None
    async with c.lock(tenant, old.scope.subject):
        old = await c.mems.get(tenant, id)  # re-read under the lock
        if old is None or old.valid_to is not None:
            return None
        token = current_tenant.set(tenant)
        try:
            (vec,) = await c.embedder.embed([text.strip()])
        finally:
            current_tenant.reset(token)
        now = datetime.now(UTC)
        new = Memory(id=str(uuid.uuid4()), scope=old.scope, text=text.strip(), kind=old.kind, entities=old.entities,
                     confidence=1.0, importance=old.importance, valid_from=now, valid_to=None, supersedes=[old.id],
                     source_doc_ids=old.source_doc_ids, extractor_version="manual", created_at=now, meta=old.meta)
        await c.mems.insert(new, vec, c.embedder.model_id, (old.id, "updates", "manual edit", now))
    await c.aux.audit(tenant, actor, "memory.edit", old.id, {"new_id": new.id})
    return new
