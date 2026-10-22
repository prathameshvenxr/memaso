from ..errors import LLMSchemaError


class HostLLM:
    """Wraps a callable supplied by the host application, so memory uses the app's own model, keys, model
    switching and usage accounting: `async def call(messages: list[dict], schema: dict) -> dict`."""

    def __init__(self, call):
        self.call = call

    async def complete(self, messages: list[dict], schema: dict) -> dict:
        out = await self.call(messages, schema)
        if not isinstance(out, dict):
            raise LLMSchemaError("host model call did not return a JSON object")
        return out
