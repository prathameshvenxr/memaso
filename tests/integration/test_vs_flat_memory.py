"""Memaso against a plain flat-note memory on the same chat, with no model anywhere. The baseline is the stand-in
in tests/eval/flat_baseline.py: it keeps every statement and loads the file whole, newest first, up to the budget."""
from datetime import UTC, datetime, timedelta

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.retrieval.search import search
from memory_core.tokens import count_tokens
from memory_core.types import Scope
from tests.conftest import FAKE_ENV
from tests.eval.flat_baseline import FlatNotes, run_flat_by_type
from tests.eval.run_eval import run_by_type

FREE = dict(FAKE_ENV, MEMASO__LLM__ADAPTER="none", MEMASO__FACTS__EXTRACT="rules", MEMASO__TRUTH__JUDGE="auto",
            MEMASO__EMBEDDER__DIM="256", MEMASO__QUEUE__WORKERS="4")
DATASET = "tests/eval/dataset_messy.jsonl"
UPDATES = ("marker_update", "paraphrased_update", "passive_update", "from_to_update", "went_to_update",
           "opposite_preference")


async def test_memaso_matches_flat_notes_everywhere_and_beats_them_wherever_a_value_changes(admin):
    c = build(load_config(None, FREE))
    await c.start()
    try:
        memaso = await run_by_type(c, DATASET)
    finally:
        await c.close()
    flat = run_flat_by_type(DATASET)
    print("\n%-26s %8s %8s" % ("category", "memaso", "flat"))
    for t in sorted(memaso):
        print("%-26s %8.2f %8.2f" % (t, memaso[t], flat[t]))
    for t, v in memaso.items():
        assert v >= flat[t], (t, memaso, flat)
        assert v >= 0.9, (t, memaso)
    for t in UPDATES:  # flat notes keep the old value beside the new one, so a changed value always leaks
        assert flat[t] == 0.0 and memaso[t] >= 0.9, (t, memaso, flat)
    assert memaso["daily_is_not_monthly"] == 1.0  # and Memaso did not over-merge to get there


async def test_at_scale_flat_notes_cost_far_more_and_drop_the_oldest_fact(admin):
    c = build(load_config(None, FREE))
    await c.start()
    scope, flat, start = Scope("eval", "scale"), FlatNotes(), datetime(2026, 1, 1, tzinfo=UTC)
    try:
        texts = ["The budget is 80k."] + [f"The metric{i} target is {i}000." for i in range(300)]
        for i, text in enumerate(texts):
            when = start + timedelta(hours=i)
            await pipeline.add(c, scope, text, observed_at=when)
            flat.add(text, when)
        await c.queue.join()
        ours = await search(c, scope, "what is the budget", k=5, token_budget=1000)
    finally:
        await c.close()
    whole, cut = count_tokens(flat.recall(None)), flat.recall(1000)
    print(f"\nprompt tokens for one question over {len(texts)} notes: memaso {ours.tokens}, flat file {whole}")
    assert "80k" in ours.context and "80k" not in cut          # the oldest fact falls off a budgeted flat file
    assert whole >= 10 * ours.tokens                           # and the whole file costs an order of magnitude more
