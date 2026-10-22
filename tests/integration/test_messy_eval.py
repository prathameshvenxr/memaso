"""Messy-chat eval with no model at all (rules + verbatim), on the offline hash embedder. Prints every category."""
import pytest

from memory_core.bootstrap import build
from memory_core.config import load_config
from tests.conftest import FAKE_ENV
from tests.eval.run_eval import run_by_type

FREE = dict(FAKE_ENV, MEMASO__LLM__ADAPTER="none", MEMASO__FACTS__EXTRACT="rules", MEMASO__TRUTH__JUDGE="auto",
            MEMASO__EMBEDDER__DIM="256", MEMASO__QUEUE__WORKERS="4")
SUPPORTED = ("first_person_preference", "marker_update", "compound_sentence", "buried_fact", "opposite_preference")


async def test_messy_chat_categories_the_rules_are_meant_to_handle(admin):
    c = build(load_config(None, FREE))
    await c.start()
    try:
        r = await run_by_type(c, "tests/eval/dataset_messy.jsonl")
    finally:
        await c.close()
    print("MESSY EVAL (hash embedder, no model)", {k: round(v, 2) for k, v in sorted(r.items())})
    for t in SUPPORTED:
        assert r[t] >= 0.9, (t, r)
