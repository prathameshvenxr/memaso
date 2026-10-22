from __future__ import annotations

import time


class RateLimiter:
    """Token bucket per tenant. ponytail: in-process; use Redis if the API runs on several workers."""

    def __init__(self, per_minute: int):
        self.cap = float(per_minute)
        self.rate = per_minute / 60.0
        self._b: dict[str, tuple[float, float]] = {}

    def retry_after(self, tenant: str) -> float:
        """0.0 if the request is allowed (and counted), else seconds until a token is available."""
        now = time.monotonic()
        tokens, last = self._b.get(tenant, (self.cap, now))
        tokens = min(self.cap, tokens + (now - last) * self.rate)
        if tokens >= 1.0:
            self._b[tenant] = (tokens - 1.0, now)
            return 0.0
        self._b[tenant] = (tokens, now)
        return (1.0 - tokens) / self.rate
