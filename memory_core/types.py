from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Kind(StrEnum):
    fact = "fact"
    preference = "preference"
    decision = "decision"
    episode = "episode"
    procedure = "procedure"
    relationship = "relationship"


class DocStatus(StrEnum):
    queued = "queued"
    extracting = "extracting"
    indexed = "indexed"
    failed = "failed"


class Verdict(StrEnum):
    new = "new"
    duplicate = "duplicate"
    updates = "updates"
    contradicts = "contradicts"


@dataclass(frozen=True)
class Scope:
    tenant: str
    subject: str
    container: str | None = None

    def __post_init__(self):
        if not self.tenant or not self.tenant.strip():
            raise ValueError("Scope.tenant is required")
        if not self.subject or not self.subject.strip():
            raise ValueError("Scope.subject is required")
        if any("\x00" in (v or "") for v in (self.tenant, self.subject, self.container)):
            raise ValueError("Scope fields must not contain NUL bytes")


@dataclass(frozen=True)
class Principal:
    tenant: str
    subject: str | None = None  # None: the caller picks the subject per request
    scopes: tuple[str, ...] = ("read", "write")


@dataclass
class Candidate:
    text: str
    kind: Kind
    entities: list[str] = field(default_factory=list)
    confidence: float = 0.8
    valid_from: datetime | None = None


@dataclass
class Memory:
    id: str
    scope: Scope
    text: str
    kind: Kind
    entities: list[str]
    confidence: float
    importance: float
    valid_from: datetime
    valid_to: datetime | None
    supersedes: list[str]
    source_doc_ids: list[str]
    extractor_version: str
    created_at: datetime
    last_accessed: datetime | None = None
    meta: dict = field(default_factory=dict)
    access_count: int = 0
    archived_at: datetime | None = None  # cold and low value: kept, but out of current search


@dataclass
class Document:
    id: str
    scope: Scope
    content: str
    hash: str
    status: DocStatus
    created_at: datetime
    meta: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class Chunk:
    id: str
    scope: Scope
    document_id: str
    position: int
    text: str
    observed_at: datetime
    meta: dict = field(default_factory=dict)


@dataclass
class ChunkHit:
    chunk: Chunk
    score: float


@dataclass
class Hit:
    memory: Memory
    score: float


@dataclass
class SearchResult:
    context: str
    results: list[Hit]
    tokens: int
    chunks: list[ChunkHit] = field(default_factory=list)  # verbatim passages that fit the budget


@dataclass
class DeletionReceipt:
    id: str
    counts: dict
    at: datetime
