from __future__ import annotations

import os

import httpx


class OpenAIEmbedder:
    """/embeddings endpoint; works for OpenAI and OpenAI-compatible servers via base_url."""

    def __init__(self, model: str, dim: int, api_key: str | None = None, base_url: str = "https://api.openai.com/v1",
                 timeout: float = 60):
        self.dim = dim
        self.model = model
        self.model_id = f"openai:{model}:{dim}"
        self.key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        body: dict = {"model": self.model, "input": texts}
        if self.model.startswith("text-embedding-3"):
            body["dimensions"] = self.dim
        r = await self.http.post("/embeddings", headers=headers, json=body)
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]
