from datetime import UTC, datetime

import pytest

from memory_core.errors import ExtractionFailed, LLMSchemaError
from memory_core.ingest.extract import extract
from memory_core.types import Kind
from tests.fakes import ScriptedLLM

AS_OF = datetime(2026, 10, 22, tzinfo=UTC)
GOOD = {"memories": [{"text": "Acme's CPA target is 420 INR", "kind": "decision", "entities": ["Acme"],
                      "confidence": 0.9, "valid_from": "2026-11-01"},
                     {"text": "Acme prefers weekly reports", "kind": "preference"}]}


async def test_returns_atomic_candidates_with_kind_and_version():
    llm = ScriptedLLM([GOOD])
    cands, version = await extract(llm, "chat", AS_OF)
    assert [c.kind for c in cands] == [Kind.decision, Kind.preference]
    assert version == "v1"
    assert cands[0].valid_from == datetime(2026, 11, 1, tzinfo=UTC)
    assert cands[1].confidence == 0.8  # default when the model omits it


async def test_as_of_is_sent_so_relative_dates_resolve():
    llm = ScriptedLLM([GOOD])
    await extract(llm, "next month budget changes", AS_OF)
    assert "2026-10-22" in llm.calls[0][0][-1]["content"]


async def test_malformed_output_retries_once_then_raises():
    llm = ScriptedLLM([LLMSchemaError("x"), LLMSchemaError("y")])
    with pytest.raises(ExtractionFailed):
        await extract(llm, "chat", AS_OF)
    assert len(llm.calls) == 2


async def test_malformed_then_good_succeeds_on_retry():
    llm = ScriptedLLM([LLMSchemaError("x"), GOOD])
    cands, _ = await extract(llm, "chat", AS_OF)
    assert len(cands) == 2


async def test_off_schema_kind_is_rejected():
    bad = {"memories": [{"text": "x", "kind": "gossip"}]}
    with pytest.raises(ExtractionFailed):
        await extract(ScriptedLLM([bad, bad]), "chat", AS_OF)


async def test_unparsable_date_is_dropped_but_memory_kept():
    out = {"memories": [{"text": "x is 1", "kind": "fact", "valid_from": "soon"}]}
    (c,), _ = await extract(ScriptedLLM([out]), "chat", AS_OF)
    assert c.valid_from is None
