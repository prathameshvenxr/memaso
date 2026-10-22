from __future__ import annotations

import hashlib
import math
import re


STOP = {"the", "for", "a", "an", "of", "to", "what", "which", "does", "do", "and", "in", "on", "are", "was",
        "how", "much", "tell", "me"}


class HashEmbedder:
    """Bag-of-words hashing embedder: deterministic, offline, similar texts land close together."""

    def __init__(self, dim: int = 64):
        self.dim = dim
        self.model_id = f"fake:hash{dim}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in (w for w in re.findall(r"\w+", t.lower()) if w not in STOP):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out
