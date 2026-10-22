import json
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from memory_core.adapters.pg_base import SERVER_SETTINGS
import pytest

from memory_core.types import Kind, Memory, Scope
from tests.conftest import APP

S = Scope("t1", "u")
NOW = datetime.now(UTC)


def mem(text, scope=S, valid_from=None, valid_to=None) -> Memory:
    return Memory(id=str(uuid.uuid4()), scope=scope, text=text, kind=Kind.fact, entities=["Acme"], confidence=0.9,
                  importance=0.5, valid_from=valid_from or NOW - timedelta(days=1), valid_to=valid_to,
                  supersedes=[], source_doc_ids=[], extractor_version="v1", created_at=NOW)


async def put(c, m, text_vec=None):
    (v,) = await c.embedder.embed([text_vec or m.text])
    await c.mems.insert(m, v, c.embedder.model_id)
    return v


async def test_rls_blocks_cross_tenant_read_even_with_raw_sql(c):
    await put(c, mem("secret budget"))
    raw = await asyncpg.connect(APP, server_settings=SERVER_SETTINGS)  # the app role, but no tenant context set
    try:
        assert await raw.fetchval("SELECT count(*) FROM memories") == 0
        await raw.execute("SELECT set_config('app.tenant','other',false)")
        assert await raw.fetchval("SELECT count(*) FROM memories") == 0
        await raw.execute("SELECT set_config('app.tenant','t1',false)")
        assert await raw.fetchval("SELECT count(*) FROM memories") == 1
        with pytest.raises(asyncpg.PostgresError):  # cannot write a row for another tenant
            await raw.execute("INSERT INTO usage(tenant,day,tokens) VALUES('other', now(), 1)")
    finally:
        await raw.close()


async def test_vector_query_only_matches_same_model_id(c):
    m = mem("budget is 50k")
    v = await put(c, m)
    assert [i for i, _ in await c.vectors.query(S, v, c.embedder.model_id, 5, None, None)] == [m.id]
    assert await c.vectors.query(S, v, "some:other-model", 5, None, None) == []


async def test_insert_with_wrong_dim_raises(c):
    with pytest.raises(ValueError, match="dims"):
        await c.mems.insert(mem("x"), [0.1, 0.2], c.embedder.model_id)


async def test_keyword_query_respects_as_of(c):
    old = mem("budget is 50k", valid_from=NOW - timedelta(days=30), valid_to=NOW - timedelta(days=10))
    cur = mem("budget is 80k", valid_from=NOW - timedelta(days=10))
    await put(c, old)
    await put(c, cur)
    ids = lambda r: [i for i, _ in r]
    assert ids(await c.keywords.query(S, "budget", 5, None, NOW - timedelta(days=20))) == [old.id]
    assert ids(await c.keywords.query(S, "budget", 5, None, None)) == [cur.id]
    assert set(ids(await c.keywords.query(S, "budget", 5, {"include_history": True}, None))) == {old.id, cur.id}


async def test_filters_kind_entity_container_confidence(c):
    a = mem("budget is 50k", Scope("t1", "u", "p1"))
    b = mem("likes weekly reports", Scope("t1", "u", "p2"))
    b.kind, b.entities, b.confidence = Kind.preference, ["Bob"], 0.4
    va, vb = await put(c, a), await put(c, b)
    q = lambda f, v=va: c.vectors.query(S, v, c.embedder.model_id, 5, f, None)
    assert {i for i, _ in await q({"kind": "preference"}, vb)} == {b.id}
    assert {i for i, _ in await q({"entity": "Acme"})} == {a.id}
    assert {i for i, _ in await q({"container": "p2"}, vb)} == {b.id}
    assert {i for i, _ in await q({"min_confidence": 0.8})} == {a.id}


async def test_store_contract_roundtrip_and_tenant_wall(c):
    m = mem("hello world")
    await put(c, m)
    assert (await c.mems.get("t1", m.id)).text == "hello world"
    assert await c.mems.get("t2", m.id) is None
    assert await c.mems.delete_where("t2", memory_id=m.id) == 0
    assert await c.mems.delete_where("t1", memory_id=m.id) == 1
