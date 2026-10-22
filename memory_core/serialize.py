from __future__ import annotations

from datetime import datetime

from .types import DeletionReceipt, Document, Hit, Memory, SearchResult


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def memory_to_dict(m: Memory) -> dict:
    return {"id": m.id, "tenant": m.scope.tenant, "subject": m.scope.subject, "container": m.scope.container, "text": m.text,
            "kind": m.kind.value, "entities": m.entities, "confidence": m.confidence, "importance": m.importance,
            "valid_from": _iso(m.valid_from), "valid_to": _iso(m.valid_to), "supersedes": m.supersedes,
            "source_doc_ids": m.source_doc_ids, "extractor_version": m.extractor_version,
            "created_at": _iso(m.created_at), "last_accessed": _iso(m.last_accessed), "meta": m.meta}


def doc_to_dict(d: Document) -> dict:
    return {"id": d.id, "status": d.status.value, "error": d.error, "created_at": _iso(d.created_at),
            "subject": d.scope.subject, "container": d.scope.container}


def hit_to_dict(h: Hit) -> dict:
    return {"score": h.score, **memory_to_dict(h.memory)}


def chunk_hit_to_dict(h) -> dict:
    k = h.chunk
    return {"score": h.score, "id": k.id, "tenant": k.scope.tenant, "subject": k.scope.subject,
            "container": k.scope.container, "document_id": k.document_id, "position": k.position, "text": k.text,
            "observed_at": _iso(k.observed_at)}


def result_to_dict(r: SearchResult) -> dict:
    return {"context": r.context, "tokens": r.tokens, "results": [hit_to_dict(h) for h in r.results],
            "chunks": [chunk_hit_to_dict(h) for h in r.chunks]}


def receipt_to_dict(r: DeletionReceipt) -> dict:
    return {"id": r.id, "counts": r.counts, "at": _iso(r.at)}
