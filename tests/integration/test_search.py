from datetime import UTC, datetime

import pytest

from memory_core.ingest import pipeline
from memory_core.retrieval.profile import profile
from memory_core.retrieval.search import search
from memory_core.types import Scope

S = Scope("acme", "u")


async def seed(c):
    await pipeline.add(c, Scope("acme", "u", "diwali"), "Budget is 50k. Acme prefers weekly report",
                       observed_at=datetime(2026, 1, 1, tzinfo=UTC))
    await pipeline.add(c, Scope("acme", "u", "holi"), "Spend is 9k", observed_at=datetime(2026, 1, 2, tzinfo=UTC))
    await c.queue.join()


async def test_blank_query_and_bad_k_raise(c):
    for q in ("", "   "):
        with pytest.raises(ValueError):
            await search(c, S, q)
    with pytest.raises(ValueError):
        await search(c, S, "budget", k=0)


async def test_filters_by_kind_entity_container(c):
    await seed(c)
    assert {h.memory.kind.value for h in (await search(c, S, "report", filters={"kind": "preference"})).results} == {"preference"}
    assert [h.memory.text for h in (await search(c, S, "budget", filters={"container": "holi"})).results] == []
    assert (await search(c, Scope("acme", "u", "diwali"), "budget")).results
    await pipeline.add(c, S, "Report goes to Priya")
    await c.queue.join()
    assert [h.memory.text for h in (await search(c, S, "report", filters={"entity": "Priya"})).results] == ["Report goes to Priya"]
    assert (await search(c, S, "report", filters={"entity": "Nobody"})).results == []


async def test_search_touches_returned_memories(c):
    await seed(c)
    r = await search(c, S, "budget")
    assert (await c.mems.get("acme", r.results[0].memory.id)).last_accessed is not None


async def test_unrelated_query_abstains(c):
    await seed(c)
    r = await search(c, S, "zebra giraffe")
    assert r.results == [] and r.context == ""


async def test_tiny_budget_returns_empty_context_not_an_overrun(c):
    await seed(c)
    r = await search(c, S, "budget", token_budget=5)
    assert r.context == "" and r.tokens == 0


async def test_profile_splits_static_and_dynamic(c):
    await seed(c)
    old = datetime(2026, 1, 1, tzinfo=UTC)
    await pipeline.add(c, S, "Reports go out Mondays", observed_at=old)
    await c.queue.join()
    p = await profile(c, S)
    assert p.context.startswith("Recalled memories") and "<memory" in p.context
    assert 'section="static"' in p.context   # settled facts observed long ago
    assert 'section="dynamic"' not in p.context  # every seeded fact is old enough to be settled
