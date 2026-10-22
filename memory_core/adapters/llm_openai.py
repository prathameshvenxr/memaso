from __future__ import annotations

import json
import os

import httpx

from ..errors import LLMSchemaError


class OpenAILLM:
    """Chat Completions with structured output. `json_mode=True` (compat servers) inlines the schema instead."""

    def __init__(self, model: str, api_key: str | None = None, base_url: str = "https://api.openai.com/v1",
                 json_mode: bool = False, timeout: float = 60):
        self.model, self.json_mode = model, json_mode
        self.key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def complete(self, messages: list[dict], schema: dict) -> dict:
        msgs = list(messages)
        if self.json_mode:
            rf = {"type": "json_object"}
            msgs = [{"role": "system", "content": "Reply with one JSON object valid for this JSON Schema:\n"
                     + json.dumps(schema)}] + msgs
        else:
            rf = {"type": "json_schema", "json_schema": {"name": schema.get("title", "result"), "schema": schema}}
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        r = await self.http.post("/chat/completions", headers=headers,
                                 json={"model": self.model, "messages": msgs, "response_format": rf})
        r.raise_for_status()
        try:
            return json.loads(r.json()["choices"][0]["message"]["content"])
        except (KeyError, IndexError, ValueError) as e:
            raise LLMSchemaError("model returned unparsable output") from e
