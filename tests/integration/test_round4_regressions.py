"""Regressions found by the round-4 review."""
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.ingest.rules import key_of
from memory_core.manage.delete import hard_delete
from memory_core.retrieval.search import search
from memory_core.types import Scope
from tests.conftest import FAKE_ENV

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


async def test_a_second_preference_about_something_else_does_not_retire_the_first(free):
    await ingest(free, "The user prefers INR", at(2026, 1, 1))
    await ingest(free, "The user prefers weekly reports", at(2026, 2, 1))
    assert {m.text for m in await free.mems.list(S, 10, 0)} == {"The user prefers INR", "The user prefers weekly reports"}
    r = await search(free, S, "INR")
    assert "INR" in r.context  # and its chunk is still current


async def test_opposite_preferences_about_the_same_thing_contradict(free):
    await ingest(free, "The client hates pie charts", at(2026, 1, 1))
    await ingest(free, "The client likes pie charts", at(2026, 2, 1))
    assert [m.text for m in await free.mems.list(S, 10, 0)] == ["The client likes pie charts"]


async def test_preference_keys_keep_the_verb_and_the_value_apart():
    assert key_of("The user prefers INR")[1] == "inr"


async def test_a_repeat_of_an_older_statement_wins_even_while_the_older_copy_is_still_pending(c):
    jobs = []

    async def hold(job):
        jobs.append(job)

    c.queue.enqueue = hold  # nothing is processed yet: everything is "pending"
    for text, when in (("Budget is 50k", at(2026, 1, 1)), ("Budget is 80k", at(2026, 2, 1)),
                       ("Budget is 50k", at(2026, 3, 1))):
        await pipeline.add(c, S, text, observed_at=when)
    assert len(jobs) == 3
    for job in reversed(jobs):  # workers run them in the worst order
        await pipeline.process(c, job)
    assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Budget is 50k"]
    cur = (await c.mems.list(S, 5, 0))[0]
    assert cur.valid_from == at(2026, 3, 1)


async def test_forgetting_a_fact_redacts_its_text_from_the_surviving_source_document(free, admin):
    await ingest(free, "Budget is 50k. The CPA target is 420.", at(2026, 1, 1))
    budget = next(m for m in await free.mems.list(S, 5, 0) if "Budget" in m.text)
    await hard_delete(free, "acme", memory_id=budget.id)
    content = await admin.fetchval("SELECT content FROM documents")
    assert "50k" not in content and "420" in content
    assert await admin.fetchval("SELECT count(*) FROM chunks WHERE text ILIKE '%50k%'") == 0


async def test_auth_and_parser_are_chosen_by_config(admin):
    from memory_core.errors import AuthError

    env = dict(FAKE_ENV, MEMASO__AUTH__ADAPTER="tests.fakes:build_auth", MEMASO__PARSER__ADAPTER="tests.fakes:build_parser")
    c = build(load_config(None, env))
    assert (await c.auth.resolve({"x-demo": "yes"})).tenant == "demo"
    with pytest.raises(AuthError):
        await c.auth.resolve({})
    assert c.parser.parse(b"x", "text/plain") == "parsed:x"


async def test_remember_route_writes_a_fact_without_any_rules_or_model(client_for, c):
    cl = await client_for("acme", subject="alice")
    first = await cl.post("/v1/remember", json={"text": "Prefers weekly reports", "kind": "preference"})
    assert first.status_code == 200 and first.json()["memory"]["text"] == "Prefers weekly reports"
    again = await cl.post("/v1/remember", json={"text": "Prefers weekly reports"})
    assert again.json() == {"memory": None, "confirmed_existing": True}
    assert [m.text for m in await c.mems.list(Scope("acme", "alice"), 5, 0)] == ["Prefers weekly reports"]


async def test_nul_and_oversize_request_fields_are_422(client_for):
    cl = await client_for("acme")
    assert (await cl.post("/v1/search", json={"query": "x", "subject": "u\u0000", "k": 1})).status_code == 422
    assert (await cl.post("/v1/memories", json={"content": "x", "subject": "u", "container": "a\u0000b"})).status_code == 422
    assert (await cl.post("/v1/remember", json={"text": "x" * 5000, "subject": "u"})).status_code == 422
