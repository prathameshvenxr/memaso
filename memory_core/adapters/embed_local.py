from __future__ import annotations

import asyncio


class LocalEmbedder:
    """sentence-transformers on the local machine (pip install memaso[local-embed])."""

    def __init__(self, model: str, dim: int | None = None):
        from sentence_transformers import SentenceTransformer  # lazy: heavy optional dependency

        self._m = SentenceTransformer(model)
        self.dim = dim or self._m.get_sentence_embedding_dimension()
        self.model_id = f"local:{model}:{self.dim}"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = await asyncio.to_thread(self._m.encode, texts, normalize_embeddings=True)
        return [list(map(float, v[: self.dim])) for v in vecs]
