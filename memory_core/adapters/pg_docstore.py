from __future__ import annotations

import json

from ..types import DocStatus, Document, Scope
from .pg_base import Pg, redaction_pattern


def _doc(r) -> Document:
    return Document(id=r["id"], scope=Scope(r["tenant"], r["subject"], r["container"]), content=r["content"],
                    hash=r["hash"], status=DocStatus(r["status"]), created_at=r["created_at"],
                    meta=json.loads(r["meta"]), error=r["error"])


class PgDocStore:
    def __init__(self, pg: Pg):
        self.pg = pg

    async def put(self, doc: Document) -> Document:
        s = doc.scope
        async with self.pg.tx(s.tenant) as c:
            row = await c.fetchrow(
                "INSERT INTO documents(id,tenant,subject,container,content,hash,status,meta,created_at) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8::jsonb,$9) ON CONFLICT (tenant,hash) DO NOTHING RETURNING *",
                doc.id, s.tenant, s.subject, s.container, doc.content, doc.hash, doc.status.value,
                json.dumps(doc.meta), doc.created_at)
            if row is None:
                row = await c.fetchrow("SELECT * FROM documents WHERE tenant=$1 AND hash=$2", s.tenant, doc.hash)
            return _doc(row)

    async def get(self, tenant: str, id: str) -> Document | None:
        async with self.pg.tx(tenant) as c:
            r = await c.fetchrow("SELECT * FROM documents WHERE tenant=$1 AND id=$2", tenant, id)
            return _doc(r) if r else None

    async def set_status(self, tenant: str, id: str, status: DocStatus, error: str | None = None) -> None:
        async with self.pg.tx(tenant) as c:
            await c.execute("UPDATE documents SET status=$3, error=$4 WHERE tenant=$1 AND id=$2",
                            tenant, id, status.value, error)

    async def delete_orphans(self, tenant: str, doc_ids: list[str]) -> int:
        if not doc_ids:
            return 0
        async with self.pg.tx(tenant) as c:
            r = await c.execute("DELETE FROM documents d WHERE d.tenant=$1 AND d.id = ANY($2) "
                                "AND NOT EXISTS (SELECT 1 FROM memory_sources s WHERE s.document_id = d.id)",
                                tenant, doc_ids)
            return int(r.split()[-1])

    async def pending_before(self, tenant: str, subject: str, created_at, exclude_id: str) -> list[str]:
        async with self.pg.tx(tenant) as c:
            rows = await c.fetch("SELECT id FROM documents WHERE tenant=$1 AND subject=$2 AND id <> $3 "
                                 "AND status IN ('queued','extracting') AND created_at < $4 "
                                 "ORDER BY created_at, id", tenant, subject, exclude_id, created_at)
        return [r["id"] for r in rows]

    async def redact(self, tenant: str, doc_ids: list[str], text: str) -> int:
        if not doc_ids or not text.strip():
            return 0
        async with self.pg.tx(tenant) as c:
            r = await c.execute("UPDATE documents SET content = regexp_replace(content, $3, '[removed]', 'gi') "
                                "WHERE tenant=$1 AND id = ANY($2)", tenant, doc_ids, redaction_pattern(text))
            return int(r.split()[-1])

    async def latest(self, tenant: str, subject: str) -> str | None:
        async with self.pg.tx(tenant) as c:
            return await c.fetchval("SELECT id FROM documents WHERE tenant=$1 AND subject=$2 "
                                    "ORDER BY created_at DESC, id DESC LIMIT 1", tenant, subject)

    async def pending(self, tenant: str) -> list[str]:
        async with self.pg.tx(tenant) as c:
            rows = await c.fetch("SELECT id FROM documents WHERE tenant=$1 AND status IN ('queued','extracting')",
                                 tenant)
        return [r["id"] for r in rows]

    async def delete_where(self, tenant: str, subject: str | None = None, doc_id: str | None = None) -> int:
        async with self.pg.tx(tenant) as c:
            if doc_id:
                r = await c.execute("DELETE FROM documents WHERE tenant=$1 AND id=$2", tenant, doc_id)
            elif subject:
                r = await c.execute("DELETE FROM documents WHERE tenant=$1 AND subject=$2", tenant, subject)
            else:
                r = await c.execute("DELETE FROM documents WHERE tenant=$1", tenant)
            return int(r.split()[-1])
