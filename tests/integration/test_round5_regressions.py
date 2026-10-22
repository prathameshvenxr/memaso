"""Regressions found by the round-5 review."""
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.manage.delete import hard_delete
from memory_core.migrate import reset
from memory_core.retrieval.search import search
from memory_core.types import Scope
from tests.conftest import ADMIN, FAKE_ENV

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


async def ingest(c, text, when, scope=S):
    await pipeline.add(c, scope, text, observed_at=when)
    await c.queue.join()


async def test_tables_live_in_their_own_schema_and_coexist_with_a_v3_memories_table(admin):
    await admin.execute("DROP TABLE IF EXISTS public.memories CASCADE")
    await admin.execute("CREATE TABLE public.memories (id int)")  # what v3 already has
    try:
        await reset(ADMIN)  # must not collide with it
        assert await admin.fetchval("SELECT count(*) FROM information_schema.tables "
                                    "WHERE table_schema='memaso' AND table_name IN ('memories','chunks','documents')") == 3
        cols = await admin.fetchval("SELECT count(*) FROM information_schema.columns "
                                    "WHERE table_schema='public' AND table_name='memories'")
        assert cols == 1  # v3's table is untouched
    finally:
        await admin.execute("DROP TABLE public.memories")


async def test_a_fact_stored_without_a_container_is_visible_in_every_account_and_account_facts_are_not_shared(free):
    acct_a, acct_b = Scope("acme", "u", "acctA"), Scope("acme", "u", "acctB")
    await pipeline.remember(free, Scope("acme", "u"), "prefers weekly reports in INR", kind="preference")
    await pipeline.remember(free, acct_a, "Budget is 50k")
    assert "weekly reports" in (await search(free, acct_a, "weekly reports")).context
    assert "weekly reports" in (await search(free, acct_b, "weekly reports")).context
    assert "50k" in (await search(free, acct_a, "budget")).context
    assert "50k" not in (await search(free, acct_b, "budget")).context


async def test_a_compound_sentence_is_not_stored_as_one_wrong_fact(free):
    await ingest(free, "The budget is 80k and the CPA is 420.", at(2026, 1, 1))
    assert await free.mems.list(S, 10, 0) == []
    assert "420" in (await search(free, S, "CPA")).context  # still recallable from the verbatim layer


async def test_a_discourse_marker_does_not_stop_an_update(free):
    await ingest(free, "Budget is 50k", at(2026, 1, 1))
    await ingest(free, "Actually the budget is 80k", at(2026, 2, 1))
    assert [m.text for m in await free.mems.list(S, 10, 0)] == ["the budget is 80k"]


async def test_first_person_preferences_are_extracted_and_opposites_contradict(free):
    await ingest(free, "I hate pie charts", at(2026, 1, 1))
    await ingest(free, "I prefer weekly reports in INR.", at(2026, 1, 5))
    assert {m.text for m in await free.mems.list(S, 10, 0)} == {"I hate pie charts", "I prefer weekly reports in INR"}
    await ingest(free, "I like pie charts", at(2026, 2, 1))
    cur = {m.text for m in await free.mems.list(S, 10, 0)}
    assert "I like pie charts" in cur and "I hate pie charts" not in cur


async def test_a_superseded_fact_is_redacted_from_its_chunk_but_the_rest_of_the_chunk_stays(free):
    await ingest(free, "user: Budget is 50k. Also I want weekly reports in INR and no pie charts.", at(2026, 1, 1))
    await ingest(free, "Budget is 80k", at(2026, 2, 1))
    r = await search(free, S, "weekly reports in INR")
    text = " ".join(k.chunk.text for k in r.chunks)
    assert "weekly reports in INR" in text          # still-true content is not hidden
    assert "50k" not in text and "[superseded]" in text
    hist = await search(free, S, "weekly reports in INR", include_history=True)
    assert "50k" in " ".join(k.chunk.text for k in hist.chunks)


async def test_adding_the_same_text_again_after_forgetting_one_fact_is_processed(free):
    text = "Budget is 50k. The CPA target is 420."
    await ingest(free, text, at(2026, 1, 1))
    budget = next(m for m in await free.mems.list(S, 5, 0) if "Budget" in m.text)
    await hard_delete(free, "acme", memory_id=budget.id)
    await ingest(free, text, at(2026, 1, 2))
    assert any("Budget" in m.text for m in await free.mems.list(S, 5, 0))


async def test_nul_bytes_in_edit_entities_and_metadata_are_422(client_for, c):
    cl = await client_for("acme", subject="alice")
    made = (await cl.post("/v1/remember", json={"text": "Budget is 50k"})).json()["memory"]
    assert (await cl.patch(f"/v1/memories/{made['id']}", json={"text": "a\u0000b"})).status_code == 422
    assert (await cl.post("/v1/remember", json={"text": "x", "entities": ["a\u0000"]})).status_code == 422
    assert (await cl.post("/v1/memories", json={"content": "x", "metadata": {"k": "a\u0000"}})).status_code == 422


def test_compose_publishes_postgres_on_localhost_only():
    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    assert "127.0.0.1:54329:5432" in compose
