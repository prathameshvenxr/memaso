from __future__ import annotations

from ..errors import AuthError
from ..migrate import hash_key
from ..types import Principal


class ApiKeyAuth:
    def __init__(self, aux):
        self.aux = aux

    async def resolve(self, headers: dict) -> Principal:
        h = {k.lower(): v for k, v in headers.items()}
        raw = h.get("authorization", "")
        if not raw.lower().startswith("bearer ") or not raw[7:].strip():
            raise AuthError("missing bearer token")
        principal = await self.aux.principal_for(hash_key(raw[7:].strip()))
        if principal is None:
            raise AuthError("invalid api key")
        return principal
