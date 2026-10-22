"""Needs the fastembed model (downloads ~90 MB once): pytest -m live tests/integration/test_live_fastembed.py"""
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.retrieval.search import search
from memory_core.types import Scope
from tests.conftest import ADMIN, APP
from tests.eval.run_eval import run_eval

pytestmark = pytest.mark.live
S = Scope("acme", "u")
ENV = {"MEMASO__LLM__ADAPTER": "none", "MEMASO__EMBEDDER__ADAPTER": "fastembed", "MEMASO__STORE__DSN": APP,
       "MEMASO__STORE__ADMIN_DSN": ADMIN, "MEMASO__QUEUE__WORKERS": "4"}  # every other setting is the shipped default


@pytest_asyncio.fixture
async def real(admin):
    c = build(load_config(None, ENV))
    await c.start()
    yield c
    await c.close()


def at(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def test_update_is_found_and_superseded_with_real_embeddings_and_default_thresholds(real):
    for text, when in (("The budget is 50k", at(2026, 1, 1)), ("The budget is 80k", at(2026, 2, 1)),
                       ("The CPA target is 420", at(2026, 2, 2)), ("The ROAS target is 4", at(2026, 2, 3))):
        await pipeline.add(real, S, text, observed_at=when)
        await real.queue.join()
    assert {m.text for m in await real.mems.list(S, 10, 0)} == {"The budget is 80k", "The CPA target is 420",
                                                                   "The ROAS target is 4"}


async def test_a_paraphrased_update_needs_the_model_and_gets_it_when_there_is_one(admin):
    calls = []

    async def host(messages, schema):
        calls.append(1)
        import json
        cand = json.loads(messages[-1]["content"])
        return {"verdict": "updates", "target_id": cand["neighbours"][0]["id"], "reason": "paraphrase"}

    with_model = build(load_config(None, ENV), llm=host)
    await with_model.start()
    try:
        for text, when in (("The budget is 50k", at(2026, 1, 1)), ("Our monthly budget is now set to 80k", at(2026, 2, 1))):
            await pipeline.add(with_model, S, text, observed_at=when)
            await with_model.queue.join()
        assert len(calls) == 1  # asked once, only for the paraphrase
        assert [m.text for m in await with_model.mems.list(S, 10, 0)] == ["Our monthly budget is now set to 80k"]
    finally:
        await with_model.close()


async def test_without_a_model_a_paraphrase_is_kept_as_a_separate_fact_never_guessed(real):
    for text, when in (("The budget is 50k", at(2026, 1, 1)), ("Our monthly budget is now set to 80k", at(2026, 2, 1))):
        await pipeline.add(real, S, text, observed_at=when)
        await real.queue.join()
    assert len(await real.mems.list(S, 10, 0)) == 2


async def test_real_embeddings_answer_natural_questions_and_abstain_off_domain(real):
    await pipeline.add(real, S, "The CPA target is 420 for the Diwali account", observed_at=at(2026, 1, 1))
    await real.queue.join()
    for q in ("what CPA are we aiming for?", "CPA target for the Diwali account"):
        assert "420" in (await search(real, S, q)).context, q
    for q in ("who won the cricket world cup", "what is the weather in Mumbai", "how do I bake bread"):
        assert (await search(real, S, q)).context == "", q


async def test_eval_on_real_embeddings_with_no_model(real):
    """Abstention on the dataset's near-domain probes ('churn of campq41' beside ad metrics) is reported, not
    asserted: MiniLM scores those ~0.5, indistinguishable from a vague real question. See README limits."""
    r = await run_eval(real, "tests/eval/dataset.jsonl")
    print("REAL EMBEDDER EVAL", r)
    assert r["recall_at_5"] >= 0.85 and r["correct_after_update"] >= 0.90 and r["stale_leak"] == 0, r


async def test_messy_chat_eval_on_real_embeddings_with_no_model(real):
    from tests.eval.run_eval import run_by_type
    r = await run_by_type(real, "tests/eval/dataset_messy.jsonl")
    print("MESSY EVAL (real MiniLM, no model)", {k: round(v, 2) for k, v in sorted(r.items())})
    for t in ("first_person_preference", "marker_update", "compound_sentence", "buried_fact", "opposite_preference"):
        assert r[t] >= 0.9, (t, r)
