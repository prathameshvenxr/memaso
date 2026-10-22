from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from ..types import Chunk, Scope
from .pg_base import Pg, redaction_pattern, vec
from .pg_memstore import _dt


def _chunk(r) -> Chunk:
    return Chunk(id=r["id"], scope=Scope(r["tenant"], r["subject"], r["container"]), document_id=r["document_id"],
                 position=r["position"], text=r["text"], observed_at=r["observed_at"], meta=json.loads(r["meta"]))


def _where(scope: Scope, filters: dict | None, as_of: datetime | None, params: list):
    f = filters or {}

    def p(v) -> str:
        params.append(v)
        return f"${len(params)}"

    conds = [f"k.tenant = {p(scope.tenant)}", f"k.subject = {p(scope.subject)}"]
    if f.get("container"):  # an explicit filter is exact
        if not isinstance(f["container"], str):
            raise ValueError("filter container must be a string")
        conds.append(f"k.container = {p(f['container'])}")
    elif scope.container:  # an account scope also sees what was stored for no account in particular
        conds.append(f"(k.container = {p(scope.container)} OR k.container IS NULL)")
    if f.get("created_after"):
        conds.append(f"k.observed_at >= {p(_dt(f['created_after'], 'created_after'))}")
    if f.get("created_before"):
        conds.append(f"k.observed_at < {p(_dt(f['created_before'], 'created_before'))}")
    if f.get("meta"):
        if not isinstance(f["meta"], dict):
            raise ValueError("filter meta must be an object")
        conds.append(f"k.meta @> {p(json.dumps(f['meta']))}::jsonb")
    if as_of is not None:
        at = p(as_of)
        conds.append(f"k.observed_at <= {at}")
        live = f"(m.valid_from <= {at} AND (m.valid_to IS NULL OR m.valid_to > {at}))"
    elif f.get("include_history"):
        return " AND ".join(conds)
    else:
        now = p(datetime.now(UTC))
        live = f"(m.valid_to IS NULL OR m.valid_to > {now})"
    # A chunk tied to a model's paraphrase (non-exact link) retires with that fact. A chunk with exact links stays:
    # search redacts the superseded spans instead (see retired_spans), so the still-true rest is never hidden.
    conds.append(
        "(NOT EXISTS (SELECT 1 FROM memory_chunks mc WHERE mc.chunk_id = k.id AND NOT mc.exact) OR EXISTS "
        f"(SELECT 1 FROM memory_chunks mc JOIN memories m ON m.id = mc.memory_id WHERE mc.chunk_id = k.id AND {live}))")
    return " AND ".join(conds)


class PgChunkStore:
    def __init__(self, pg: Pg, dim: int):
        self.pg, self.dim = pg, dim

    def _check(self, v: list[float]) -> None:
        if len(v) != self.dim:
            raise ValueError(f"vector has {len(v)} dims, store configured for {self.dim}")

    async def insert_many(self, chunks: list[Chunk], vectors: list[list[float]], model_id: str) -> None:
        for v in vectors:
            self._check(v)
        if not chunks:
            return
        async with self.pg.tx(chunks[0].scope.tenant) as c:
            for ch, v in zip(chunks, vectors):
                s = ch.scope
                await c.execute(
                    "INSERT INTO chunks(id,tenant,subject,container,document_id,position,text,observed_at,meta,"
                    "model_id,vector) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10,$11::vector) ON CONFLICT DO NOTHING",
                    ch.id, s.tenant, s.subject, s.container, ch.document_id, ch.position, ch.text, ch.observed_at,
                    json.dumps(ch.meta), model_id, vec(v))

    async def link(self, tenant: str, memory_id: str, document_id: str, fact_text: str,
                   vector: list[float] | None = None, model_id: str | None = None) -> int:
        """Tie a fact to the chunk(s) of its source document whose text contains it. A model's paraphrase matches
        no text, so then the document's chunk nearest to the fact's vector is used."""
        async with self.pg.tx(tenant) as c:
            r = await c.execute(
                "INSERT INTO memory_chunks(memory_id, chunk_id, tenant) SELECT $1, id, tenant FROM chunks "
                "WHERE tenant=$2 AND document_id=$3 AND text ~* $4 ON CONFLICT DO NOTHING",
                memory_id, tenant, document_id, redaction_pattern(fact_text))
            n = int(r.split()[-1])
            if n == 0 and vector is not None and model_id is not None:
                self._check(vector)
                r = await c.execute(
                    "INSERT INTO memory_chunks(memory_id, chunk_id, tenant, exact) SELECT $1, id, tenant, false "
                    "FROM chunks WHERE tenant=$2 AND document_id=$3 AND model_id=$4 "
                    "ORDER BY vector <=> $5::vector LIMIT 1 ON CONFLICT DO NOTHING",
                    memory_id, tenant, document_id, model_id, vec(vector))
                n = int(r.split()[-1])
            return n

    async def redact(self, tenant: str, doc_ids: list[str], text: str) -> int:
        if not doc_ids or not text.strip():
            return 0
        async with self.pg.tx(tenant) as c:
            r = await c.execute("UPDATE chunks SET text = regexp_replace(text, $3, '[removed]', 'gi') "
                                "WHERE tenant=$1 AND document_id = ANY($2)", tenant, doc_ids, redaction_pattern(text))
            return int(r.split()[-1])

    async def retired_spans(self, tenant: str, chunk_ids: list[str], as_of: datetime | None = None) -> dict[str, list[str]]:
        """Texts of facts, linked by their exact words to these chunks, that are no longer current (as of `as_of`,
        default now). Search redacts them so a stale value does not read as current."""
        if not chunk_ids:
            return {}
        at = as_of or datetime.now(UTC)
        async with self.pg.tx(tenant) as c:
            rows = await c.fetch(
                "SELECT mc.chunk_id, m.text FROM memory_chunks mc JOIN memories m ON m.id = mc.memory_id "
                "WHERE mc.tenant=$1 AND mc.chunk_id = ANY($2) AND mc.exact AND NOT "
                "(m.valid_from <= $3 AND (m.valid_to IS NULL OR m.valid_to > $3))", tenant, chunk_ids, at)
        out: dict[str, list[str]] = {}
        for r in rows:
            out.setdefault(r["chunk_id"], []).append(r["text"])
        return out

    async def get_many(self, tenant: str, ids: list[str]) -> list[Chunk]:
        if not ids:
            return []
        async with self.pg.tx(tenant) as c:
            rows = await c.fetch("SELECT * FROM chunks k WHERE k.tenant=$1 AND k.id = ANY($2)", tenant, ids)
        by = {r["id"]: _chunk(r) for r in rows}
        return [by[i] for i in ids if i in by]

    async def query(self, scope: Scope, vector: list[float], model_id: str, k: int, filters: dict | None = None,
                    as_of: datetime | None = None) -> list[tuple[str, float]]:
        self._check(vector)
        params: list = [vec(vector), model_id]
        where = _where(scope, filters, as_of, params)
        params.append(k)
        async with self.pg.tx(scope.tenant) as c:
            rows = await c.fetch(
                f"SELECT k.id, 1 - (k.vector <=> $1::vector) AS score FROM chunks k "
                f"WHERE k.model_id = $2 AND {where} ORDER BY k.vector <=> $1::vector LIMIT ${len(params)}", *params)
        return [(r["id"], float(r["score"])) for r in rows]

    async def keyword_query(self, scope: Scope, text: str, k: int, filters: dict | None = None,
                            as_of: datetime | None = None) -> list[tuple[str, float]]:
        terms = list(dict.fromkeys(re.findall(r"\w+", text.lower())))[:20]
        if not terms:
            return []
        params: list = [" | ".join(terms)]
        where = _where(scope, filters, as_of, params)
        params.append(k)
        async with self.pg.tx(scope.tenant) as c:
            rows = await c.fetch(
                f"SELECT k.id, ts_rank(k.tsv, q) AS score FROM chunks k, to_tsquery('english', $1) q "
                f"WHERE k.tsv @@ q AND {where} ORDER BY score DESC LIMIT ${len(params)}", *params)
        return [(r["id"], float(r["score"])) for r in rows]

    async def missing_vectors(self, tenant: str, model_id: str, limit: int) -> list[Chunk]:
        async with self.pg.tx(tenant) as c:
            rows = await c.fetch("SELECT * FROM chunks k WHERE k.tenant=$1 AND k.model_id IS DISTINCT FROM $2 "
                                 "LIMIT $3", tenant, model_id, limit)
        return [_chunk(r) for r in rows]

    async def set_vector(self, tenant: str, id: str, vector: list[float], model_id: str) -> None:
        self._check(vector)
        async with self.pg.tx(tenant) as c:
            await c.execute("UPDATE chunks SET vector=$3::vector, model_id=$4 WHERE tenant=$1 AND id=$2",
                            tenant, id, vec(vector), model_id)


class PgChunkKeywordIndex:
    """Adapter exposing the chunk full-text query under the KeywordIndex port (`query`)."""

    def __init__(self, store: PgChunkStore):
        self.store = store

    async def query(self, scope, text, k, filters=None, as_of=None):
        return await self.store.keyword_query(scope, text, k, filters, as_of)
