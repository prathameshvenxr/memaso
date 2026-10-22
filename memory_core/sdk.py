from __future__ import annotations

from datetime import datetime

import httpx

from .types import Chunk, ChunkHit, DeletionReceipt, Hit, Kind, Memory, Scope, SearchResult


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def _memory(d: dict) -> Memory:
    return Memory(id=d["id"], scope=Scope(d["tenant"], d["subject"], d["container"]), text=d["text"],
                  kind=Kind(d["kind"]), entities=d["entities"], confidence=d["confidence"],
                  importance=d["importance"], valid_from=_dt(d["valid_from"]), valid_to=_dt(d["valid_to"]),
                  supersedes=d["supersedes"], source_doc_ids=d["source_doc_ids"],
                  extractor_version=d["extractor_version"], created_at=_dt(d["created_at"]),
                  last_accessed=_dt(d["last_accessed"]), meta=d["meta"])


def _result(d: dict) -> SearchResult:
    return SearchResult(context=d["context"], tokens=d["tokens"],
                        results=[Hit(_memory(r), r["score"]) for r in d["results"]],
                        chunks=[ChunkHit(Chunk(id=k["id"], scope=Scope(k["tenant"], k["subject"], k["container"]),
                                               document_id=k["document_id"], position=k["position"], text=k["text"],
                                               observed_at=_dt(k["observed_at"])), k["score"])
                                for k in d.get("chunks", [])])


class MemoryClient:
    """Async client for the REST API. `subject` defaults apply to every call unless overridden."""

    def __init__(self, base_url: str, api_key: str, subject: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None, timeout: float = 30):
        self.subject = subject
        self.http = httpx.AsyncClient(base_url=base_url, headers={"Authorization": f"Bearer {api_key}"},
                                      transport=transport, timeout=timeout)

    async def _req(self, method: str, path: str, **kw):
        r = await self.http.request(method, path, **kw)
        r.raise_for_status()
        return r.json()

    def _sub(self, subject: str | None) -> str | None:
        return subject or self.subject

    async def add(self, content, subject: str | None = None, container: str | None = None,
                  metadata: dict | None = None, idempotency_key: str | None = None) -> dict:
        return await self._req("POST", "/v1/memories", json={
            "content": content, "subject": self._sub(subject), "container": container, "metadata": metadata,
            "idempotency_key": idempotency_key})

    async def remember(self, text: str, kind: str = "fact", entities: list[str] | None = None,
                       subject: str | None = None, container: str | None = None) -> Memory | None:
        """Store a fact you already extracted. None means it only confirmed an existing fact."""
        d = await self._req("POST", "/v1/remember", json={"text": text, "kind": kind, "entities": entities or [],
                                                           "subject": self._sub(subject), "container": container})
        return _memory(d["memory"]) if d["memory"] else None

    async def document(self, doc_id: str) -> dict:
        return await self._req("GET", f"/v1/documents/{doc_id}")

    async def search(self, query: str, subject: str | None = None, container: str | None = None, k: int = 10,
                     token_budget: int = 1500, filters: dict | None = None, as_of: datetime | None = None,
                     include_history: bool = False) -> SearchResult:
        d = await self._req("POST", "/v1/search", json={
            "query": query, "subject": self._sub(subject), "container": container, "k": k,
            "token_budget": token_budget, "filters": filters, "as_of": as_of.isoformat() if as_of else None,
            "include_history": include_history})
        return _result(d)

    async def profile(self, subject: str | None = None, container: str | None = None,
                      token_budget: int = 800) -> SearchResult:
        params = {"token_budget": token_budget}
        if self._sub(subject):
            params["subject"] = self._sub(subject)
        if container:
            params["container"] = container
        return _result(await self._req("GET", "/v1/profile", params=params))

    async def forget(self, memory_id: str | None = None, document_id: str | None = None,
                     subject: str | None = None, everything: bool = False) -> DeletionReceipt:
        if not (memory_id or document_id or subject or everything):
            raise ValueError("forget() needs memory_id, document_id, subject or everything")
        d = await self._req("POST", "/v1/forget", json={"memory_id": memory_id, "document_id": document_id,
                                                         "subject": subject, "everything": everything})
        return DeletionReceipt(id=d["id"], counts=d["counts"], at=_dt(d["at"]))

    async def export(self, subject: str | None = None, container: str | None = None) -> dict:
        params = {k: v for k, v in (("subject", self._sub(subject)), ("container", container)) if v}
        return await self._req("GET", "/v1/export", params=params)

    async def aclose(self) -> None:
        await self.http.aclose()
