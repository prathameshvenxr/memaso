"""FROZEN acceptance tests: written from the PRD before the first review round. The builder never edits these."""
from __future__ import annotations

import random
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.ingest import pipeline
from memory_core.manage.delete import hard_delete
from memory_core.retrieval.search import search
from memory_core.tokens import count_tokens
from memory_core.types import Scope

from ..conftest import FAKE_ENV

S = Scope("acme", "diwali-account")


def utc(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def ingest(c, text, scope=S, at=None):
    doc = await pipeline.add(c, scope, text, observed_at=at)
    await c.queue.join()
    return doc


async def test_recall_after_add(c):
    await ingest(c, "Budget is 50k. The account prefers INR reports.")
    r = await search(c, S, "what is the budget", token_budget=500)
    assert "50k" in r.context
    assert r.tokens <= 500


async def test_update_supersedes_and_history_is_kept(c):
    await ingest(c, "Budget is 50k", at=utc(2026, 1, 1))
    await ingest(c, "Budget is 80k", at=utc(2026, 3, 1))
    now = await search(c, S, "budget")
    assert "80k" in now.context and "50k" not in now.context
    then = await search(c, S, "budget", as_of=utc(2026, 2, 1))
    assert "50k" in then.context and "80k" not in then.context
    hist = await search(c, S, "budget", include_history=True)
    assert "50k" in hist.context and "80k" in hist.context


async def test_tenants_are_isolated_over_the_api(c, client_for):
    a, b = await client_for("tenant-a"), await client_for("tenant-b")
    r = await a.post("/v1/memories", json={"content": "Budget is 50k", "subject": "u1"})
    assert r.status_code == 202
    await c.queue.join()
    mine = (await a.post("/v1/search", json={"query": "budget", "subject": "u1"})).json()
    assert mine["results"]
    mid = mine["results"][0]["id"]
    assert (await b.get(f"/v1/memories/{mid}")).status_code == 404
    for q in ["budget", "50k", "Budget is 50k", "u1"]:
        theirs = (await b.post("/v1/search", json={"query": q, "subject": "u1"})).json()
        assert theirs["results"] == [] and theirs["context"] == ""
    # the tenant named in a body is ignored; the key decides
    await b.post("/v1/memories", json={"content": "Spend is 9k", "subject": "u1", "tenant": "tenant-a"})
    await c.queue.join()
    leak = (await a.post("/v1/search", json={"query": "spend", "subject": "u1"})).json()
    assert leak["results"] == []


async def test_hard_delete_leaves_nothing(c, admin):
    await ingest(c, "Budget is 50k. The account prefers INR reports.")
    await ingest(c, "Budget is 80k", at=utc(2027, 1, 1))
    receipt = await hard_delete(c, "acme", subject="diwali-account")
    assert receipt.counts["memories"] >= 2
    assert (await search(c, S, "budget", include_history=True)).results == []
    for table in ("memories", "embeddings", "memory_links", "memory_sources", "documents"):
        assert await admin.fetchval(f"SELECT count(*) FROM {table}") == 0, table


async def test_token_budget_is_never_exceeded(c):
    await ingest(c, ". ".join(f"Metric{i} is value{i} for campaign{i}" for i in range(40)))
    rnd = random.Random(7)
    for _ in range(25):
        budget = rnd.randint(1, 400)
        r = await search(c, S, f"metric{rnd.randint(0, 39)} value", k=20, token_budget=budget)
        assert count_tokens(r.context) <= budget


async def test_blank_input_is_rejected(c, client_for):
    cl = await client_for("acme")
    assert (await cl.post("/v1/memories", json={"content": "   ", "subject": "u"})).status_code == 422
    assert (await cl.post("/v1/search", json={"query": "  ", "subject": "u"})).status_code == 422
    with pytest.raises(ValueError):
        await search(c, S, "")


async def test_missing_or_bad_key_is_401(client_for):
    cl = await client_for("acme")
    cl.headers.pop("Authorization")
    assert (await cl.get("/v1/profile?subject=u")).status_code == 401
    cl.headers["Authorization"] = "Bearer nope"
    assert (await cl.get("/v1/profile?subject=u")).status_code == 401


async def test_adapters_swap_by_config_only():
    env = dict(FAKE_ENV, MEMASO__LLM__ADAPTER="compat", MEMASO__LLM__MODEL="m",
               MEMASO__LLM__BASE_URL="http://localhost:9/v1")
    c = build(load_config(None, env))
    assert type(c.llm.inner).__name__ == "CompatLLM"
    assert type(build(load_config(None, FAKE_ENV)).llm.inner).__name__ == "FakeLLM"


def test_core_never_imports_adapters_or_vendors():
    root = Path(__file__).resolve().parents[2] / "memory_core"
    banned = re.compile(r"^\s*(from|import)\s+(\.+adapters|memory_core\.adapters|anthropic|openai|redis|asyncpg)", re.M)
    for pkg in ("ingest", "retrieval", "manage", "api"):
        for f in (root / pkg).glob("*.py"):
            assert not banned.search(f.read_text()), f"{f.name} imports an adapter or vendor SDK"
