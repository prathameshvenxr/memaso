"""Regressions found by the round-2 review."""
import asyncio
from datetime import UTC, datetime

import pytest

from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.types import DocStatus, Scope
from tests.conftest import FAKE_ENV

S = Scope("acme", "u")


def at(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def test_forget_without_a_selector_is_rejected_and_deletes_nothing(c, client_for):
    bound = await client_for("acme", subject="alice")
    await bound.post("/v1/memories", json={"content": "Budget is 50k"})
    await c.queue.join()
    for body in ({}, {"memoryId": "typo"}, {"subject": None}):
        assert (await bound.post("/v1/forget", json=body)).status_code == 422
    assert len(await c.mems.list(Scope("acme", "alice"), 5, 0)) == 1


async def test_a_value_asserted_again_after_a_newer_one_becomes_current_again(c):
    for text, when in (("Budget is 50k", at(2026, 1, 1)), ("Budget is 80k", at(2026, 2, 1)),
                       ("Budget is 50k", at(2026, 3, 1))):
        await pipeline.add(c, S, text, observed_at=when)
        await c.queue.join()
    assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Budget is 50k"]


async def test_repeating_the_latest_statement_does_not_create_a_new_document(c, admin):
    await pipeline.add(c, S, "Budget is 50k", observed_at=at(2026, 1, 1))
    await c.queue.join()
    await pipeline.add(c, S, "Budget is 50k", observed_at=at(2026, 1, 2))
    await c.queue.join()
    assert await admin.fetchval("SELECT count(*) FROM documents") == 1


async def test_a_document_whose_enqueue_was_lost_is_enqueued_by_the_retry(c):
    jobs = []
    real = c.queue.enqueue

    async def flaky(job):
        jobs.append(job)
        if len(jobs) == 1:
            raise ConnectionError("redis down")
        await real(job)

    c.queue.enqueue = flaky
    with pytest.raises(ConnectionError):
        await pipeline.add(c, S, "Budget is 50k")
    doc = await pipeline.add(c, S, "Budget is 50k")  # the client's retry
    await c.queue.join()
    assert len(jobs) == 2 and (await c.docs.get("acme", doc.id)).status == DocStatus.indexed


async def test_ingest_waits_for_reindex_so_an_embedder_swap_cannot_skip_contradictions(c):
    await pipeline.add(c, S, "Budget is 50k", observed_at=at(2026, 1, 1))
    await c.queue.join()
    c.embedder.model_id = "fake:hash64:swapped"  # old memory now has no vector for the current model
    c._clean.clear()
    await pipeline.add(c, S, "Budget is 80k", observed_at=at(2026, 2, 1))
    await c.queue.join()
    assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Budget is 80k"]


def test_embedder_dim_is_never_defaulted_for_a_config_that_names_none():
    cfg = load_config(None, {"MEMASO__EMBEDDER__ADAPTER": "fake"})
    assert "dim" not in cfg.embedder


async def test_embedder_dimension_mismatch_fails_fast_at_start(admin):
    from memory_core.bootstrap import build

    c = build(load_config(None, dict(FAKE_ENV)))
    c.embedder.dim = 10  # the store was built for 64, the embedder now claims otherwise
    c.embedder.inner.dim = 64
    with pytest.raises(RuntimeError, match="dimension"):
        await c.start()
    await c.pg.close()


async def test_redis_style_worker_survives_a_connection_error():
    from memory_core.adapters.queue_inproc import InProcQueue

    q, seen = InProcQueue(workers=1, retry_delay=0.001), []

    async def on_dead(job, err):
        raise RuntimeError("on_dead exploded")

    async def h(job):
        if job["n"] == 0:
            raise ConnectionError("down")
        seen.append(job["n"])

    await q.start(h, on_dead)
    await q.enqueue({"n": 0})
    await q.enqueue({"n": 1})
    await q.join()
    await q.close()
    assert seen == [1]


async def test_create_tenant_rerun_keeps_an_existing_budget(admin):
    from memory_core.migrate import create_tenant
    from tests.conftest import ADMIN

    await create_tenant(ADMIN, "acme", 500)
    await create_tenant(ADMIN, "acme")
    assert await admin.fetchval("SELECT monthly_budget FROM tenants WHERE id='acme'") == 500


async def test_bad_filter_and_nul_bytes_are_422_not_500(c, client_for):
    cl = await client_for("acme")
    assert (await cl.post("/v1/search", json={"query": "x", "subject": "u", "filters": {"kind": 5}})).status_code == 422
    assert (await cl.post("/v1/memories", json={"content": "a\u0000b", "subject": "u"})).status_code == 422
    assert (await cl.post("/v1/search", json={"query": "x" * 5000, "subject": "u"})).status_code == 422
