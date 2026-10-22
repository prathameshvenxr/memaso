from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

from ..metering import current_tenant
from ..types import Chunk, DocStatus, Document, Scope
from . import truth
from .chunk import chunk
from .dedup import normalize
from .extract import extract
from .rules import extract_rules

log = logging.getLogger("memaso.pipeline")
MAX_CONTENT = 200_000  # chars per document


def flatten(content: str | list[dict] | dict) -> str:
    """Text as-is; chat messages as 'role: text' lines; any other structured event as sorted JSON."""
    if isinstance(content, str):
        return content.strip()
    lines = []
    for m in [content] if isinstance(content, dict) else content:
        if not m:
            continue
        if isinstance(m, dict) and "content" in m:
            body = str(m["content"]).strip()
            if body:
                lines.append(f"{m.get('role', 'user')}: {body}")
        else:
            lines.append(json.dumps(m, sort_keys=True, ensure_ascii=False))
    return "\n".join(lines)


async def add(c, scope: Scope, content: str | list[dict] | dict, metadata: dict | None = None,
              idempotency_key: str | None = None, observed_at: datetime | None = None) -> Document:
    text = flatten(content)
    if not text:
        raise ValueError("content is empty")
    if "\x00" in text or "\\u0000" in json.dumps(metadata or {}):  # json.dumps escapes NUL as \u0000
        raise ValueError("content and metadata must not contain NUL bytes")
    if len(text) > MAX_CONTENT:
        raise ValueError(f"content is longer than {MAX_CONTENT} characters")
    body = idempotency_key if idempotency_key else " ".join(text.split())
    key = f"{scope.tenant}\x1f{scope.subject}\x1f{scope.container}\x1f{body}"  # always scoped: keys never collide
    doc = Document(id=str(uuid.uuid4()), scope=scope, content=text, hash=hashlib.sha256(key.encode()).hexdigest(),
                   status=DocStatus.queued, created_at=observed_at or datetime.now(UTC), meta=metadata or {})
    stored = await c.docs.put(doc)
    if stored.id == doc.id:  # a new document
        await c.queue.enqueue({"tenant": scope.tenant, "doc_id": doc.id})
        return stored
    # Same content seen before. With a caller key that is idempotency. Without one, repeating the *latest*
    # statement is a repeat, but repeating an older one after something newer ("50k", "80k", "50k") is a new
    # assertion and must be processed again, or the stale value would win.
    if not idempotency_key and (await c.docs.latest(scope.tenant, scope.subject) != stored.id
                                or " ".join(stored.content.split()) != " ".join(text.split())):
        # ^ the stored text differs: part of it was forgotten (redacted), so this add is new information
        again = Document(id=str(uuid.uuid4()), scope=scope, content=text,
                         hash=hashlib.sha256((doc.hash + doc.id).encode()).hexdigest(), status=DocStatus.queued,
                         created_at=doc.created_at, meta=doc.meta)
        stored = await c.docs.put(again)
        await c.queue.enqueue({"tenant": scope.tenant, "doc_id": stored.id})
        return stored
    if stored.status == DocStatus.failed:  # adding it again is a retry
        await c.docs.set_status(scope.tenant, stored.id, DocStatus.queued)
        stored.status = DocStatus.queued
    if stored.status != DocStatus.indexed:  # also covers an enqueue that was lost: process() is idempotent
        await c.queue.enqueue({"tenant": scope.tenant, "doc_id": stored.id})
    return stored


async def store_chunks(c, doc: Document) -> None:
    """Layer A: the document's own words, in ~800-character passages, embedded locally. No model call."""
    if not c.cfg.ingest["verbatim"] or c.chunks is None:
        return
    parts = chunk(doc.content, int(c.cfg.ingest["chunk_chars"]))
    vecs = await c.embedder.embed(parts)
    rows = [Chunk(id=hashlib.sha256(f"{doc.id}:{i}".encode()).hexdigest(), scope=doc.scope, document_id=doc.id,
                  position=i, text=part, observed_at=doc.created_at, meta=doc.meta) for i, part in enumerate(parts)]
    await c.chunks.insert_many(rows, vecs, c.embedder.model_id)


async def candidates(c, doc: Document):
    """Layer B input. rules: no model. llm: the configured model, one call per ~4000 characters. none: skip."""
    mode = c.cfg.facts["extract"]
    if mode == "none" or doc.meta.get("archived"):  # archived turns were already mined: keep their words only
        return [], "none"
    if mode == "rules":
        return extract_rules(doc.content), "rules-v1"
    cands, seen, version = [], set(), "v1"
    for part in chunk(doc.content):
        got, version = await extract(c.llm, part, doc.created_at)
        for x in got:
            if normalize(x.text) not in seen:
                seen.add(normalize(x.text))
                cands.append(x)
    return cands, version


async def _run(c, tenant: str, doc: Document) -> None:
    """One document through the pipeline. The caller holds the subject lock."""
    await c.docs.set_status(tenant, doc.id, DocStatus.extracting)
    await store_chunks(c, doc)
    cands, version = await candidates(c, doc)
    vecs = await c.embedder.embed([x.text for x in cands]) if cands else []
    for cand, vec in zip(cands, vecs):  # sequential on purpose: later candidates see earlier ones
        decision = await truth.resolve(cand, vec, doc.scope, c)
        await truth.apply(decision, cand, vec, doc.scope, doc, version, c)
    await c.docs.set_status(tenant, doc.id, DocStatus.indexed)


async def process(c, job: dict) -> None:
    tenant = job["tenant"]
    token = current_tenant.set(tenant)
    try:
        doc = await c.docs.get(tenant, job["doc_id"])
        if doc is None:  # deleted meanwhile
            return
        if tenant not in c._clean:  # after an embedder swap, finish re-embedding before judging against old memories
            if (await c.mems.missing_vectors(tenant, c.embedder.model_id, 1)
                    or (c.chunks is not None and await c.chunks.missing_vectors(tenant, c.embedder.model_id, 1))):
                await reindex(c, tenant)
            c._clean.add(tenant)
        async with c.lock(tenant, doc.scope.subject):
            # Workers do not run in arrival order, so first finish this subject's older pending documents: the
            # newest statement must be applied last, whichever job a worker happened to pick up first.
            for older in await c.docs.pending_before(tenant, doc.scope.subject, doc.created_at, doc.id):
                prev = await c.docs.get(tenant, older)
                if prev is not None and prev.status != DocStatus.indexed:
                    await _run(c, tenant, prev)
            doc = await c.docs.get(tenant, job["doc_id"])  # re-read: another worker may have finished it
            if doc is None or doc.status == DocStatus.indexed:
                return
            await _run(c, tenant, doc)
    finally:
        current_tenant.reset(token)


async def mark_dead(c, job: dict, err: Exception) -> None:
    tenant = job["tenant"]
    await c.docs.set_status(tenant, job["doc_id"], DocStatus.failed, type(err).__name__)
    await c.aux.dead_letter(tenant, job, type(err).__name__)


async def resume_pending(c, tenants: list[str] | None = None) -> int:
    """After a restart the in-process queue is empty: re-enqueue documents left queued or extracting.
    `process` is idempotent, so a double delivery is harmless. Covers provisioned tenants."""
    n = 0
    for t in tenants if tenants is not None else await c.aux.tenants():
        for doc_id in await c.docs.pending(t):
            await c.queue.enqueue({"tenant": t, "doc_id": doc_id})
            n += 1
    return n


async def reindex(c, tenant: str, batch: int = 100) -> int:
    """Embed every memory that has no vector for the current embedder model_id (run after changing embedder)."""
    token = current_tenant.set(tenant)
    done = 0
    try:
        while True:
            todo = await c.mems.missing_vectors(tenant, c.embedder.model_id, batch)
            if todo:
                vecs = await c.embedder.embed([m.text for m in todo])
                for m, v in zip(todo, vecs):
                    await c.mems.set_vector(tenant, m.id, v, c.embedder.model_id)
                done += len(todo)
            old = await c.chunks.missing_vectors(tenant, c.embedder.model_id, batch) if c.chunks is not None else []
            if old:
                vecs = await c.embedder.embed([k.text for k in old])
                for k, v in zip(old, vecs):
                    await c.chunks.set_vector(tenant, k.id, v, c.embedder.model_id)
                done += len(old)
            if not todo and not old:
                return done
    finally:
        current_tenant.reset(token)


async def startup_sweep(c) -> None:
    """Background, best effort: finish interrupted documents and re-embed after an embedder change."""
    try:
        for t in await c.aux.tenants():
            await reindex(c, t)
        await resume_pending(c)
    except Exception:  # noqa: BLE001 - must never take the service down; the next start retries
        log.exception("startup sweep failed")


async def remember(c, scope: Scope, text: str, kind: str = "fact", entities: list[str] | None = None,
                   observed_at: datetime | None = None):
    """Store one fact the caller already extracted (for example from the agent's own `remember` tool call): no
    extraction model call, only an embedding and the usual truth maintenance. Synchronous, no queue.
    Returns the new Memory, or None if it only confirmed an existing one."""
    from ..types import Candidate, Kind

    text = " ".join(str(text).split())
    if not text or "\x00" in text or any("\x00" in e for e in (entities or [])):
        raise ValueError("fact text is empty or contains NUL bytes")
    when = observed_at or datetime.now(UTC)
    doc = await c.docs.put(Document(id=str(uuid.uuid4()), scope=scope, content=text,
                                    hash=hashlib.sha256(f"fact\x1f{uuid.uuid4()}".encode()).hexdigest(),
                                    status=DocStatus.indexed, created_at=when, meta={"source": "remember"}))
    token = current_tenant.set(scope.tenant)
    try:
        async with c.lock(scope.tenant, scope.subject):
            cand = Candidate(text=text, kind=Kind(kind), entities=list(entities or []), confidence=1.0)
            (vec,) = await c.embedder.embed([text])
            decision = await truth.resolve(cand, vec, scope, c)
            return await truth.apply(decision, cand, vec, scope, doc, "remember", c)
    finally:
        current_tenant.reset(token)
