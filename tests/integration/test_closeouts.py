"""Tests for the items the README listed as open: atomic forget, poison predecessors, token counting, compose."""
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from memory_core.bootstrap import build
from memory_core.config import load_config
from memory_core.errors import LLMSchemaError
from memory_core.ingest import pipeline
from memory_core.manage.delete import hard_delete
from memory_core.retrieval.pack import pack
from memory_core.types import DocStatus, Hit, Kind, Memory, Scope
from tests.conftest import FAKE_ENV
from tests.fakes import ScriptedLLM

ROOT = Path(__file__).resolve().parents[2]
S = Scope("acme", "u")
FREE = dict(FAKE_ENV, MEMASO__LLM__ADAPTER="none", MEMASO__FACTS__EXTRACT="rules", MEMASO__TRUTH__JUDGE="auto")


def at(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


async def test_forgetting_a_memory_is_one_store_call_not_several_transactions(c):
    await pipeline.add(c, S, "Budget is 50k. The CPA target is 420.", observed_at=at(2026, 1, 1))
    await c.queue.join()
    budget = next(m for m in await c.mems.list(S, 5, 0) if "Budget" in m.text)
    calls = []
    for owner, name in ((c.docs, "delete_orphans"), (c.docs, "redact"), (c.chunks, "redact"), (c.mems, "delete_where")):
        real = getattr(owner, name)

        async def spy(*a, _real=real, _name=name, **k):
            calls.append(_name)
            return await _real(*a, **k)

        setattr(owner, name, spy)
    receipt = await hard_delete(c, "acme", memory_id=budget.id)
    assert calls == [], "forget(memory_id) must be a single atomic store operation"
    assert receipt.counts["memories"] == 1


async def test_a_failing_older_document_does_not_block_a_newer_one(c):
    jobs = []

    async def hold(job):
        jobs.append(job)

    c.queue.enqueue = hold
    old = await pipeline.add(c, S, "Budget is 50k", observed_at=at(2026, 1, 1))
    new = await pipeline.add(c, S, "Spend is 9k", observed_at=at(2026, 2, 1))
    c.llm = ScriptedLLM([LLMSchemaError("x"), LLMSchemaError("y"),            # the older document cannot be read
                         {"memories": [{"text": "Spend is 9k", "kind": "fact"}]}])  # the newer one can
    c.cfg.facts["extract"] = "llm"
    await pipeline.process(c, {"tenant": "acme", "doc_id": new.id})
    assert (await c.docs.get("acme", new.id)).status == DocStatus.indexed
    assert [m.text for m in await c.mems.list(S, 5, 0)] == ["Spend is 9k"]
    assert (await c.docs.get("acme", old.id)).status != DocStatus.indexed   # still pending its own retries
    c.llm = ScriptedLLM([{"memories": [{"text": "Budget is 50k", "kind": "fact"}]}])
    await pipeline.process(c, {"tenant": "acme", "doc_id": old.id})        # and it still works when it is retried
    assert {m.text for m in await c.mems.list(S, 5, 0)} == {"Spend is 9k", "Budget is 50k"}


def _hit(text: str) -> Hit:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    m = Memory(id="0f8fad5b-d9cb-469f-a165-70867728950e", scope=S, text=text, kind=Kind.fact, entities=[],
               confidence=1, importance=.5, valid_from=now, valid_to=None, supersedes=[], source_doc_ids=[],
               extractor_version="t", created_at=now)
    return Hit(m, 1.0)


def test_memory_tags_carry_a_short_id_so_a_real_tokenizer_cannot_blow_the_budget():
    ctx, _, _ = pack([_hit("Budget is 80k")], 500)
    assert 'id="0f8fad5b"' in ctx and "d9cb-469f" not in ctx


def test_a_real_tokenizer_can_be_plugged_in_and_is_used_for_packing():
    from memory_core import tokens

    try:
        tokens.set_counter(lambda t: len(t))  # one token per character: far stricter than the default estimate
        ctx, kept, used = pack([_hit("Budget is 80k")] * 5, 200)
        assert used <= 200 and len(ctx) <= 200 and len(kept) < 5
    finally:
        tokens.set_counter(None)


def test_the_token_counter_is_selectable_by_config():
    from memory_core import tokens

    try:
        build(load_config(None, dict(FREE, MEMASO__RETRIEVAL__TOKEN_COUNTER="tests.fakes:char_counter")))
        assert tokens.count_tokens("abcd") == 4
    finally:
        tokens.set_counter(None)


def test_compose_migrates_in_a_one_shot_service_and_the_api_holds_no_admin_credentials():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    api, migrate = compose["services"]["api"], compose["services"]["migrate"]
    assert "MEMASO__STORE__ADMIN_DSN" in migrate["environment"]
    assert "MEMASO__STORE__ADMIN_DSN" not in api["environment"]
    assert api["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert "migrate" not in (ROOT / "Dockerfile").read_text().split("CMD")[-1]


def test_database_passwords_come_from_the_environment_with_dev_defaults():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "${MEMASO_DB_ADMIN_PASSWORD:-" in compose and "${MEMASO_APP_PASSWORD:-" in compose
    assert (ROOT / "docker" / "initdb.sh").exists() and not (ROOT / "docker" / "initdb.sql").exists()


def test_ci_runs_the_suite_against_pgvector():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    job = next(iter(wf["jobs"].values()))
    assert "pgvector" in job["services"]["db"]["image"]
