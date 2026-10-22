from __future__ import annotations

import asyncpg

from memory_core.adapters.pg_base import SERVER_SETTINGS
import httpx
import pytest
import pytest_asyncio

from memory_core.api.app import create_app
from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.migrate import create_api_key, create_tenant, reset

ADMIN = "postgresql://postgres:memaso_admin@localhost:54329/memaso"
APP = "postgresql://memaso_app:memaso_app@localhost:54329/memaso"
FAKE_ENV = {
    "MEMASO__LLM__ADAPTER": "fake",
    "MEMASO__EMBEDDER__ADAPTER": "fake",
    "MEMASO__EMBEDDER__DIM": "64",
    "MEMASO__STORE__DSN": APP,
    "MEMASO__STORE__ADMIN_DSN": ADMIN,
    "MEMASO__QUEUE__RETRY_DELAY": "0.01",
    "MEMASO__QUEUE__MAX_ATTEMPTS": "3",
    "MEMASO__FACTS__EXTRACT": "llm",   # the existing suites exercise the model-driven path; rules mode has its own
    "MEMASO__TRUTH__JUDGE": "llm",     # tests (test_rules_mode.py)
    "MEMASO__TRUTH__NEIGHBOUR_THRESHOLD": "0.5",  # hash embeddings are coarser than real ones
}


@pytest_asyncio.fixture(scope="session")
async def db():
    conn = await asyncpg.connect(ADMIN)  # drop tables an earlier layout left in public (dev database only)
    await conn.execute("DROP TABLE IF EXISTS public.memory_chunks, public.chunks, public.memory_sources, "
                       "public.memory_links, public.embeddings, public.memories, public.documents, public.audit_log, "
                       "public.deletion_receipts, public.jobs_dead_letter, public.usage, public.api_keys, "
                       "public.tenants, public.schema_migrations CASCADE")
    await conn.close()
    await reset(ADMIN)


@pytest_asyncio.fixture
async def admin(db):
    conn = await asyncpg.connect(ADMIN, server_settings=SERVER_SETTINGS)
    await conn.execute("TRUNCATE documents, memories, memory_sources, memory_links, embeddings, audit_log, "
                       "deletion_receipts, jobs_dead_letter, usage CASCADE; DELETE FROM api_keys; DELETE FROM tenants")
    yield conn
    await conn.close()


@pytest_asyncio.fixture
async def c(admin):
    c = build(load_config(None, FAKE_ENV))
    await c.start()
    yield c
    await c.close()


@pytest_asyncio.fixture
async def client_for(c):
    """Factory: (tenant, subject=None) -> httpx client authenticated with a fresh key for that tenant."""
    app = create_app(c, manage_lifecycle=False)
    clients = []

    async def make(tenant: str, subject: str | None = None, budget: int | None = None):
        await create_tenant(ADMIN, tenant, budget)
        key = await create_api_key(ADMIN, tenant, subject)
        cl = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                               headers={"Authorization": f"Bearer {key}"})
        clients.append(cl)
        return cl

    make.app = app
    yield make
    for cl in clients:
        await cl.aclose()
