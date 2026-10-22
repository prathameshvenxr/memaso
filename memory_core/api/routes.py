from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..ingest import pipeline
from ..manage.crud import edit_memory, get_memory, list_memories
from ..manage.delete import hard_delete
from ..manage.export import export_scope
from ..metering import check_budget, current_tenant
from ..retrieval.profile import profile
from ..retrieval.search import search
from ..serialize import doc_to_dict, memory_to_dict, receipt_to_dict, result_to_dict
from ..types import Principal
from .deps import principal, scope_for

router = APIRouter(prefix="/v1")


class AddReq(BaseModel):
    content: str | list[dict[str, Any]] | dict[str, Any]
    subject: str | None = None
    container: str | None = None
    metadata: dict | None = None
    idempotency_key: str | None = None
    observed_at: datetime | None = None


class SearchReq(BaseModel):
    query: str = Field(max_length=4000)
    subject: str | None = None
    container: str | None = None
    k: int = Field(10, ge=1, le=100)
    token_budget: int = Field(1500, ge=1, le=50_000)
    filters: dict | None = None
    as_of: datetime | None = None
    include_history: bool = False


class EditReq(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class RememberReq(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    kind: str = "fact"
    entities: list[str] = Field(default_factory=list, max_length=50)
    subject: str | None = None
    container: str | None = None


class ForgetReq(BaseModel):
    model_config = {"extra": "forbid"}  # a typo'd field must not silently become "forget everything of mine"
    memory_id: str | None = None
    document_id: str | None = None
    subject: str | None = None
    everything: bool = False


def _c(request: Request):
    return request.app.state.c


@router.post("/memories", status_code=202)
async def add_memory(body: AddReq, request: Request, p: Principal = Depends(principal)):
    c = _c(request)
    await check_budget(c.aux, p.tenant)
    doc = await pipeline.add(c, scope_for(p, body.subject, body.container), body.content, body.metadata,
                             body.idempotency_key, body.observed_at)
    return doc_to_dict(doc)


@router.post("/remember")
async def remember_route(body: RememberReq, request: Request, p: Principal = Depends(principal)):
    """A fact the caller already extracted (for example from the agent's own tool call): no rules, no model."""
    c = _c(request)
    await check_budget(c.aux, p.tenant)
    m = await pipeline.remember(c, scope_for(p, body.subject, body.container), body.text, body.kind, body.entities)
    return {"memory": memory_to_dict(m) if m else None, "confirmed_existing": m is None}


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request, p: Principal = Depends(principal)):
    doc = await _c(request).docs.get(p.tenant, doc_id)
    if doc is None or (p.subject and doc.scope.subject != p.subject):
        raise HTTPException(404, "document not found")
    return doc_to_dict(doc)


@router.post("/search")
async def search_route(body: SearchReq, request: Request, p: Principal = Depends(principal)):
    c = _c(request)
    await check_budget(c.aux, p.tenant)
    res = await search(c, scope_for(p, body.subject, body.container), body.query, body.k, body.token_budget,
                       body.filters, body.as_of, body.include_history)
    return result_to_dict(res)


@router.get("/profile")
async def profile_route(request: Request, subject: str | None = None, container: str | None = None,
                        token_budget: int = Query(800, ge=1, le=50_000), p: Principal = Depends(principal)):
    return result_to_dict(await profile(_c(request), scope_for(p, subject, container), token_budget))


@router.get("/memories")
async def list_route(request: Request, subject: str | None = None, container: str | None = None,
                     limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                     include_history: bool = False, p: Principal = Depends(principal)):
    ms = await list_memories(_c(request), scope_for(p, subject, container), limit, offset, include_history)
    return {"memories": [memory_to_dict(m) for m in ms]}


@router.get("/memories/{memory_id}")
async def get_route(memory_id: str, request: Request, p: Principal = Depends(principal)):
    m = await get_memory(_c(request), p.tenant, memory_id)
    if m is None or (p.subject and m.scope.subject != p.subject):
        raise HTTPException(404, "memory not found")
    return memory_to_dict(m)


@router.patch("/memories/{memory_id}")
async def edit_route(memory_id: str, body: EditReq, request: Request, p: Principal = Depends(principal)):
    c = _c(request)
    old = await get_memory(c, p.tenant, memory_id)
    if old is None or (p.subject and old.scope.subject != p.subject):
        raise HTTPException(404, "memory not found")
    new = await edit_memory(c, p.tenant, memory_id, body.text)
    if new is None:
        raise HTTPException(409, "memory is superseded; edit the current version")
    return memory_to_dict(new)


@router.delete("/memories/{memory_id}")
async def delete_route(memory_id: str, request: Request, p: Principal = Depends(principal)):
    c = _c(request)
    m = await get_memory(c, p.tenant, memory_id)
    if m is None or (p.subject and m.scope.subject != p.subject):
        raise HTTPException(404, "memory not found")
    return receipt_to_dict(await hard_delete(c, p.tenant, memory_id=memory_id))


@router.post("/forget")
async def forget_route(body: ForgetReq, request: Request, p: Principal = Depends(principal)):
    c = _c(request)
    if not (body.memory_id or body.document_id or body.subject or body.everything):
        raise HTTPException(422, "choose one of memory_id, document_id, subject, everything")
    if p.subject:
        if body.everything or (body.subject and body.subject != p.subject):
            raise HTTPException(403, "this key is bound to one subject")
        if body.memory_id:
            m = await get_memory(c, p.tenant, body.memory_id)
            if m is None or m.scope.subject != p.subject:
                raise HTTPException(404, "memory not found")
        if body.document_id:
            d = await c.docs.get(p.tenant, body.document_id)
            if d is None or d.scope.subject != p.subject:
                raise HTTPException(404, "document not found")
    r = await hard_delete(c, p.tenant, memory_id=body.memory_id, doc_id=body.document_id,
                          subject=body.subject, everything=body.everything)
    return receipt_to_dict(r)


@router.get("/export")
async def export_route(request: Request, subject: str | None = None, container: str | None = None,
                       p: Principal = Depends(principal)):
    return await export_scope(_c(request), scope_for(p, subject, container))
