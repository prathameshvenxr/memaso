from __future__ import annotations

import json
from datetime import UTC, date, datetime

from ..types import Principal
from .pg_base import Pg


class PgAux:
    """Audit log, deletion receipts, dead letters, usage metering and api-key lookup."""

    def __init__(self, pg: Pg):
        self.pg = pg

    async def audit(self, tenant: str, actor: str, action: str, target: str | None = None,
                    detail: dict | None = None) -> None:
        async with self.pg.tx(tenant) as c:
            await c.execute("INSERT INTO audit_log(tenant,actor,action,target,detail) VALUES($1,$2,$3,$4,$5::jsonb)",
                            tenant, actor, action, target, json.dumps(detail or {}))

    async def receipt(self, tenant: str, id: str, requested: dict, counts: dict) -> datetime:
        async with self.pg.tx(tenant) as c:
            return await c.fetchval(
                "INSERT INTO deletion_receipts(id,tenant,requested,counts) VALUES($1,$2,$3::jsonb,$4::jsonb) "
                "RETURNING at", id, tenant, json.dumps(requested), json.dumps(counts))

    async def get_receipt(self, tenant: str, id: str) -> dict | None:
        async with self.pg.tx(tenant) as c:
            r = await c.fetchrow("SELECT * FROM deletion_receipts WHERE tenant=$1 AND id=$2", tenant, id)
        return None if r is None else {"id": r["id"], "requested": json.loads(r["requested"]),
                                       "counts": json.loads(r["counts"]), "at": r["at"]}

    async def dead_letter(self, tenant: str, job: dict, error: str) -> None:
        async with self.pg.tx(tenant) as c:
            await c.execute("INSERT INTO jobs_dead_letter(tenant,job,error) VALUES($1,$2::jsonb,$3)",
                            tenant, json.dumps(job), error)

    async def add_usage(self, tenant: str, tokens: int) -> None:
        async with self.pg.tx(tenant) as c:
            await c.execute("INSERT INTO usage(tenant,day,tokens) VALUES($1,$2,$3) "
                            "ON CONFLICT (tenant,day) DO UPDATE SET tokens = usage.tokens + EXCLUDED.tokens",
                            tenant, datetime.now(UTC).date(), tokens)

    async def month_usage(self, tenant: str) -> int:
        first = date.today().replace(day=1)
        async with self.pg.tx(tenant) as c:
            return int(await c.fetchval("SELECT COALESCE(SUM(tokens),0) FROM usage WHERE tenant=$1 AND day>=$2",
                                        tenant, first))

    async def budget(self, tenant: str) -> int | None:
        async with self.pg.raw() as c:
            return await c.fetchval("SELECT monthly_budget FROM tenants WHERE id=$1", tenant)

    async def tenants(self) -> list[str]:
        async with self.pg.raw() as c:
            return [r["id"] for r in await c.fetch("SELECT id FROM tenants")]

    async def principal_for(self, key_hash: str) -> Principal | None:
        async with self.pg.raw() as c:
            r = await c.fetchrow("SELECT tenant, subject FROM api_keys WHERE key_hash=$1", key_hash)
        return None if r is None else Principal(tenant=r["tenant"], subject=r["subject"])
