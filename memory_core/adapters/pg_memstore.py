from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from ..types import Kind, Memory, Scope
from .pg_base import Pg, vec

_SELECT = """
SELECT m.*,
  ARRAY(SELECT supersedes_id FROM memory_links l WHERE l.memory_id = m.id) AS supersedes,
  ARRAY(SELECT document_id FROM memory_sources s WHERE s.memory_id = m.id) AS source_doc_ids
FROM memories m
"""


def _mem(r) -> Memory:
    return Memory(id=r["id"], scope=Scope(r["tenant"], r["subject"], r["container"]), text=r["text"],
                  kind=Kind(r["kind"]), entities=list(r["entities"]), confidence=r["confidence"],
                  importance=r["importance"], valid_from=r["valid_from"], valid_to=r["valid_to"],
                  supersedes=list(r["supersedes"]), source_doc_ids=list(r["source_doc_ids"]),
                  extractor_version=r["extractor_version"], created_at=r["created_at"],
                  last_accessed=r["last_accessed"], meta=json.loads(r["meta"]))


def _dt(v, name: str) -> datetime:
    if isinstance(v, str):
        try:
            v = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"filter {name} is not an ISO 8601 date") from None
    if not isinstance(v, datetime):
        raise ValueError(f"filter {name} must be an ISO 8601 date")
    return v if v.tzinfo else v.replace(tzinfo=UTC)


def _where(scope: Scope, filters: dict | None, as_of: datetime | None, params: list, alias: str = "m"):
    """Shared WHERE for vector, keyword and list queries. `params` is extended in place."""
    f = filters or {}
    a = alias

    def p(v) -> str:
        params.append(v)
        return f"${len(params)}"

    conds = [f"{a}.tenant = {p(scope.tenant)}", f"{a}.subject = {p(scope.subject)}"]
    if "container_is" in f:  # exact: a string, or None meaning "no container"
        conds.append(f"{a}.container = {p(f['container_is'])}" if f["container_is"] is not None
                     else f"{a}.container IS NULL")
    else:
        if f.get("container"):  # an explicit filter is exact
            if not isinstance(f["container"], str):
                raise ValueError("filter container must be a string")
            conds.append(f"{a}.container = {p(f['container'])}")
        elif scope.container:  # an account scope also sees what was stored for no account in particular
            conds.append(f"({a}.container = {p(scope.container)} OR {a}.container IS NULL)")
    if as_of is not None:
        conds.append(f"{a}.valid_from <= {p(as_of)} AND ({a}.valid_to IS NULL OR {a}.valid_to > {p(as_of)})")
    elif not f.get("include_history"):
        now = datetime.now(UTC)
        conds.append(f"({a}.valid_to IS NULL OR {a}.valid_to > {p(now)})")
        if not f.get("include_future"):  # a fact scheduled for later is not current yet
            conds.append(f"{a}.valid_from <= {p(now)}")
    if f.get("kind"):
        kinds = f["kind"]
        if isinstance(kinds, str):
            kinds = [kinds]
        elif not isinstance(kinds, (list, tuple)):
            raise ValueError("filter kind must be a string or a list of strings")
        conds.append(f"{a}.kind = ANY({p([Kind(k).value for k in kinds])})")
    if f.get("entity"):
        if not isinstance(f["entity"], str):
            raise ValueError("filter entity must be a string")
        conds.append(f"{p(f['entity'])} = ANY({a}.entities)")
    if f.get("min_confidence") is not None:
        try:
            conds.append(f"{a}.confidence >= {p(float(f['min_confidence']))}")
        except (TypeError, ValueError):
            raise ValueError("filter min_confidence must be a number") from None
    if f.get("created_after"):
        conds.append(f"{a}.created_at >= {p(_dt(f['created_after'], 'created_after'))}")
    if f.get("created_before"):
        conds.append(f"{a}.created_at < {p(_dt(f['created_before'], 'created_before'))}")
    if f.get("meta"):
        if not isinstance(f["meta"], dict):
            raise ValueError("filter meta must be an object")
        conds.append(f"{a}.meta @> {p(json.dumps(f['meta']))}::jsonb")
    return " AND ".join(conds)


class PgMemStore:
    """Implements MemoryStore, VectorStore and KeywordIndex over one Postgres pool."""

    def __init__(self, pg: Pg, dim: int):
        self.pg = pg
        self.dim = dim

    def _check_dim(self, vector: list[float]) -> None:
        if len(vector) != self.dim:
            raise ValueError(f"vector has {len(vector)} dims, store configured for {self.dim}")

    async def insert(self, mem: Memory, vector: list[float], model_id: str,
                     supersede: tuple[str, str, str | None, datetime] | None = None) -> None:
        self._check_dim(vector)
        s = mem.scope
        async with self.pg.tx(s.tenant) as c:
            await c.execute(
                "INSERT INTO memories(id,tenant,subject,container,text,kind,entities,confidence,importance,"
                "valid_from,valid_to,extractor_version,meta,created_at) "
                "VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14)",
                mem.id, s.tenant, s.subject, s.container, mem.text, mem.kind.value, mem.entities, mem.confidence,
                mem.importance, mem.valid_from, mem.valid_to, mem.extractor_version, json.dumps(mem.meta),
                mem.created_at)
            for d in mem.source_doc_ids:
                await c.execute("INSERT INTO memory_sources VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                                mem.id, d, s.tenant)
            await c.execute("INSERT INTO embeddings VALUES($1,$2,$3,$4::vector)", mem.id, model_id, s.tenant,
                            vec(vector))
            if supersede:
                old_id, verdict, reason, close_at = supersede
                await c.execute("INSERT INTO memory_links VALUES($1,$2,$3,$4,$5)", mem.id, old_id, s.tenant,
                                verdict, reason)
                await c.execute("UPDATE memories SET valid_to=$3 WHERE tenant=$1 AND id=$2 "
                                "AND (valid_to IS NULL OR valid_to > $3)", s.tenant, old_id, close_at)

    async def get(self, tenant: str, id: str) -> Memory | None:
        got = await self.get_many(tenant, [id])
        return got[0] if got else None

    async def get_many(self, tenant: str, ids: list[str]) -> list[Memory]:
        if not ids:
            return []
        async with self.pg.tx(tenant) as c:
            rows = await c.fetch(_SELECT + " WHERE m.tenant=$1 AND m.id = ANY($2)", tenant, ids)
        by_id = {r["id"]: _mem(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    async def list(self, scope: Scope, limit: int, offset: int, include_history: bool = False) -> list[Memory]:
        params: list = []
        where = _where(scope, {"include_history": include_history}, None, params)
        params += [limit, offset]
        async with self.pg.tx(scope.tenant) as c:
            rows = await c.fetch(
                f"{_SELECT} WHERE {where} ORDER BY m.created_at DESC, m.id LIMIT ${len(params)-1} OFFSET ${len(params)}",
                *params)
        return [_mem(r) for r in rows]

    async def bump_confidence(self, tenant: str, id: str, source_doc_id: str) -> None:
        async with self.pg.tx(tenant) as c:
            await c.execute("UPDATE memories SET confidence = LEAST(1.0, confidence + 0.1) "
                            "WHERE tenant=$1 AND id=$2", tenant, id)
            await c.execute("INSERT INTO memory_sources VALUES($1,$2,$3) ON CONFLICT DO NOTHING",
                            id, source_doc_id, tenant)

    async def set_vector(self, tenant: str, id: str, vector: list[float], model_id: str) -> None:
        self._check_dim(vector)
        async with self.pg.tx(tenant) as c:
            await c.execute("INSERT INTO embeddings VALUES($1,$2,$3,$4::vector) "
                            "ON CONFLICT (memory_id, model_id) DO UPDATE SET vector=EXCLUDED.vector",
                            id, model_id, tenant, vec(vector))

    async def missing_vectors(self, tenant: str, model_id: str, limit: int) -> list[Memory]:
        async with self.pg.tx(tenant) as c:
            rows = await c.fetch(
                _SELECT + " WHERE m.tenant=$1 AND NOT EXISTS (SELECT 1 FROM embeddings e "
                "WHERE e.memory_id=m.id AND e.model_id=$2) LIMIT $3", tenant, model_id, limit)
        return [_mem(r) for r in rows]

    @staticmethod
    async def _reopen(c, tenant: str, gone: list[str]) -> None:
        """Memories being removed may have superseded others: those become current again (unless a different
        surviving memory also supersedes them)."""
        if gone:
            await c.execute(
                "UPDATE memories SET valid_to = NULL WHERE tenant=$1 AND id IN "
                "(SELECT supersedes_id FROM memory_links WHERE tenant=$1 AND memory_id = ANY($2)) "
                "AND NOT EXISTS (SELECT 1 FROM memory_links l WHERE l.supersedes_id = memories.id "
                "AND NOT (l.memory_id = ANY($2)))", tenant, gone)

    async def delete_where(self, tenant: str, memory_id: str | None = None, doc_id: str | None = None,
                           subject: str | None = None, reopen: bool = False) -> int:
        """`reopen=True` makes the predecessors of a removed memory current again (a rollback). The default is
        False: forgetting a value must never bring back the older value it replaced."""
        async with self.pg.tx(tenant) as c:
            if memory_id:
                if reopen:
                    await self._reopen(c, tenant, [memory_id])
                # the verbatim text the fact came from goes with it, even when the document has other facts
                await c.execute("DELETE FROM chunks WHERE tenant=$1 AND id IN "
                                "(SELECT chunk_id FROM memory_chunks WHERE tenant=$1 AND memory_id=$2)",
                                tenant, memory_id)
                r = await c.execute("DELETE FROM memories WHERE tenant=$1 AND id=$2", tenant, memory_id)
                return int(r.split()[-1])
            if doc_id:
                ids = [x["memory_id"] for x in await c.fetch(
                    "SELECT memory_id FROM memory_sources WHERE tenant=$1 AND document_id=$2", tenant, doc_id)]
                await c.execute("DELETE FROM memory_sources WHERE tenant=$1 AND document_id=$2", tenant, doc_id)
                gone = [x["id"] for x in await c.fetch(
                    "SELECT m.id FROM memories m WHERE m.tenant=$1 AND m.id = ANY($2) AND NOT EXISTS "
                    "(SELECT 1 FROM memory_sources s WHERE s.memory_id=m.id)", tenant, ids)]
                if reopen:
                    await self._reopen(c, tenant, gone)
                r = await c.execute("DELETE FROM memories WHERE tenant=$1 AND id = ANY($2)", tenant, gone)
                return int(r.split()[-1])
            if subject:
                r = await c.execute("DELETE FROM memories WHERE tenant=$1 AND subject=$2", tenant, subject)
            else:
                r = await c.execute("DELETE FROM memories WHERE tenant=$1", tenant)
            return int(r.split()[-1])

    async def touch(self, tenant: str, ids: list[str]) -> None:
        if ids:
            async with self.pg.tx(tenant) as c:
                await c.execute("UPDATE memories SET last_accessed=now() WHERE tenant=$1 AND id = ANY($2)",
                                tenant, ids)

    async def query(self, scope: Scope, vector: list[float], model_id: str, k: int, filters: dict | None = None,
                    as_of: datetime | None = None) -> list[tuple[str, float]]:
        self._check_dim(vector)
        params: list = [vec(vector), model_id]
        where = _where(scope, filters, as_of, params)
        params.append(k)
        async with self.pg.tx(scope.tenant) as c:
            rows = await c.fetch(
                f"SELECT m.id, 1 - (e.vector <=> $1::vector) AS score FROM embeddings e "
                f"JOIN memories m ON m.id = e.memory_id WHERE e.model_id = $2 AND {where} "
                f"ORDER BY e.vector <=> $1::vector LIMIT ${len(params)}", *params)
        return [(r["id"], float(r["score"])) for r in rows]

    async def keyword_query(self, scope: Scope, text: str, k: int, filters: dict | None = None,
                            as_of: datetime | None = None) -> list[tuple[str, float]]:
        # OR over the query's words: stopwords vanish in the 'english' config, so a question like
        # "what is the budget for Diwali?" matches on budget/diwali instead of demanding every word.
        terms = list(dict.fromkeys(re.findall(r"\w+", text.lower())))[:20]
        if not terms:
            return []
        params: list = [" | ".join(terms)]
        where = _where(scope, filters, as_of, params)
        params.append(k)
        async with self.pg.tx(scope.tenant) as c:
            rows = await c.fetch(
                f"SELECT m.id, ts_rank(m.tsv, q) AS score FROM memories m, to_tsquery('english', $1) q "
                f"WHERE m.tsv @@ q AND {where} ORDER BY score DESC LIMIT ${len(params)}", *params)
        return [(r["id"], float(r["score"])) for r in rows]


class PgKeywordIndex:
    """Adapter exposing PgMemStore's FTS under the KeywordIndex port (`query`)."""

    def __init__(self, store: PgMemStore):
        self.store = store

    async def query(self, scope, text, k, filters=None, as_of=None):
        return await self.store.keyword_query(scope, text, k, filters, as_of)
