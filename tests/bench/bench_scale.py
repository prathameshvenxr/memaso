"""Search latency at scale: python -m tests.bench.bench_scale [rows]   (needs the Postgres container and fastembed).

Loads `rows` facts and `rows` chunks with random 384-dimension vectors into a throwaway tenant, times real
searches (query embedding included) and the store queries alone, then removes the tenant. Vectors are random, which
is fine for latency: an exact scan costs the same whatever the data says."""
from __future__ import annotations

import asyncio
import statistics
import sys
import time

import asyncpg

from memory_core.adapters.pg_base import SERVER_SETTINGS
from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.retrieval.search import search
from memory_core.types import Scope
from tests.conftest import ADMIN, APP

TENANT, SUBJECT, DIM = "bench", "u", 384
QUERIES = ["what is the budget", "which reports do I prefer", "cpa target for the account", "creative fatigue",
           "weekly report in INR", "who joined the call", "campaign pacing", "audience size", "word42 bench", "roas"]


async def load(rows: int) -> str:
    conn = await asyncpg.connect(ADMIN, server_settings=SERVER_SETTINGS)
    model = (await _model_id())
    await conn.execute("INSERT INTO tenants(id) VALUES($1) ON CONFLICT DO NOTHING", TENANT)
    await conn.execute("INSERT INTO documents(id,tenant,subject,content,hash,status,created_at) "
                       "VALUES('bench-doc',$1,$2,'bench','bench-hash','indexed',now())", TENANT, SUBJECT)
    t = time.time()
    await conn.execute(f"""
        INSERT INTO memories(id,tenant,subject,text,kind,confidence,valid_from,extractor_version)
        SELECT 'bm'||g, $1, $2, 'word'||(g % 5000)||' bench fact '||g, 'fact', 0.9, now() - interval '1 day', 'bench'
        FROM generate_series(1,$3::int) g""", TENANT, SUBJECT, rows)
    await conn.execute(f"""
        INSERT INTO embeddings(memory_id,model_id,tenant,vector)
        SELECT 'bm'||g, $3, $1, (SELECT array_agg(random()::float4) FROM generate_series(1,{DIM}) WHERE g > 0)::vector
        FROM generate_series(1,$2::int) g""", TENANT, rows, model)
    await conn.execute(f"""
        INSERT INTO chunks(id,tenant,subject,document_id,position,text,observed_at,model_id,vector)
        SELECT 'bc'||g, $1, $2, 'bench-doc', g, 'chunk '||g||' word'||(g % 4000)||' bench', now() - interval '1 day', $4,
               (SELECT array_agg(random()::float4) FROM generate_series(1,{DIM}) WHERE g > 0)::vector
        FROM generate_series(1,$3::int) g""", TENANT, SUBJECT, rows, model)
    await conn.execute("ANALYZE memories; ANALYZE embeddings; ANALYZE chunks;")
    await conn.close()
    print(f"loaded {rows} facts + {rows} chunks in {time.time() - t:.0f}s")
    return model


async def _model_id() -> str:
    c = build(load_config(None, {"MEMASO__EMBEDDER__ADAPTER": "fastembed", "MEMASO__STORE__DSN": APP}))
    return c.embedder.model_id


async def cleanup() -> None:
    conn = await asyncpg.connect(ADMIN, server_settings=SERVER_SETTINGS)
    await conn.execute("delete from chunks where tenant=$1", TENANT)
    await conn.execute("delete from memories where tenant=$1", TENANT)
    await conn.execute("delete from documents where tenant=$1", TENANT)
    await conn.execute("delete from tenants where id=$1", TENANT)
    await conn.close()


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))]


async def main(rows: int) -> None:
    await cleanup()
    try:
        await load(rows)
        c = build(load_config(None, {"MEMASO__EMBEDDER__ADAPTER": "fastembed", "MEMASO__STORE__DSN": APP}))
        await c.start()
        scope = Scope(TENANT, SUBJECT)
        await search(c, scope, "warm up")
        total, store_only = [], []
        for _ in range(6):
            for q in QUERIES:
                t = time.perf_counter()
                await search(c, scope, q, k=10, token_budget=1500)
                total.append((time.perf_counter() - t) * 1000)
                (vec,) = await c.embedder.embed([q])
                t = time.perf_counter()
                await asyncio.gather(c.vectors.query(scope, vec, c.embedder.model_id, 30, {}, None),
                                     c.keywords.query(scope, q, 30, {}, None),
                                     c.chunks.query(scope, vec, c.embedder.model_id, 30, {}, None),
                                     c.chunk_keywords.query(scope, q, 30, {}, None))
                store_only.append((time.perf_counter() - t) * 1000)
        print(f"rows={rows} facts + {rows} chunks, 384-dim, exact scan")
        print(f"search() incl. query embedding : p50 {statistics.median(total):.0f} ms  p95 {pct(total, .95):.0f} ms")
        print(f"four store legs only           : p50 {statistics.median(store_only):.0f} ms  p95 {pct(store_only, .95):.0f} ms")
        await c.close()
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000))
