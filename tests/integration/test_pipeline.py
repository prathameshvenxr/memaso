import asyncio
from datetime import UTC, datetime

import pytest

from memory_core.ingest import pipeline
from memory_core.retrieval.search import search
from memory_core.types import DocStatus, Scope
from tests.fakes import AlwaysBadLLM

S = Scope("acme", "u")


async def test_add_returns_queued_immediately(c):
    doc = await pipeline.add(c, S, "Budget is 50k")
    assert doc.status == DocStatus.queued
    await c.queue.join()
    assert (await c.docs.get("acme", doc.id)).status == DocStatus.indexed


async def test_same_content_twice_creates_one_document(c, admin):
    a = await pipeline.add(c, S, "Budget is 50k")
    b = await pipeline.add(c, S, "  Budget   is 50k ")
    await c.queue.join()
    assert a.id == b.id and await admin.fetchval("SELECT count(*) FROM documents") == 1
    assert await admin.fetchval("SELECT count(*) FROM memories") == 1


async def test_concurrent_adds_create_one_document_and_one_memory(c, admin):
    docs = await asyncio.gather(*[pipeline.add(c, S, "Budget is 50k") for _ in range(20)])
    await c.queue.join()
    assert len({d.id for d in docs}) == 1
    assert await admin.fetchval("SELECT count(*) FROM memories") == 1


async def test_same_text_for_two_subjects_is_two_documents(c, admin):
    await pipeline.add(c, Scope("acme", "u1"), "Budget is 50k")
    await pipeline.add(c, Scope("acme", "u2"), "Budget is 50k")
    await c.queue.join()
    assert await admin.fetchval("SELECT count(*) FROM documents") == 2


@pytest.mark.parametrize("blank", ["", "   \n", [], [{"role": "user", "content": "  "}]])
async def test_blank_content_rejected(c, blank):
    with pytest.raises(ValueError):
        await pipeline.add(c, S, blank)


async def test_failed_extraction_marks_failed_stores_nothing_and_dead_letters(c, admin):
    c.llm = AlwaysBadLLM()
    doc = await pipeline.add(c, S, "Budget is 50k")
    await c.queue.join()
    d = await c.docs.get("acme", doc.id)
    assert d.status == DocStatus.failed and d.error == "ExtractionFailed"
    assert await admin.fetchval("SELECT count(*) FROM memories") == 0
    assert await admin.fetchval("SELECT count(*) FROM jobs_dead_letter") == 1
    assert AlwaysBadLLM.calls == 6  # 3 queue attempts x (1 call + 1 extraction retry)


async def test_chat_messages_are_flattened_with_roles(c):
    doc = await pipeline.add(c, S, [{"role": "user", "content": "Budget is 50k"},
                                    {"role": "assistant", "content": "Noted"}])
    assert doc.content == "user: Budget is 50k\nassistant: Noted"


async def test_redelivered_job_is_a_noop(c, admin):
    doc = await pipeline.add(c, S, "Budget is 50k")
    await c.queue.join()
    await pipeline.process(c, {"tenant": "acme", "doc_id": doc.id})
    assert await admin.fetchval("SELECT count(*) FROM memories") == 1


async def test_reindex_after_embedder_change_restores_vector_recall(c):
    await pipeline.add(c, S, "Budget is 50k", observed_at=datetime(2026, 1, 1, tzinfo=UTC))
    await c.queue.join()
    c.embedder.model_id = "fake:hash64:v2"  # simulates swapping the embedding model
    assert (await c.vectors.query(S, (await c.embedder.embed(["budget"]))[0], c.embedder.model_id, 5, None, None)) == []
    assert await pipeline.reindex(c, "acme") == 2  # the fact and its verbatim chunk
    assert (await c.vectors.query(S, (await c.embedder.embed(["budget"]))[0], c.embedder.model_id, 5, None, None))
    assert "50k" in (await search(c, S, "budget")).context


async def test_rapid_corrections_for_one_subject_never_leave_a_stale_fact(c):
    # two workers must not process a subject's documents at the same time, or each misses the other
    for round_ in range(8):
        s = Scope("acme", f"racer{round_}")
        await pipeline.add(c, s, "Budget is 50k", observed_at=datetime(2026, 1, 1, tzinfo=UTC))
        await pipeline.add(c, s, "Budget is 80k", observed_at=datetime(2026, 2, 1, tzinfo=UTC))
    await c.queue.join()
    for round_ in range(8):
        cur = await c.mems.list(Scope("acme", f"racer{round_}"), 10, 0)
        assert [m.text for m in cur] == ["Budget is 80k"], round_
