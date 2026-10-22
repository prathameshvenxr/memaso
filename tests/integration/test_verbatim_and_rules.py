"""Layer A (verbatim chunks) and the model-free paths: rules extraction, rules judge, remember, library facade."""
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.manage.delete import hard_delete
from memory_core.memory import Memory
from memory_core.retrieval.search import search
from memory_core.types import Scope
from tests.conftest import FAKE_ENV
from tests.eval.run_eval import run_eval

S = Scope("acme", "u")
FREE = dict(FAKE_ENV, MEMASO__LLM__ADAPTER="none", MEMASO__FACTS__EXTRACT="rules", MEMASO__TRUTH__JUDGE="auto")


def at(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


@pytest_asyncio.fixture
async def free(admin):
    c = build(load_config(None, FREE))
    await c.start()
    yield c
    await c.close()


async def ingest(c, text, when):
    await pipeline.add(c, S, text, observed_at=when)
    await c.queue.join()


async def test_no_model_configured_still_stores_verbatim_and_derives_facts(free, admin):
    assert free.llm is None
    await ingest(free, "Budget is 50k. Reports go to Priya.", at(2026, 1, 1))
    assert await admin.fetchval("SELECT count(*) FROM chunks") == 1
    assert [m.text for m in await free.mems.list(S, 10, 0)] == ["Budget is 50k"]  # only the plain statement


async def test_verbatim_text_with_no_extractable_fact_is_still_recallable(free):
    await ingest(free, "Spoke with Priya about the Diwali creative refresh and the carousel test", at(2026, 1, 1))
    assert await free.mems.list(S, 10, 0) == []
    r = await search(free, S, "what did Priya say about the creative refresh")
    assert r.results == [] and len(r.chunks) == 1 and "carousel" in r.context


async def test_rules_judge_supersedes_and_the_stale_chunk_becomes_history(free):
    await ingest(free, "Budget is 50k", at(2026, 1, 1))
    await ingest(free, "Budget is 80k", at(2026, 2, 1))
    assert [m.text for m in await free.mems.list(S, 5, 0)] == ["Budget is 80k"]
    now = await search(free, S, "budget")
    assert "80k" in now.context and "50k" not in now.context            # the old chunk no longer reads as current
    hist = await search(free, S, "budget", include_history=True)
    assert "50k" in hist.context and "80k" in hist.context
    then = await search(free, S, "budget", as_of=at(2026, 1, 15))
    assert "50k" in then.context and "80k" not in then.context


async def test_rules_keep_different_attributes_separate_and_read_preferences(free):
    await ingest(free, "The CPA target is 420. The ROAS target is 4. The client prefers weekly report", at(2026, 1, 1))
    assert {m.text for m in await free.mems.list(S, 10, 0)} == {
        "The CPA target is 420", "The ROAS target is 4", "The client prefers weekly report"}
    await ingest(free, "The client prefers monthly report", at(2026, 2, 1))
    cur = {m.text for m in await free.mems.list(S, 10, 0)}  # two preferences: without a model they are not merged
    assert {"The client prefers monthly report", "The client prefers weekly report"} <= cur


async def test_rules_ignore_assistant_lines_questions_and_hedges(free):
    await ingest(free, "user: What is the budget?\nassistant: The budget is 90k.\nuser: maybe the cap is 5k\n"
                       "user: The cap is 7k", at(2026, 1, 1))
    assert [m.text for m in await free.mems.list(S, 10, 0)] == ["The cap is 7k"]


async def test_model_is_called_only_for_ambiguous_cases():
    calls = []

    async def host(messages, schema):
        calls.append(schema["title"])
        return {"verdict": "new", "target_id": None, "reason": "unrelated"}

    c = build(load_config(None, FREE), llm=host)
    await c.start()
    try:
        await ingest(c, "The budget is 50k", at(2026, 1, 1))
        await ingest(c, "The budget is 80k", at(2026, 2, 1))   # same attribute: the rules decide, no model call
        assert calls == []
        # free text that is close to a stored fact cannot be read by the rules: now the model is asked, once
        await pipeline.remember(c, S, "budget 80k confirmed", observed_at=at(2026, 3, 1))
        assert calls == ["judge"]
    finally:
        await c.close()


async def test_remember_writes_a_fact_with_no_model_call_and_supersedes(free):
    first = await free.mems.get("acme", (await _remember(free, "CPA target is 420", at(2026, 1, 1))).id)
    second = await _remember(free, "CPA target is 380", at(2026, 2, 1))
    assert second.supersedes == [first.id]
    assert [m.text for m in await free.mems.list(S, 5, 0)] == ["CPA target is 380"]
    with pytest.raises(ValueError):
        await pipeline.remember(free, S, "  ")


async def _remember(c, text, when):
    return await pipeline.remember(c, S, text, observed_at=when)


async def test_hard_delete_by_subject_removes_chunks_and_by_memory_removes_the_source_text(free, admin):
    await ingest(free, "Budget is 50k", at(2026, 1, 1))
    (m,) = await free.mems.list(S, 5, 0)
    await hard_delete(free, "acme", memory_id=m.id)
    assert await admin.fetchval("SELECT count(*) FROM chunks") == 0
    await ingest(free, "Budget is 60k", at(2026, 1, 2))
    await hard_delete(free, "acme", subject="u")
    for t in ("chunks", "memory_chunks", "documents", "memories"):
        assert await admin.fetchval(f"SELECT count(*) FROM {t}") == 0, t


async def test_chunks_are_tenant_isolated(free):
    await ingest(free, "Budget is 50k", at(2026, 1, 1))
    other = Scope("globex", "u")
    assert (await search(free, other, "budget")).chunks == []
    (hit,) = (await search(free, S, "budget")).chunks
    assert await free.chunks.get_many("globex", [hit.chunk.id]) == []


async def test_facade_uses_the_hosts_model_and_respects_the_budget():
    calls = []

    async def host(messages, schema):
        calls.append(1)
        return {"memories": []}

    async with await Memory.create(load_config(None, dict(FREE, MEMASO__FACTS__EXTRACT="llm")), llm=host) as mem:
        await mem.ingest_turn(S, [{"role": "user", "content": "The cap is 7k"}])
        await mem.archive(S, [{"role": "user", "content": "old detail about creatives"}])
        await mem.drain()
        assert len(calls) == 1   # the app's own model, once for the turn; archived turns cost no model call
        await mem.remember(S, "Reports go out Mondays")
        ctx = await mem.context(S, "reports", token_budget=300)
        assert "Mondays" in ctx
        from memory_core.tokens import count_tokens
        assert count_tokens(await mem.context(S, "creatives", token_budget=60)) <= 60


async def test_facts_mode_llm_without_a_model_is_refused_at_build():
    with pytest.raises(ValueError, match="no model"):
        build(load_config(None, dict(FREE, MEMASO__FACTS__EXTRACT="llm")))


async def test_eval_targets_hold_with_no_model_at_all(admin):
    c = build(load_config(None, dict(FREE, MEMASO__EMBEDDER__DIM="256", MEMASO__QUEUE__WORKERS="4")))
    await c.start()
    try:
        r = await run_eval(c, "tests/eval/dataset.jsonl")
    finally:
        await c.close()
    assert r["recall_at_5"] >= 0.85 and r["correct_after_update"] >= 0.90, r
    assert r["abstention"] >= 0.80 and r["stale_leak"] == 0, r
