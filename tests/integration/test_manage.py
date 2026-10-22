from datetime import UTC, datetime

import pytest

from memory_core.ingest import pipeline
from memory_core.manage.crud import edit_memory, list_memories
from memory_core.manage.delete import hard_delete
from memory_core.manage.export import export_scope
from memory_core.retrieval.search import search
from memory_core.types import Scope

S = Scope("acme", "u")
T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def ingest(c, text, scope=S):
    doc = await pipeline.add(c, scope, text, observed_at=T0)
    await c.queue.join()
    return doc


async def test_edit_creates_new_version_and_hides_old(c):
    await ingest(c, "Budget is 50k")
    (old,) = await list_memories(c, S)
    new = await edit_memory(c, "acme", old.id, "Budget is 55k")
    assert new.supersedes == [old.id]
    assert [m.text for m in await list_memories(c, S)] == ["Budget is 55k"]
    assert len(await list_memories(c, S, include_history=True)) == 2
    assert await edit_memory(c, "acme", old.id, "again") is None  # old version is no longer editable
    with pytest.raises(ValueError):
        await edit_memory(c, "acme", new.id, "  ")


async def test_delete_by_doc_removes_only_its_memories(c):
    d1 = await ingest(c, "Budget is 50k")
    await ingest(c, "Spend is 9k")
    r = await hard_delete(c, "acme", doc_id=d1.id)
    assert r.counts == {"memories": 1, "documents": 1}
    assert [m.text for m in await list_memories(c, S)] == ["Spend is 9k"]


async def test_shared_memory_survives_deleting_one_of_two_sources(c):
    d1 = await ingest(c, "Budget is 50k")
    await pipeline.add(c, S, "budget is 50k.", observed_at=T0)  # restatement -> second source, same memory
    await c.queue.join()
    r = await hard_delete(c, "acme", doc_id=d1.id)
    assert r.counts["memories"] == 0 and len(await list_memories(c, S)) == 1


async def test_delete_subject_leaves_other_subjects(c):
    await ingest(c, "Budget is 50k")
    other = Scope("acme", "other")
    await ingest(c, "Budget is 70k", other)
    await hard_delete(c, "acme", subject="u")
    assert await list_memories(c, S) == [] and len(await list_memories(c, other)) == 1


async def test_delete_everything_wipes_tenant_but_not_other_tenants(c):
    await ingest(c, "Budget is 50k")
    await ingest(c, "Budget is 70k", Scope("globex", "u"))
    await hard_delete(c, "acme", everything=True)
    assert await list_memories(c, S) == []
    assert len(await list_memories(c, Scope("globex", "u"))) == 1


async def test_selector_rules(c):
    with pytest.raises(ValueError):
        await hard_delete(c, "acme")
    with pytest.raises(ValueError):
        await hard_delete(c, "acme", subject="u", everything=True)


async def test_receipt_is_stored_and_retrievable(c):
    await ingest(c, "Budget is 50k")
    r = await hard_delete(c, "acme", subject="u")
    stored = await c.aux.get_receipt("acme", r.id)
    assert stored["counts"]["memories"] == 1 and stored["requested"] == {"subject": "u"}
    assert await c.aux.get_receipt("globex", r.id) is None


async def test_every_mutation_writes_an_audit_row_without_memory_text(c, admin):
    await ingest(c, "Budget is 50k")
    (m,) = await list_memories(c, S)
    await edit_memory(c, "acme", m.id, "Budget is 55k")
    await hard_delete(c, "acme", subject="u")
    rows = await admin.fetch("SELECT action, detail::text AS d FROM audit_log ORDER BY id")
    assert [r["action"] for r in rows] == ["memory.edit", "delete"]
    assert not any("Budget" in r["d"] for r in rows)


async def test_export_contains_history(c):
    await ingest(c, "Budget is 50k")
    await pipeline.add(c, S, "Budget is 80k", observed_at=datetime(2026, 3, 1, tzinfo=UTC))
    await c.queue.join()
    out = await export_scope(c, S)
    assert {m["text"] for m in out["memories"]} == {"Budget is 50k", "Budget is 80k"}
    assert any(m["valid_to"] for m in out["memories"])


async def test_deleted_memory_is_gone_from_search_after_delete(c):
    await ingest(c, "Budget is 50k")
    (m,) = await list_memories(c, S)
    await hard_delete(c, "acme", memory_id=m.id)
    assert (await search(c, S, "budget", include_history=True)).results == []
