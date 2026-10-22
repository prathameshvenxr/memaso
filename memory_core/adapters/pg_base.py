from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg

from ..migrate import SERVER_SETTINGS  # noqa: E402  (re-exported: the one definition lives next to the migrations)


class Pg:
    """One pool shared by every Postgres adapter. Each transaction is pinned to a tenant for RLS."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        if self.pool is None:
            self.pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10, server_settings=SERVER_SETTINGS)
            async with self.pool.acquire() as conn:
                bad = await conn.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            if bad:
                await self.pool.close()
                self.pool = None
                raise RuntimeError("store.dsn connects as a superuser/BYPASSRLS role, which would silently disable "
                                   "tenant isolation; connect as the unprivileged app role")

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()
            self.pool = None

    @asynccontextmanager
    async def tx(self, tenant: str):
        assert self.pool, "Pg.start() not called"
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.tenant', $1, true)", tenant)
                yield conn

    @asynccontextmanager
    async def subject_lock(self, tenant: str, subject: str):
        """Cross-process mutex for one subject's ingestion, so concurrent documents cannot miss each other.
        ponytail: a fresh connection per held lock (not pooled, so workers can never starve the pool)."""
        conn = await asyncpg.connect(self.dsn, server_settings=SERVER_SETTINGS)
        try:
            await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", f"{tenant}\x1f{subject}")
            yield
        finally:
            await conn.close()  # closing the session releases the advisory lock

    @asynccontextmanager
    async def raw(self):
        """No tenant context: only for api_keys/tenants lookups, which are not RLS tables."""
        assert self.pool, "Pg.start() not called"
        async with self.pool.acquire() as conn:
            yield conn


def vec(v: list[float]) -> str:
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def redaction_pattern(text: str) -> str:
    """A Postgres ARE that matches `text` ignoring case and whitespace differences, with punctuation escaped."""
    import re

    esc = lambda tok: re.sub(r"([\x21-\x2f\x3a-\x40\x5b-\x60\x7b-\x7e])", r"\\\1", tok)  # ASCII punctuation only
    # boundaries: "CPA is 42" must not match inside "CPA is 420" or "CPA is 4.5"
    return r"(?<!\w)" + r"\s+".join(esc(t) for t in text.split()) + r"(?!\w|[.,][0-9])"
