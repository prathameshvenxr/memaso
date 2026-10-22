"""Contract suites: any adapter for a port must pass its suite. Subclass and implement `make()`."""
from __future__ import annotations

import asyncio

import pytest

from memory_core.errors import LLMSchemaError


class EmbedderContract:
    def make(self):  # pragma: no cover
        raise NotImplementedError

    async def test_dim_and_model_id_are_declared(self):
        e = self.make()
        assert e.dim > 0 and e.model_id
        (v,) = await e.embed(["hello"])
        assert len(v) == e.dim

    async def test_batch_order_and_determinism(self):
        e = self.make()
        a, b = await e.embed(["alpha", "beta"]), await e.embed(["alpha", "beta"])
        assert a == b and a[0] != a[1]


class LLMContract:
    """An adapter must return JSON that validates against the real extraction and judge schemas."""

    def make(self):  # pragma: no cover
        raise NotImplementedError

    async def test_extract_output_validates_against_the_extraction_schema(self):
        import jsonschema
        from memory_core.ingest.extract import SCHEMA, SYSTEM
        out = await self.make().complete(
            [{"role": "system", "content": SYSTEM},
             {"role": "user", "content": '{"as_of": "2026-01-01", "content": "user: Our budget is 50k. I prefer weekly reports."}'}],
            SCHEMA)
        jsonschema.validate(out, SCHEMA)

    async def test_judge_output_validates_against_the_judge_schema(self):
        import jsonschema
        from memory_core.ingest.truth import JUDGE_SCHEMA, JUDGE_SYSTEM
        out = await self.make().complete(
            [{"role": "system", "content": JUDGE_SYSTEM},
             {"role": "user", "content": '{"candidate": "Budget is 80k", "neighbours": [{"id": "m1", "text": "Budget is 50k"}]}'}],
            JUDGE_SCHEMA)
        jsonschema.validate(out, JUDGE_SCHEMA)


class QueueContract:
    def make(self):  # pragma: no cover
        raise NotImplementedError

    async def test_delivers_every_job(self):
        q, seen = self.make(), []

        async def h(job):
            seen.append(job["n"])

        await q.start(h)
        for n in range(5):
            await q.enqueue({"n": n})
        await q.join()
        await q.close()
        assert sorted(seen) == [0, 1, 2, 3, 4]

    async def test_failing_handler_is_retried_then_declared_dead(self):
        q, attempts, dead = self.make(), [], []

        async def h(job):
            attempts.append(1)
            raise RuntimeError("boom")

        async def on_dead(job, err):
            dead.append((job, type(err).__name__))

        await q.start(h, on_dead)
        await q.enqueue({"n": 1})
        await q.join()
        await q.close()
        assert len(attempts) == q.max_attempts and dead == [({"n": 1}, "RuntimeError")]

    async def test_transient_failure_succeeds_on_retry(self):
        q, calls = self.make(), []

        async def h(job):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("once")

        await q.start(h)
        await q.enqueue({"n": 1})
        await q.join()
        await q.close()
        assert len(calls) == 2


# ---------------------------------------------------------------- store contracts
import uuid  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

from memory_core.errors import AuthError  # noqa: E402
from memory_core.types import Chunk, DocStatus, Document, Kind, Memory, Scope  # noqa: E402

NOW = datetime.now(UTC)
A, B = Scope("tenant-a", "u"), Scope("tenant-b", "u")


def _doc(scope=A, text="hello", status=DocStatus.queued, when=None, key=None) -> Document:
    return Document(id=str(uuid.uuid4()), scope=scope, content=text, hash=key or str(uuid.uuid4()), status=status,
                    created_at=when or NOW)


def _mem(text, scope=A, valid_from=None, docs=()) -> Memory:
    return Memory(id=str(uuid.uuid4()), scope=scope, text=text, kind=Kind.fact, entities=[], confidence=0.9,
                  importance=0.5, valid_from=valid_from or NOW - timedelta(days=1), valid_to=None, supersedes=[],
                  source_doc_ids=list(docs), extractor_version="t", created_at=NOW)


class StoreContract:
    """Subclass and provide an async fixture `store` yielding a bundle with docs, mems, vectors, keywords, chunks,
    aux (and dim 8 vectors via `vec`). Any store adapter must pass every test here."""

    @staticmethod
    def vec(*hot: int, dim: int = 8) -> list[float]:
        v = [0.0] * dim
        for i in hot:
            v[i] = 1.0
        return v

    # --- DocStore
    async def test_doc_put_get_and_idempotent_on_tenant_hash(self, store):
        d = _doc(key="same")
        assert (await store.docs.put(d)).id == d.id
        assert (await store.docs.put(_doc(key="same"))).id == d.id
        assert (await store.docs.get("tenant-a", d.id)).content == "hello"
        assert await store.docs.get("tenant-b", d.id) is None
        assert (await store.docs.put(_doc(B, key="same"))).id != d.id  # the same hash in another tenant is separate

    async def test_doc_status_pending_latest(self, store):
        old, new = _doc(when=NOW - timedelta(days=2)), _doc(when=NOW - timedelta(days=1))
        await store.docs.put(old)
        await store.docs.put(new)
        assert await store.docs.latest("tenant-a", "u") == new.id
        await store.docs.set_status("tenant-a", old.id, DocStatus.indexed)
        assert await store.docs.pending("tenant-a") == [new.id]

    async def test_delete_orphans_spares_documents_still_cited(self, store):
        cited, loose = _doc(), _doc()
        await store.docs.put(cited)
        await store.docs.put(loose)
        await store.mems.insert(_mem("x", docs=[cited.id]), self.vec(0), "m")
        assert await store.docs.delete_orphans("tenant-a", [cited.id, loose.id]) == 1
        assert await store.docs.get("tenant-a", cited.id) is not None

    # --- MemoryStore + VectorStore + KeywordIndex
    async def test_memory_roundtrip_and_tenant_wall(self, store):
        m = _mem("budget is 50k")
        await store.mems.insert(m, self.vec(0), "m")
        assert (await store.mems.get("tenant-a", m.id)).text == "budget is 50k"
        assert await store.mems.get("tenant-b", m.id) is None
        assert await store.mems.delete_where("tenant-b", memory_id=m.id) == 0

    async def test_supersede_is_atomic_and_deleting_never_resurrects_unless_asked(self, store):
        old, new = _mem("budget is 50k"), _mem("budget is 80k")
        await store.mems.insert(old, self.vec(0), "m")
        await store.mems.insert(new, self.vec(0), "m", (old.id, "updates", "r", NOW))
        assert [m.text for m in await store.mems.list(A, 10, 0)] == ["budget is 80k"]
        assert len(await store.mems.list(A, 10, 0, include_history=True)) == 2
        await store.mems.delete_where("tenant-a", memory_id=new.id)
        assert await store.mems.list(A, 10, 0) == []          # forgetting a value never brings the old one back
        # an explicit rollback can: re-create the situation and delete with reopen=True
        newer = _mem("budget is 90k")
        await store.mems.insert(newer, self.vec(0), "m", (old.id, "updates", "r", NOW))
        await store.mems.delete_where("tenant-a", memory_id=newer.id, reopen=True)
        assert [m.text for m in await store.mems.list(A, 10, 0)] == ["budget is 50k"]

    async def test_vector_and_keyword_queries_respect_scope_model_and_time(self, store):
        m = _mem("budget is 50k")
        await store.mems.insert(m, self.vec(0), "m1")
        assert [i for i, _ in await store.vectors.query(A, self.vec(0), "m1", 5, None, None)] == [m.id]
        assert await store.vectors.query(A, self.vec(0), "other-model", 5, None, None) == []
        assert await store.vectors.query(B, self.vec(0), "m1", 5, None, None) == []
        assert [i for i, _ in await store.keywords.query(A, "budget", 5, None, None)] == [m.id]
        assert await store.keywords.query(A, "budget", 5, None, NOW - timedelta(days=5)) == []

    async def test_wrong_dimension_is_refused(self, store):
        with pytest.raises(ValueError):
            await store.mems.insert(_mem("x"), [0.1, 0.2], "m")

    # --- ChunkStore
    async def test_superseded_exact_facts_are_reported_as_spans_and_paraphrase_links_retire_the_chunk(self, store):
        d = _doc()
        await store.docs.put(d)
        ck = Chunk(id="c1", scope=A, document_id=d.id, position=0, text="Budget is 50k and more",
                   observed_at=NOW - timedelta(days=3))
        await store.chunks.insert_many([ck], [self.vec(1)], "m")
        old, new = _mem("Budget is 50k", valid_from=NOW - timedelta(days=3), docs=[d.id]), _mem("Budget is 80k")
        await store.mems.insert(old, self.vec(0), "m")
        assert await store.chunks.link("tenant-a", old.id, d.id, "Budget is 50k") == 1       # exact: words found
        got = lambda f=None, as_of=None: store.chunks.query(A, self.vec(1), "m", 5, f, as_of)
        assert [i for i, _ in await got()] == ["c1"]
        await store.mems.insert(new, self.vec(0), "m", (old.id, "updates", "r", NOW))
        assert [i for i, _ in await got()] == ["c1"]                                         # stays visible...
        assert await store.chunks.retired_spans("tenant-a", ["c1"]) == {"c1": ["Budget is 50k"]}  # ...with a span to redact
        assert await store.chunks.retired_spans("tenant-a", ["c1"], NOW - timedelta(days=2)) == {}
        # a paraphrase tied to the nearest chunk retires the whole chunk once superseded
        para_old = _mem("Monthly budget is 50,000", valid_from=NOW - timedelta(days=3))
        await store.mems.insert(para_old, self.vec(2), "m")
        ck2 = Chunk(id="c2", scope=A, document_id=d.id, position=1, text="fifty thousand", observed_at=NOW - timedelta(days=3))
        await store.chunks.insert_many([ck2], [self.vec(3)], "m")
        assert await store.chunks.link("tenant-a", para_old.id, d.id, "no such words", self.vec(3), "m") == 1
        assert "c2" in [i for i, _ in await store.chunks.query(A, self.vec(3), "m", 5, None, None)]
        await store.mems.insert(_mem("Monthly budget is 80,000"), self.vec(2), "m", (para_old.id, "updates", "r", NOW))
        assert "c2" not in [i for i, _ in await store.chunks.query(A, self.vec(3), "m", 5, None, None)]
        assert [i for i, _ in await store.chunks.query(A, self.vec(3), "m", 5, {"include_history": True}, None)]
        assert await store.chunks.query(B, self.vec(1), "m", 5, None, None) == []


class AuthContract:
    """Subclass and provide `make()` -> (auth, good_headers, expected_tenant)."""

    def make(self):  # pragma: no cover
        raise NotImplementedError

    async def test_valid_credentials_resolve_to_the_tenant(self):
        auth, good, tenant = await self.make()
        assert (await auth.resolve(good)).tenant == tenant

    async def test_header_name_is_case_insensitive(self):
        auth, good, tenant = await self.make()
        assert (await auth.resolve({k.upper(): v for k, v in good.items()})).tenant == tenant

    async def test_missing_or_wrong_credentials_raise_auth_error(self):
        auth, good, _ = await self.make()
        for bad in ({}, {"authorization": ""}, {"authorization": "Bearer nope"}, {"authorization": "Basic abc"}):
            with pytest.raises(AuthError):
                await auth.resolve(bad)
