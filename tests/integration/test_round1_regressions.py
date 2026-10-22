"""Regressions found by the round-1 review. Each fails on the round-0 code."""
import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from memory_core.errors import LLMSchemaError
from memory_core.ingest import pipeline
from memory_core.ingest.truth import resolve
from memory_core.manage.crud import edit_memory, list_memories
from memory_core.manage.delete import hard_delete
from memory_core.retrieval.profile import profile
from memory_core.retrieval.search import search
from memory_core.types import Candidate, DocStatus, Kind, Scope, Verdict
from tests.fakes import AlwaysBadLLM, ScriptedLLM

S = Scope("acme", "u")


def at(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=UTC)


async def ingest(c, text, when, scope=S, **kw):
    doc = await pipeline.add(c, scope, text, observed_at=when, **kw)
    await c.queue.join()
    return doc


async def test_same_day_correction_with_date_only_hint_supersedes(c):
    await ingest(c, "Budget is 50k", at(2026, 10, 20, 10))
    c.llm = ScriptedLLM([{"memories": [{"text": "Budget is 80k", "kind": "fact", "valid_from": "2026-10-20"}]},
                         {"verdict": "updates", "target_id": None, "reason": "x"}])
    (old,) = await c.mems.list(S, 5, 0)
    c.llm.script[1]["target_id"] = old.id
    await ingest(c, "Budget is 80k", at(2026, 10, 20, 11))
    assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Budget is 80k"]


async def test_judge_saying_new_never_supersedes_even_if_it_names_a_target(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    (old,) = await c.mems.list(S, 5, 0)
    c.llm = ScriptedLLM([{"verdict": "new", "target_id": old.id, "reason": "echo"}])
    (v,) = await c.embedder.embed(["Budget is 80k"])
    d = await resolve(Candidate("Budget is 80k", Kind.fact), v, S, c)
    assert d.verdict == Verdict.new and d.ids == []


async def test_judge_outage_is_retried_not_swallowed(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    c.llm = ScriptedLLM([RuntimeError("429")])
    (v,) = await c.embedder.embed(["Budget is 80k"])
    with pytest.raises(RuntimeError):
        await resolve(Candidate("Budget is 80k", Kind.fact), v, S, c)


async def test_future_dated_fact_is_not_current_until_its_date(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    nxt = datetime.now(UTC) + timedelta(days=30)
    c.llm = ScriptedLLM([{"memories": [{"text": "Budget is 80k", "kind": "fact",
                                        "valid_from": nxt.isoformat()}]}])
    (old,) = await c.mems.list(S, 5, 0)
    c.llm.script.append({"verdict": "updates", "target_id": old.id, "reason": "scheduled change"})
    await ingest(c, "From next month budget is 80k", datetime.now(UTC))
    assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Budget is 50k"]
    now = await search(c, S, "budget")
    assert [h.memory.text for h in now.results] == ["Budget is 50k"]  # the fact layer: 80k is not current yet
    assert all("from next month" in k.chunk.text.lower() for k in now.chunks if "80k" in k.chunk.text)  # verbatim keeps its own words
    assert "80k" in (await search(c, S, "budget", as_of=nxt + timedelta(days=1))).context


async def test_forget_by_memory_id_removes_the_source_text_and_allows_readding(c, admin):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    (m,) = await c.mems.list(S, 5, 0)
    r = await hard_delete(c, "acme", memory_id=m.id)
    assert r.counts == {"memories": 1, "documents": 1}
    assert await admin.fetchval("SELECT count(*) FROM documents") == 0
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    assert len(await c.mems.list(S, 5, 0)) == 1


async def test_failed_document_is_reprocessed_when_added_again(c):
    good = c.llm
    c.llm = AlwaysBadLLM()
    d = await pipeline.add(c, S, "Budget is 50k")
    await c.queue.join()
    assert (await c.docs.get("acme", d.id)).status == DocStatus.failed
    c.llm = good
    await pipeline.add(c, S, "Budget is 50k")
    await c.queue.join()
    assert (await c.docs.get("acme", d.id)).status == DocStatus.indexed
    assert len(await c.mems.list(S, 5, 0)) == 1


async def test_pending_documents_are_resumed_on_startup(c):
    from memory_core.migrate import create_tenant
    from tests.conftest import ADMIN
    await create_tenant(ADMIN, "acme")
    doc = await c.docs.put(__import__("memory_core.types", fromlist=["Document"]).Document(
        id="d-pending", scope=S, content="Budget is 50k", hash="h-pending", status=DocStatus.queued,
        created_at=at(2026, 1, 1)))
    from memory_core.ingest.pipeline import resume_pending
    await resume_pending(c)
    await c.queue.join()
    assert (await c.docs.get("acme", doc.id)).status == DocStatus.indexed


async def test_idempotency_key_is_scoped_to_the_subject(c):
    a = await pipeline.add(c, Scope("acme", "alice"), "Budget is 50k", idempotency_key="msg-1")
    b = await pipeline.add(c, Scope("acme", "bob"), "Spend is 9k", idempotency_key="msg-1")
    await c.queue.join()
    assert a.id != b.id and b.scope.subject == "bob"
    assert [m.text for m in await c.mems.list(Scope("acme", "bob"), 5, 0)] == ["Spend is 9k"]


async def test_structured_events_are_accepted(c):
    doc = await pipeline.add(c, S, {"event": "budget_changed", "value": 80000})
    assert '"event": "budget_changed"' in doc.content
    with pytest.raises(ValueError):
        await pipeline.add(c, S, {})


async def test_long_documents_are_chunked_and_every_part_is_extracted(c):
    text = ". ".join(f"Metric{i} is value{i} for campaign{i}" for i in range(400))
    assert len(text) > 10_000
    await ingest(c, text, at(2026, 1, 1))
    assert len(await c.mems.list(S, 500, 0)) == 400


async def test_time_range_filter_accepts_iso_strings_and_rejects_garbage(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    ok = await search(c, S, "budget", filters={"created_after": "2020-01-01"})
    assert ok.results
    assert not (await search(c, S, "budget", filters={"created_after": "2099-01-01T00:00:00Z"})).results
    for bad in ({"created_after": "yesterday-ish"}, {"entity": 5}, {"kind": "gossip"}, {"min_confidence": "high"}):
        with pytest.raises(ValueError):
            await search(c, S, "budget", filters=bad)


async def test_natural_language_question_matches_through_the_keyword_leg(c, admin):
    await ingest(c, "Diwali budget is 50k", at(2026, 1, 1))
    c.cfg.retrieval["min_similarity"] = 0.99  # switch the vector leg off: only keywords can answer
    r = await search(c, S, "what is the budget for Diwali?")
    assert "50k" in r.context


async def test_edit_is_atomic_and_concurrent_edits_leave_one_current_version(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    (old,) = await c.mems.list(S, 5, 0)
    res = await asyncio.gather(*[edit_memory(c, "acme", old.id, f"Budget is {n}k") for n in (60, 70, 80)])
    assert sum(r is not None for r in res) == 1
    assert len(await c.mems.list(S, 10, 0)) == 1


async def test_profile_static_section_uses_when_the_fact_became_true(c):
    await ingest(c, "Budget is 50k", at(2025, 1, 1))
    await ingest(c, "Reports go out Mondays", datetime.now(UTC))
    p = await profile(c, S)
    assert 'section="static"' in p.context and 'section="dynamic"' in p.context


async def test_profile_is_not_capped_at_ten_dynamic_facts(c):
    await ingest(c, ". ".join(f"Metric{i} is value{i}" for i in range(25)), datetime.now(UTC))
    assert len((await profile(c, S, token_budget=5000)).results) == 25
