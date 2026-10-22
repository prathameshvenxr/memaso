"""FROZEN: the PRD's quality targets (AC-1, AC-2), measured on the offline adapters and, with -m live, on real ones."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from memory_core.bootstrap import build
from memory_core.config import load_config
from tests.conftest import FAKE_ENV
from tests.eval.run_eval import run_eval

DATA = Path(__file__).resolve().parents[1] / "eval" / "dataset.jsonl"


def test_dataset_has_at_least_200_conversations_covering_every_question_type():
    items = [json.loads(l) for l in DATA.read_text().splitlines() if l.strip()]
    assert len(items) >= 200
    assert {i["type"] for i in items} == {"single", "preference", "update", "multi_session", "temporal", "abstain"}


def assert_targets(r: dict):
    assert r["recall_at_5"] >= 0.85, r
    assert r["correct_after_update"] >= 0.90, r
    assert r["abstention"] >= 0.80, r
    assert r["stale_leak"] == 0, r


async def test_targets_met_on_offline_adapters(admin):
    c = build(load_config(None, dict(FAKE_ENV, MEMASO__EMBEDDER__DIM="256", MEMASO__QUEUE__WORKERS="4")))
    await c.start()
    try:
        assert_targets(await run_eval(c, DATA))
    finally:
        await c.close()


@pytest.mark.live
async def test_targets_met_on_configured_real_adapters(admin):
    """MEMASO_CONFIG=config.yaml pytest -m live tests/acceptance/test_eval_targets.py"""
    c = build(load_config(os.environ["MEMASO_CONFIG"]))
    await c.start()
    try:
        assert_targets(await run_eval(c, DATA))
    finally:
        await c.close()
