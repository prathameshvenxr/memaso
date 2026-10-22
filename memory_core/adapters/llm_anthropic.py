from __future__ import annotations

import os

import httpx

from ..errors import LLMSchemaError


class AnthropicLLM:
    def __init__(self, model: str, api_key: str | None = None, base_url: str = "https://api.anthropic.com",
                 max_tokens: int = 4096, timeout: float = 60):
        self.model, self.max_tokens = model, max_tokens
        self.key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def complete(self, messages: list[dict], schema: dict) -> dict:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [m for m in messages if m["role"] != "system"]
        r = await self.http.post("/v1/messages", headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                                 json={"model": self.model, "max_tokens": self.max_tokens, "system": system,
                                       "messages": turns,
                                       "tools": [{"name": "respond", "description": "Return the result.",
                                                  "input_schema": schema}],
                                       "tool_choice": {"type": "tool", "name": "respond"}})
        r.raise_for_status()
        for block in r.json().get("content", []):
            if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
                return block["input"]
        raise LLMSchemaError("model returned no structured output")
