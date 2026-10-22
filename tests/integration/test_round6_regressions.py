"""Regressions found by the round-6 review."""
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.ingest.rules import extract_rules, key_of
from memory_core.manage.delete import hard_delete
from memory_core.retrieval.search import redact_spans, search
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


async def ingest(c, text, when, scope=S):
    await pipeline.add(c, scope, text, observed_at=when)
    await c.queue.join()


async def test_forgetting_a_hindi_fact_works_and_removes_its_words(c, admin):
    from tests.fakes import ScriptedLLM

    c.llm = ScriptedLLM([{"memories": [{"text": "मेरा बजट 50k है", "kind": "fact"},
                                       {"text": "बाकी सब ठीक है", "kind": "fact"}]}])
    await pipeline.add(c, S, "मेरा बजट 50k है. बाकी सब ठीक है", observed_at=at(2026, 1, 1))
    await c.queue.join()
    budget = next(m for m in await c.mems.list(S, 5, 0) if "बजट" in m.text)
    receipt = await hard_delete(c, "acme", memory_id=budget.id)  # must not abort on the regular expression
    assert receipt.counts["memories"] == 1
    content = await admin.fetchval("SELECT content FROM documents")
    assert "बजट" not in content and "बाकी सब ठीक है" in content


def test_redaction_never_cuts_into_a_longer_number_or_word():
    assert redact_spans("user: ROAS is 4. Update: ROAS is 4.5 now", ["ROAS is 4"]) == "user: [superseded]. Update: ROAS is 4.5 now"
    assert redact_spans("CPA is 42. Actually CPA is 420", ["CPA is 42"]) == "[superseded]. Actually CPA is 420"
    assert redact_spans("the budget is 50k, ok", ["budget is 50k"]) == "the [superseded], ok"


async def test_forgetting_a_short_value_leaves_a_longer_one_alone(free, admin):
    await ingest(free, "CPA is 42. Reports go out Mondays.", at(2026, 1, 1))
    await ingest(free, "Actually CPA is 420", at(2026, 2, 1))
    old = next(m for m in await free.mems.list(S, 10, 0, include_history=True) if m.text == "CPA is 42")
    await hard_delete(free, "acme", memory_id=old.id)
    texts = [r["content"] for r in await admin.fetch("SELECT content FROM documents")]
    assert any("CPA is 420" in t for t in texts) and not any("[removed]0" in t for t in texts)


@pytest.mark.parametrize("old,new", [
    ("We are spending 50k a month", "We are targeting women 25-34"),
    ("I was happy", "I was in Delhi"),
    ("It is 50k", "It is expensive"),
    ("That is red", "This is 50k"),
])
async def test_generic_subjects_never_supersede_each_other(free, old, new):
    await ingest(free, old, at(2026, 1, 1))
    await ingest(free, new, at(2026, 2, 1))
    assert all(m.valid_to is None for m in await free.mems.list(S, 10, 0, include_history=True))  # nothing was closed
    assert key_of(new) is None


async def test_two_unrelated_we_statements_in_one_message_both_stay_current(free):
    await ingest(free, "We are targeting women 25-34. We are spending 50k a month.", at(2026, 1, 1))
    assert {m.text for m in await free.mems.list(S, 10, 0)} == {"We are targeting women 25-34", "We are spending 50k a month"}


def test_comma_joined_facts_are_not_one_fact():
    assert extract_rules("CPA is 420, ROAS is 4") == []
    assert extract_rules("Budget is 50k & CPA is 420") == []
    assert [c.text for c in extract_rules("CPA is 420 while ROAS is 4")] == []
    assert [c.text for c in extract_rules("Budget is 1,000")] == ["Budget is 1,000"]


async def test_a_past_tense_remark_never_replaces_the_current_value(free):
    await ingest(free, "CPA is 380", at(2026, 2, 1))
    await ingest(free, "CPA was 420 last month", at(2026, 3, 1))
    assert [m.text for m in await free.mems.list(S, 10, 0)] == ["CPA is 380"]
    assert extract_rules("CPA was 420 last month") == []


async def test_same_timestamp_statements_are_applied_in_arrival_order(free):
    jobs = []

    async def hold(job):
        jobs.append(job)

    free.queue.enqueue = hold
    t = at(2026, 1, 1)
    for text in ("Budget is 50k", "Budget is 80k"):
        await pipeline.add(free, S, text, observed_at=t)  # identical observed_at
    for job in reversed(jobs):  # the worst order
        await pipeline.process(free, job)
    assert [m.text for m in await free.mems.list(S, 5, 0)] == ["Budget is 80k"]


def test_langchain_style_role_names_are_understood():
    text = "ai: Budget is 99k\nhuman: The cap is 7k\ndeveloper: Spend is 1k"
    assert [c.text for c in extract_rules(text)] == ["The cap is 7k"]


async def test_a_users_container_is_filter_is_ignored_not_trusted(free, client_for):
    cl = await client_for("acme", subject="alice")
    r = await cl.post("/v1/search", json={"query": "x", "filters": {"container_is": 5}})
    assert r.status_code == 200


def test_initdb_rejects_unsafe_passwords():
    from pathlib import Path
    sh = (Path(__file__).resolve().parents[2] / "docker" / "initdb.sh").read_text()
    assert "[!A-Za-z0-9_]" in sh and "exit 1" in sh


async def test_grants_are_reapplied_on_every_migrate_so_a_late_role_still_works(admin):
    import asyncpg
    from memory_core.migrate import migrate
    from tests.conftest import ADMIN

    await admin.execute("DROP ROLE IF EXISTS late_app")
    await admin.execute("CREATE ROLE late_app LOGIN PASSWORD 'late_app' NOSUPERUSER NOBYPASSRLS")
    try:
        await migrate(ADMIN, app_role="late_app")
        conn = await asyncpg.connect("postgresql://late_app:late_app@localhost:54329/memaso",
                                     server_settings={"search_path": "memaso,public"})
        try:
            await conn.execute("SELECT set_config('app.tenant','t',false)")
            assert await conn.fetchval("SELECT count(*) FROM documents") == 0  # permission denied before the fix
        finally:
            await conn.close()
    finally:
        await admin.execute("REVOKE ALL ON ALL TABLES IN SCHEMA memaso FROM late_app")
        await admin.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA memaso FROM late_app")
        await admin.execute("REVOKE ALL ON SCHEMA memaso FROM late_app")
        await admin.execute("DROP ROLE late_app")
