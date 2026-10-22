"""Memory management: paraphrase-proof updates, importance and decay, consolidation, and the entity graph.
All of it runs with no model, on the offline hash embedder."""
import uuid
from datetime import UTC, datetime, timedelta

import pytest_asyncio

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.ingest.importance import score_importance
from memory_core.manage.consolidate import consolidate, restore
from memory_core.memory import Memory as Facade
from memory_core.retrieval.profile import profile
from memory_core.retrieval.salience import salience
from memory_core.retrieval.search import search
from memory_core.types import Kind, Memory, Scope
from tests.conftest import FAKE_ENV

FREE = dict(FAKE_ENV, MEMASO__LLM__ADAPTER="none", MEMASO__FACTS__EXTRACT="rules", MEMASO__TRUTH__JUDGE="auto")
S = Scope("acme", "u")


def at(m, d, y=2026):
    return datetime(y, m, d, tzinfo=UTC)


def recent(days):
    """A time relative to the real clock, for tests that run through code that reads it."""
    return datetime.now(UTC) - timedelta(days=days)


@pytest_asyncio.fixture
async def free(admin):
    c = build(load_config(None, FREE))
    await c.start()
    yield c
    await c.close()


async def feed(c, items, scope=S):
    for text, when in items:
        await pipeline.add(c, scope, text, observed_at=when)
        await c.queue.join()


async def raw(c, text, *, kind="fact", importance=0.5, created=None, container=None, entities=(), subject="u"):
    """Insert a memory directly, bypassing truth maintenance, so a test controls its exact state."""
    when = created or at(1, 1)
    m = Memory(id=str(uuid.uuid4()), scope=Scope("acme", subject, container), text=text, kind=Kind(kind),
               entities=list(entities), confidence=0.8, importance=importance, valid_from=when, valid_to=None,
               supersedes=[], source_doc_ids=[], extractor_version="t", created_at=when)
    (v,) = await c.embedder.embed([text])
    await c.mems.insert(m, v, c.embedder.model_id)
    return m


# ---------------------------------------------------------------- paraphrase-proof updates
async def test_a_paraphrased_update_closes_the_old_fact_without_a_model(free):
    await feed(free, [("Budget is 50k", at(1, 1)), ("We've raised our monthly budget to 80k.", at(2, 1))])
    assert [m.text for m in await free.mems.list(S, 10, 0)] == ["monthly budget is 80k"]
    assert {m.text for m in await free.mems.list(S, 10, 0, include_history=True)} == {"Budget is 50k", "monthly budget is 80k"}
    ctx = (await search(free, S, "what is the budget")).context
    assert "80k" in ctx and "50k" not in ctx


async def test_a_daily_figure_does_not_close_a_monthly_one(free):
    await feed(free, [("Budget is 50k", at(1, 1)), ("The daily budget is 2k.", at(2, 1))])
    assert {m.text for m in await free.mems.list(S, 10, 0)} == {"Budget is 50k", "The daily budget is 2k"}


async def test_restating_a_fact_in_other_words_is_a_duplicate_not_a_new_version(free):
    await feed(free, [("Budget is 50k", at(1, 1)), ("The budget is 50k.", at(2, 1))])
    ms = await free.mems.list(S, 10, 0, include_history=True)
    assert len(ms) == 1 and ms[0].confidence > 0.75


# ---------------------------------------------------------------- importance, decay
def test_importance_reflects_kind_emphasis_and_explicit_saves():
    assert score_importance("We must never send reports on Sundays", Kind.decision) >= 0.85
    assert score_importance("Budget is 50k", Kind.fact) < 0.7
    assert score_importance("Budget is 50k", Kind.fact, explicit=True) > score_importance("Budget is 50k", Kind.fact)
    assert score_importance("It rained", Kind.episode) < score_importance("Budget is 50k", Kind.fact)
    assert 0 <= score_importance("always never must critical 5k", Kind.decision, explicit=True) <= 1


def _m(importance, created, accessed=None, n=0):
    return Memory(id="x", scope=S, text="t", kind=Kind.fact, entities=[], confidence=0.8, importance=importance,
                  valid_from=created, valid_to=None, supersedes=[], source_doc_ids=[], extractor_version="t",
                  created_at=created, last_accessed=accessed, access_count=n)


def test_salience_favours_important_recent_and_used_memories():
    now = at(6, 1)
    fresh_important = _m(0.9, now - timedelta(days=5))
    stale_trivial = _m(0.3, now - timedelta(days=400))
    used = _m(0.3, now - timedelta(days=400), accessed=now - timedelta(days=2), n=5)
    assert salience(fresh_important, now) > salience(used, now) > salience(stale_trivial, now)
    assert all(0.0 <= salience(x, now) <= 1.0 for x in (fresh_important, stale_trivial, used))


async def test_importance_is_scored_when_a_fact_is_stored_and_grows_with_corroboration(free):
    await feed(free, [("Budget is 50k", at(1, 1))])
    (m,) = await free.mems.list(S, 10, 0)
    before = m.importance
    await feed(free, [("The budget is 50k.", at(1, 5))])
    (m,) = await free.mems.list(S, 10, 0)
    assert m.importance > before
    saved = await pipeline.remember(free, S, "Never send reports on Sundays", "decision")
    assert saved.importance >= 0.9


async def test_search_records_that_a_memory_was_used(free):
    await feed(free, [("Budget is 50k", at(1, 1))])
    await search(free, S, "what is the budget")
    (m,) = await free.mems.list(S, 10, 0)
    assert m.access_count == 1 and m.last_accessed is not None


async def test_a_small_profile_keeps_what_matters_most(free):
    for i in range(30):
        await pipeline.remember(free, S, f"Widget{i} is blue{i}", "fact", observed_at=at(1, 1))
    await pipeline.remember(free, S, "We decided never to raise spend on Sundays", "decision", observed_at=at(1, 2))
    got = (await profile(free, S, token_budget=120)).context
    assert "never to raise spend on Sundays" in got


# ---------------------------------------------------------------- consolidation
async def test_consolidation_merges_duplicates_that_truth_maintenance_missed(free):
    a = await raw(free, "Budget is 50k", created=at(1, 1))
    await raw(free, "The budget is 50k", created=at(1, 2))
    await raw(free, "Budget is 50k", created=at(1, 3), container="other")   # another account: not a duplicate
    rep = await consolidate(free, S, now=at(2, 1))
    assert rep["merged"] == 1
    current = await free.mems.list(S, 10, 0)
    assert len(current) == 2 and a.id in {m.id for m in current}
    assert len(await free.mems.list(S, 10, 0, include_history=True)) == 3   # nothing is deleted
    kept = await free.mems.get("acme", a.id)
    assert kept.importance > 0.5 and kept.confidence > 0.8


async def test_cold_low_value_facts_are_archived_not_deleted_and_can_be_restored(free):
    now = datetime.now(UTC)
    cold = await raw(free, "Widget is blue", importance=0.3, created=now - timedelta(days=400))
    await raw(free, "We decided never to raise spend on Sundays", kind="decision", importance=0.9,
              created=now - timedelta(days=400))
    used = await raw(free, "Gadget is red", importance=0.3, created=now - timedelta(days=400))
    await raw(free, "Fresh is green", created=now - timedelta(days=2))
    await free.mems.touch("acme", [used.id])
    rep = await consolidate(free, S, now=now)
    assert rep["archived"] == 1
    texts = {m.text for m in await free.mems.list(S, 10, 0)}
    assert texts == {"We decided never to raise spend on Sundays", "Gadget is red", "Fresh is green"}
    assert cold.text in {m.text for m in await free.mems.list(S, 10, 0, include_history=True)}
    assert "blue" not in (await search(free, S, "widget colour")).context
    assert "blue" in (await search(free, S, "widget colour", include_history=True)).context
    assert await restore(free, "acme", [cold.id]) == 1
    assert cold.text in {m.text for m in await free.mems.list(S, 10, 0)}


async def test_a_cap_on_current_facts_archives_the_least_salient_first(free):
    now = datetime.now(UTC)
    for i in range(6):
        await raw(free, f"Item{i} is v{i}", created=now - timedelta(days=10 * (i + 1)))
    free.cfg.consolidation["max_current"] = 3
    rep = await consolidate(free, S, now=now)
    assert rep["archived"] == 3
    assert {m.text for m in await free.mems.list(S, 10, 0)} == {"Item0 is v0", "Item1 is v1", "Item2 is v2"}


async def test_consolidation_is_audited_with_counts_only_and_exposed_on_the_facade(free, admin):
    await raw(free, "Budget is 50k", created=at(1, 1))
    await raw(free, "The budget is 50k", created=at(1, 2))
    rep = await Facade(free).consolidate(S)
    assert rep["merged"] == 1
    row = await admin.fetchrow("SELECT detail::text AS d FROM audit_log WHERE action='consolidate'")
    assert row and "50k" not in row["d"] and "merged" in row["d"]


# ---------------------------------------------------------------- entity graph
async def test_a_question_about_a_person_reaches_facts_linked_through_an_entity(free):
    await feed(free, [("The account lead for Acme is Priya.", at(1, 1)), ("The Acme CPA target is 420.", at(1, 2))])
    q = "what number should Priya hit"
    with_graph = (await search(free, S, q)).context
    assert "420" in with_graph and "Priya" in with_graph
    free.cfg.retrieval["graph_hops"] = 0
    without = (await search(free, S, q)).context
    assert "420" not in without and "Priya" in without


async def test_the_graph_only_walks_to_current_facts(free):
    await feed(free, [("The account lead for Acme is Priya.", at(1, 1)), ("The Acme CPA target is 420.", at(1, 2)),
                      ("The Acme CPA target is 380.", at(2, 1))])
    ctx = (await search(free, S, "what number should Priya hit")).context
    assert "380" in ctx and "420" not in ctx


async def test_a_question_with_no_known_entity_still_gets_nothing(free):
    await feed(free, [("The account lead for Acme is Priya.", at(1, 1))])
    assert (await search(free, S, "how do I bake bread")).context == ""


async def test_entity_keys_are_stored_lower_cased_for_indexed_lookup(free, admin):
    await feed(free, [("The Acme CPA target is 420.", at(1, 2))])
    row = await admin.fetchrow("SELECT entity_keys FROM memories")
    assert set(row["entity_keys"]) == {"acme", "cpa"}


# ---------------------------------------------------------------- REST
async def test_the_rest_api_consolidates_and_restores_within_the_callers_subject(free, client_for):
    cl = await client_for("acme", "u")
    await raw(free, "Budget is 50k", created=recent(3))
    await raw(free, "The budget is 50k", created=recent(2))
    r = await cl.post("/v1/consolidate", json={})
    assert r.status_code == 200 and r.json() == {"merged": 1, "archived": 0}
    other = await raw(free, "Widget is blue", subject="other")
    assert (await cl.post("/v1/restore", json={"memory_ids": [other.id]})).status_code == 404  # not this key's subject
    assert (await cl.post("/v1/restore", json={"memory_ids": ["nope"]})).json() == {"restored": 0}
    assert (await cl.post("/v1/consolidate", json={"subject": "u", "typo": 1})).status_code == 422


async def test_the_sdk_exposes_consolidate_and_restore(free, client_for):
    from memory_core.sdk import MemoryClient

    cl = await client_for("acme", "u")
    sdk = MemoryClient("http://t", "unused", subject="u", transport=cl._transport)
    sdk.http.headers.update(cl.headers)
    await raw(free, "Budget is 50k", created=recent(3))
    await raw(free, "The budget is 50k", created=recent(2))
    assert await sdk.consolidate() == {"merged": 1, "archived": 0}
    assert await sdk.restore(["nope"]) == 0
    await sdk.aclose()


# ---------------------------------------------------------------- found by independent review
async def test_an_old_ordinary_fact_is_archived_and_its_words_leave_search_with_it(free, admin):
    await feed(free, [("Budget is 50k", at(1, 1))])
    await admin.execute("UPDATE memories SET created_at = now() - interval '400 days'")
    old = (await free.mems.list(S, 10, 0))[0]
    assert (await consolidate(free, S))["archived"] == 1
    assert "50k" not in (await search(free, S, "what is the budget")).context          # the verbatim chunk goes too
    assert "50k" in (await search(free, S, "what is the budget", include_history=True)).context
    # a newer value arrives while the old one is archived, and restoring the old one must not resurrect it
    await feed(free, [("Budget is 80k", at(2, 1))])
    assert await restore(free, "acme", [old.id]) == 0
    assert {m.text for m in await free.mems.list(S, 10, 0)} == {"Budget is 80k"}
    assert "50k" not in (await search(free, S, "what is the budget")).context


async def test_restoring_a_fact_nothing_has_replaced_brings_it_back(free, admin):
    await feed(free, [("Widget is blue", at(1, 1))])
    await admin.execute("UPDATE memories SET created_at = now() - interval '400 days'")
    (old,) = await free.mems.list(S, 10, 0)
    await consolidate(free, S)
    assert await restore(free, "acme", [old.id]) == 1
    assert [m.text for m in await free.mems.list(S, 10, 0)] == ["Widget is blue"]
    assert "blue" in (await search(free, S, "widget colour")).context


async def test_consolidating_one_container_does_not_archive_facts_shared_with_the_others(free):
    now = datetime.now(UTC)
    for i in range(3):
        await raw(free, f"Shared{i} is old{i}", created=now - timedelta(days=300 + i))      # no container: shared
    for i in range(3):
        await raw(free, f"Mine{i} is new{i}", created=now - timedelta(days=i + 1), container="A")
    free.cfg.consolidation["max_current"] = 3
    rep = await consolidate(free, Scope("acme", "u", "A"), now=now)
    assert rep["archived"] == 0
    assert len(await free.mems.list(Scope("acme", "u", "B"), 10, 0)) == 3     # container B still sees the shared facts


async def test_forgetting_a_memory_forgets_the_duplicates_merged_into_it(free):
    kept = await raw(free, "Budget is 50k", created=recent(3))
    await raw(free, "The budget is 50k", created=recent(2))
    assert (await consolidate(free, S))["merged"] == 1
    assert (await free.mems.forget("acme", kept.id))["memories"] == 1
    assert await free.mems.list(S, 10, 0, include_history=True) == []
    assert "50k" not in str(await Facade(free).export(S))


async def test_forgetting_with_reopen_does_not_reopen_a_merged_duplicate(free):
    kept = await raw(free, "Budget is 50k", created=recent(3))
    await raw(free, "The budget is 50k", created=recent(2))
    await consolidate(free, S)
    await free.mems.forget("acme", kept.id, reopen=True)
    assert await free.mems.list(S, 10, 0, include_history=True) == []


async def test_two_consolidations_at_once_merge_once_and_raise_confidence_once(free):
    import asyncio

    kept = await raw(free, "Budget is 50k", created=recent(3))
    await raw(free, "The budget is 50k", created=recent(2))
    a, b = await asyncio.gather(consolidate(free, S), consolidate(free, S))
    assert a["merged"] + b["merged"] == 1
    got = await free.mems.get("acme", kept.id)
    assert abs(got.importance - 0.55) < 1e-6 and abs(got.confidence - 0.9) < 1e-6


async def test_a_multi_word_entity_is_found_from_the_question(free):
    await pipeline.remember(free, S, "The CPA target is 420", "fact", entities=["Acme Corp"])
    await pipeline.remember(free, S, "The account lead is Priya", "fact", entities=["Priya", "Acme Corp"])
    free.cfg.retrieval["graph_hops"] = 1
    ctx = (await search(free, S, "what number should the acme corp team hit")).context
    assert "420" in ctx
