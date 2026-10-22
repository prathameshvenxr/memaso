from __future__ import annotations

import asyncio


class FastEmbedder:
    """Local ONNX embeddings (no API, no torch, no cost): pip install memaso[fastembed]. The model downloads once
    on first use. Default: a 384-dimension MiniLM, the same family MemPalace ships as its default."""

    def __init__(self, model: str = "sentence-transformers/all-MiniLM-L6-v2", dim: int | None = None, **kw):
        from fastembed import TextEmbedding  # lazy: optional dependency

        self._m = TextEmbedding(model_name=model, **kw)
        native = len(next(iter(self._m.embed(["dimension probe"]))))
        if dim is not None and dim != native:
            raise ValueError(f"model {model} outputs {native} dimensions, not {dim}")
        self.dim = native
        self.model_id = f"fastembed:{model}:{native}"

    def _run(self, texts: list[str]) -> list[list[float]]:
        return [[float(x) for x in v] for v in self._m.embed(texts)]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._run, texts)
