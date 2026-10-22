from __future__ import annotations

import math

from fastapi import HTTPException, Request

from ..errors import AuthError
from ..types import Principal, Scope


async def principal(request: Request) -> Principal:
    app = request.app
    try:
        p = await app.state.c.auth.resolve(dict(request.headers))
    except AuthError as e:
        raise HTTPException(401, str(e), headers={"WWW-Authenticate": "Bearer"})
    wait = app.state.limiter.retry_after(p.tenant)
    if wait > 0:
        raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(math.ceil(wait))})
    return p


def scope_for(p: Principal, subject: str | None, container: str | None) -> Scope:
    """Tenant always comes from the key. A key bound to a subject cannot act for another one."""
    if p.subject and subject and subject != p.subject:
        raise HTTPException(403, "this key is bound to a different subject")
    chosen = p.subject or subject
    if not chosen:
        raise HTTPException(422, "subject is required")
    return Scope(p.tenant, chosen, container)
