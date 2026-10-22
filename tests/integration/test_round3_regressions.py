"""Regressions found by the round-3 review (real code and the audit copy)."""
from datetime import UTC, datetime

import asyncpg
import pytest

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.ingest.rules import extract_rules
from memory_core.manage.delete import hard_delete
from memory_core.memory import Memory
from memory_core.retrieval.search import search
from memory_core.types import DocStatus, Document, Kind, Memory as Mem, Scope
from tests.conftest import APP, FAKE_ENV
from tests.fakes import ScriptedLLM

S = Scope("acme", "u")
FREE = dict(FAKE_ENV, MEMASO__LLM__ADAPTER="none", MEMASO__FACTS__EXTRACT="rules", MEMASO__TRUTH__JUDGE="auto")


def at(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def ingest(c, text, when, scope=S, **kw):
    doc = await pipeline.add(c, scope, text, observed_at=when, **kw)
    await c.queue.join()
    return doc


async def free_container():
    c = build(load_config(None, FREE))
    await c.start()
    return c


def test_inproc_queue_ignores_a_redis_url_left_in_the_environment():
    c = build(load_config(None, dict(FAKE_ENV, MEMASO__QUEUE__URL="redis://redis:6379/0")))
    assert type(c.queue).__name__ == "InProcQueue"


async def test_subject_bound_key_cannot_touch_another_subjects_data_on_any_id_route(c, client_for):
    admin = await client_for("acme")
    await admin.post("/v1/memories", json={"content": "Budget is 50k", "subject": "bob"})
    await c.queue.join()
    bob = Scope("acme", "bob")
    (m,) = await c.mems.list(bob, 5, 0)
    doc_id = m.source_doc_ids[0]
    alice = await client_for("acme", subject="alice")
    assert (await alice.get(f"/v1/memories/{m.id}")).status_code == 404
    assert (await alice.patch(f"/v1/memories/{m.id}", json={"text": "Budget is 1"})).status_code == 404
    assert (await alice.delete(f"/v1/memories/{m.id}")).status_code == 404
    assert (await alice.get(f"/v1/documents/{doc_id}")).status_code == 404
    assert (await alice.post("/v1/forget", json={"document_id": doc_id})).status_code == 404
    assert (await alice.post("/v1/forget", json={"memory_id": m.id})).status_code == 404
    assert [x.text for x in await c.mems.list(bob, 5, 0)] == ["Budget is 50k"]


async def test_the_same_attribute_in_two_ad_accounts_does_not_supersede_across_them(c):
    a, b = Scope("acme", "u", "account-A"), Scope("acme", "u", "account-B")
    await ingest(c, "Budget is 50k", at(2026, 1, 1), a)
    await ingest(c, "Budget is 80k", at(2026, 2, 1), b)
    assert [m.text for m in await c.mems.list(a, 5, 0)] == ["Budget is 50k"]
    assert [m.text for m in await c.mems.list(b, 5, 0)] == ["Budget is 80k"]


async def test_archiving_old_turns_never_overwrites_a_current_fact(admin):
    c = await free_container()
    try:
        await ingest(c, "Budget is 50k", at(2026, 1, 1))
        await pipeline.remember(c, S, "Budget is 80k", observed_at=at(2026, 3, 1))
        await pipeline.add(c, S, "user: Budget is 50k", {"archived": True}, observed_at=datetime.now(UTC))
        await c.queue.join()
        assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Budget is 80k"]
        assert "50k" in (await search(c, S, "budget", include_history=True)).context  # the detail is retrievable
    finally:
        await c.close()


def test_a_multi_line_assistant_message_never_becomes_a_user_fact():
    text = "user: hi\nassistant: Here is the report\nBudget is 99k\nuser: The cap is 7k\ntool: row\nSpend is 1k"
    assert [x.text for x in extract_rules(text)] == ["The cap is 7k"]


async def test_two_contradicting_statements_in_one_document_resolve_to_the_later_one(admin):
    c = await free_container()
    try:
        await ingest(c, "Budget is 50k.\nBudget is 80k", at(2026, 1, 1))
        assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Budget is 80k"]
    finally:
        await c.close()


async def test_forgetting_the_current_fact_does_not_bring_the_old_value_back(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    await ingest(c, "Budget is 80k", at(2026, 2, 1))
    (new,) = await c.mems.list(S, 5, 0)
    await hard_delete(c, "acme", memory_id=new.id)
    assert await c.mems.list(S, 5, 0) == []
    assert "80k" not in (await search(c, S, "budget", include_history=True)).context


async def test_forgetting_one_of_two_facts_removes_its_text_from_the_shared_chunk(admin):
    c = await free_container()
    try:
        await ingest(c, "Budget is 50k. The CPA target is 420.", at(2026, 1, 1))
        budget = next(m for m in await c.mems.list(S, 5, 0) if "Budget" in m.text)
        await hard_delete(c, "acme", memory_id=budget.id)
        r = await search(c, S, "budget 50k", include_history=True)
        assert "50k" not in r.context
    finally:
        await c.close()


async def test_a_model_paraphrase_still_retires_its_chunk_when_superseded(c):
    c.llm = ScriptedLLM([{"memories": [{"text": "Monthly budget is 50,000", "kind": "fact"}]}])
    await ingest(c, "our spend plan: we are at fifty thousand", at(2026, 1, 1))
    (old,) = await c.mems.list(S, 5, 0)
    c.llm = ScriptedLLM([{"memories": [{"text": "Monthly budget is 80,000", "kind": "fact"}]},
                         {"verdict": "updates", "target_id": old.id, "reason": "paraphrase"}])
    await ingest(c, "our spend plan: we moved to eighty thousand", at(2026, 2, 1))
    ctx = (await search(c, S, "spend plan")).context
    assert "fifty thousand" not in ctx and "eighty thousand" in ctx


async def test_an_in_flight_ingest_cannot_leave_a_memory_behind_after_its_document_is_deleted(c):
    doc = await c.docs.put(Document(id="d1", scope=S, content="x", hash="h1", status=DocStatus.extracting,
                                    created_at=at(2026, 1, 1)))
    await hard_delete(c, "acme", subject="u")  # documents go first
    mem = Mem(id="m1", scope=S, text="Budget is 50k", kind=Kind.fact, entities=[], confidence=1, importance=0.5,
              valid_from=at(2026, 1, 1), valid_to=None, supersedes=[], source_doc_ids=[doc.id],
              extractor_version="t", created_at=at(2026, 1, 1))
    (vec,) = await c.embedder.embed(["Budget is 50k"])
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await c.mems.insert(mem, vec, c.embedder.model_id)
    assert await c.mems.list(S, 5, 0) == []


async def test_input_edges_are_422_not_500(c, client_for):
    cl = await client_for("acme")
    assert (await cl.get("/v1/profile", params={"subject": "u", "token_budget": 10**7})).status_code == 422
    assert (await cl.post("/v1/search", json={"query": "x", "subject": "u", "filters": {"meta": 5}})).status_code == 422
    assert (await cl.post("/v1/search", json={"query": "x", "subject": "u", "as_of": "2026-01-01T00:00:00"})).status_code == 200


def test_judge_llm_without_a_model_is_refused_at_build():
    with pytest.raises(ValueError, match="truth.judge"):
        build(load_config(None, dict(FREE, MEMASO__TRUTH__JUDGE="llm")))
