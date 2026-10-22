from datetime import UTC, datetime

from memory_core.ingest import pipeline
from memory_core.ingest.truth import resolve
from memory_core.retrieval.search import search
from memory_core.types import Candidate, Kind, Scope, Verdict
from tests.fakes import ScriptedLLM

S = Scope("acme", "u")


def at(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def ingest(c, text, when):
    await pipeline.add(c, S, text, observed_at=when)
    await c.queue.join()


async def test_exact_restatement_is_duplicate_and_bumps_confidence(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    (before,) = await c.mems.list(S, 10, 0)
    await ingest(c, "budget is 50k.", at(2026, 1, 2))
    mems = await c.mems.list(S, 10, 0)
    assert len(mems) == 1 and mems[0].confidence > before.confidence
    assert len(mems[0].source_doc_ids) == 2


async def test_change_supersedes_old_and_keeps_it_in_history(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    await ingest(c, "Budget is 80k", at(2026, 2, 1))
    cur = await c.mems.list(S, 10, 0)
    hist = await c.mems.list(S, 10, 0, include_history=True)
    assert [m.text for m in cur] == ["Budget is 80k"] and len(hist) == 2
    new = cur[0]
    assert new.supersedes and next(m for m in hist if m.id == new.supersedes[0]).valid_to == new.valid_from


async def test_no_neighbours_means_new_without_calling_the_llm(c):
    c.llm = ScriptedLLM([])  # any call raises
    d = await resolve(Candidate("Budget is 50k", Kind.fact), (await c.embedder.embed(["Budget is 50k"]))[0], S, c)
    assert d.verdict == Verdict.new


async def test_malformed_judge_answer_defaults_to_new_and_is_audited(c, admin):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    c.llm = ScriptedLLM([{"verdict": "maybe"}])
    (v,) = await c.embedder.embed(["Budget is 80k"])
    d = await resolve(Candidate("Budget is 80k", Kind.fact), v, S, c)
    assert d.verdict == Verdict.new  # never silently supersede
    assert await admin.fetchval("SELECT count(*) FROM audit_log WHERE action='judge_failed'") == 1


async def test_judge_naming_an_unknown_target_is_ignored(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    c.llm = ScriptedLLM([{"verdict": "updates", "target_id": "does-not-exist", "reason": "x"}])
    (v,) = await c.embedder.embed(["Budget is 80k"])
    assert (await resolve(Candidate("Budget is 80k", Kind.fact), v, S, c)).verdict == Verdict.new


async def test_out_of_order_ingestion_keeps_the_newer_fact_current(c):
    await ingest(c, "Budget is 80k", at(2026, 3, 1))
    await ingest(c, "Budget is 50k", at(2026, 1, 1))  # older observation arrives late
    assert [m.text for m in await c.mems.list(S, 10, 0)] == ["Budget is 80k"]
    assert "50k" in (await search(c, S, "budget", as_of=at(2026, 2, 1))).context


async def test_superseded_memory_not_in_default_list(c):
    await ingest(c, "Budget is 50k", at(2026, 1, 1))
    await ingest(c, "Budget is 80k", at(2026, 2, 1))
    assert all(m.valid_to is None for m in await c.mems.list(S, 10, 0))
