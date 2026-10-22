"""Postgres + pgvector store bundle. A third-party store plugs in with `store.adapter: "pkg.module:build"` and a
function of the same shape: build(store_cfg: dict, dim: int) -> StoreBundle."""
from __future__ import annotations

from dataclasses import dataclass

from .pg_aux import PgAux
from .pg_base import Pg
from .pg_chunkstore import PgChunkKeywordIndex, PgChunkStore
from .pg_docstore import PgDocStore
from .pg_memstore import PgKeywordIndex, PgMemStore


@dataclass
class StoreBundle:
    lifecycle: object   # start() / close()
    docs: object        # DocStore
    mems: object        # MemoryStore
    vectors: object     # VectorStore
    keywords: object    # KeywordIndex
    chunks: object      # ChunkStore (verbatim layer; also serves as its own vector index)
    chunk_keywords: object  # KeywordIndex over chunks
    aux: object         # Aux
    lock: object        # lock(tenant, subject): async context manager, a cross-process mutex


def build(store_cfg: dict, dim: int) -> StoreBundle:
    if not store_cfg.get("dsn"):
        raise ValueError("store.dsn is required")
    pg = Pg(store_cfg["dsn"])
    mems = PgMemStore(pg, dim=dim)
    chunks = PgChunkStore(pg, dim=dim)
    return StoreBundle(lifecycle=pg, docs=PgDocStore(pg), mems=mems, vectors=mems, keywords=PgKeywordIndex(mems),
                       chunks=chunks, chunk_keywords=PgChunkKeywordIndex(chunks), aux=PgAux(pg),
                       lock=pg.subject_lock)
