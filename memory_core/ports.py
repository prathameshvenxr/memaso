"""Ports: the only things core logic is allowed to depend on. Adapters implement them."""
from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable, Protocol

from .types import Document, DocStatus, Memory, Principal, Scope


class LLM(Protocol):
    async def complete(self, messages: list[dict], schema: dict) -> dict:
        """Return a JSON object valid for `schema`. Raise LLMSchemaError if it cannot."""


class Embedder(Protocol):
    model_id: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class DocStore(Protocol):
    async def put(self, doc: Document) -> Document:
        """Insert; if (tenant, hash) exists return the existing document instead."""

    async def get(self, tenant: str, id: str) -> Document | None: ...
    async def set_status(self, tenant: str, id: str, status: DocStatus, error: str | None = None) -> None: ...
    async def delete_where(self, tenant: str, subject: str | None = None, doc_id: str | None = None) -> int: ...
    async def delete_orphans(self, tenant: str, doc_ids: list[str]) -> int:
        """Remove those documents that no memory cites any more."""

    async def pending_before(self, tenant: str, subject: str, created_at, exclude_id: str) -> list[str]:
        """Ids of the subject's queued/extracting documents observed before `created_at`, oldest first."""

    async def redact(self, tenant: str, doc_ids: list[str], text: str) -> int:
        """Replace `text` (case and whitespace insensitive) inside these documents with '[removed]'."""

    async def latest(self, tenant: str, subject: str) -> str | None:
        """Id of the most recent document of a subject (by observation time)."""

    async def pending(self, tenant: str) -> list[str]:
        """Ids of documents stuck queued/extracting (e.g. after a restart)."""


class MemoryStore(Protocol):
    async def insert(self, mem: Memory, vector: list[float], model_id: str,
                     supersede: tuple[str, str, str | None, datetime] | None = None) -> None:
        """Insert `mem` and, in the same transaction, link it over `supersede=(old_id, verdict, reason, close_at)`
        and close the old memory's validity at `close_at`."""

    async def get(self, tenant: str, id: str) -> Memory | None: ...
    async def get_many(self, tenant: str, ids: list[str]) -> list[Memory]: ...
    async def list(self, scope: Scope, limit: int, offset: int, include_history: bool = False) -> list[Memory]: ...
    async def bump_confidence(self, tenant: str, id: str, source_doc_id: str) -> None: ...
    async def set_vector(self, tenant: str, id: str, vector: list[float], model_id: str) -> None: ...
    async def missing_vectors(self, tenant: str, model_id: str, limit: int) -> list[Memory]: ...
    async def delete_where(
        self, tenant: str, memory_id: str | None = None, doc_id: str | None = None, subject: str | None = None,
        reopen: bool = False
    ) -> int: ...
    async def touch(self, tenant: str, ids: list[str]) -> None: ...


class VectorStore(Protocol):
    async def query(
        self, scope: Scope, vector: list[float], model_id: str, k: int, filters: dict | None, as_of: datetime | None
    ) -> list[tuple[str, float]]: ...


class KeywordIndex(Protocol):
    async def query(
        self, scope: Scope, text: str, k: int, filters: dict | None, as_of: datetime | None
    ) -> list[tuple[str, float]]: ...


Handler = Callable[[dict], Awaitable[None]]
DeadHandler = Callable[[dict, Exception], Awaitable[None]]


class Queue(Protocol):
    async def enqueue(self, job: dict) -> None: ...
    async def start(self, handler: Handler, on_dead: DeadHandler | None = None) -> None:
        """Begin consuming. A handler exception means retry; after max attempts on_dead(job, exc) is called."""

    async def join(self) -> None:
        """Resolve once every enqueued job, retries included, is finished."""

    async def close(self) -> None: ...


class Parser(Protocol):
    def parse(self, data: bytes, mime: str) -> str: ...


class Auth(Protocol):
    async def resolve(self, headers: dict) -> Principal:
        """Raise AuthError if the credentials are missing or invalid."""


class ChunkStore(Protocol):
    """Verbatim layer: scope-tagged passages with their own vectors."""

    async def insert_many(self, chunks: list, vectors: list[list[float]], model_id: str) -> None: ...
    async def redact(self, tenant: str, doc_ids: list[str], text: str) -> int: ...
    async def retired_spans(self, tenant: str, chunk_ids: list[str], as_of: datetime | None = None) -> dict[str, list[str]]: ...
    async def link(self, tenant: str, memory_id: str, document_id: str, fact_text: str,
                   vector: list[float] | None = None, model_id: str | None = None) -> int: ...
    async def get_many(self, tenant: str, ids: list[str]) -> list: ...
    async def query(self, scope: Scope, vector: list[float], model_id: str, k: int, filters: dict | None,
                    as_of: datetime | None) -> list[tuple[str, float]]: ...
    async def keyword_query(self, scope: Scope, text: str, k: int, filters: dict | None,
                            as_of: datetime | None) -> list[tuple[str, float]]: ...
    async def missing_vectors(self, tenant: str, model_id: str, limit: int) -> list: ...
    async def set_vector(self, tenant: str, id: str, vector: list[float], model_id: str) -> None: ...


class Aux(Protocol):
    """Operational records kept next to the data: audit, receipts, dead letters, usage, api keys."""

    async def audit(self, tenant: str, actor: str, action: str, target: str | None = None,
                    detail: dict | None = None) -> None: ...
    async def receipt(self, tenant: str, id: str, requested: dict, counts: dict) -> datetime: ...
    async def get_receipt(self, tenant: str, id: str) -> dict | None: ...
    async def dead_letter(self, tenant: str, job: dict, error: str) -> None: ...
    async def add_usage(self, tenant: str, tokens: int) -> None: ...
    async def month_usage(self, tenant: str) -> int: ...
    async def budget(self, tenant: str) -> int | None: ...
    async def tenants(self) -> list[str]: ...
    async def principal_for(self, key_hash: str) -> Principal | None: ...
