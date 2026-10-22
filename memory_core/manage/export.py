from __future__ import annotations

from datetime import UTC, datetime

from ..serialize import memory_to_dict
from ..types import Scope


async def export_scope(c, scope: Scope, page: int = 500) -> dict:
    """Every memory of the subject, superseded history included."""
    out, offset = [], 0
    while True:
        batch = await c.mems.list(scope, page, offset, include_history=True)
        out += [memory_to_dict(m) for m in batch]
        if len(batch) < page:
            break
        offset += page
    return {"tenant": scope.tenant, "subject": scope.subject, "container": scope.container,
            "exported_at": datetime.now(UTC).isoformat(), "memories": out}
