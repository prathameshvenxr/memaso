"""Drop-in memory for a LangGraph agent: a `remember` tool and a pre-model node that fills state["memory_context"]."""
from __future__ import annotations

from typing import Awaitable, Callable

from langchain_core.tools import StructuredTool

from ..sdk import MemoryClient

ScopeFn = Callable[[], dict]  # returns {"subject": ..., "container": ...}


def remember_tool(client: MemoryClient, scope_fn: ScopeFn) -> StructuredTool:
    async def remember(content: str) -> str:
        """Save a durable fact or preference about the user for future conversations."""
        await client.add(content, **scope_fn())
        return "saved"

    return StructuredTool.from_function(coroutine=remember, name="remember",
                                        description="Save a durable fact or preference for future conversations.")


def _last_user_text(state: dict) -> str:
    for m in reversed(state.get("messages", [])):
        role = m.get("role") if isinstance(m, dict) else getattr(m, "type", "")
        if role in ("user", "human"):
            content = m["content"] if isinstance(m, dict) else m.content
            return content if isinstance(content, str) else str(content)
    return ""


def memory_hook(client: MemoryClient, scope_fn: ScopeFn, token_budget: int = 1500
                ) -> Callable[[dict], Awaitable[dict]]:
    """Profile gets a third of the budget, query-specific recall the rest; failures degrade to no memory."""

    async def hook(state: dict) -> dict:
        try:
            scope = scope_fn()
            prof = await client.profile(token_budget=token_budget // 3, **scope)
            parts = [prof.context] if prof.context else []
            query = _last_user_text(state)
            if query.strip():
                rec = await client.search(query, token_budget=token_budget - prof.tokens, **scope)
                if rec.context:
                    parts.append(rec.context)
            return {"memory_context": "\n".join(parts)}
        except Exception:  # noqa: BLE001 - memory must never take the agent down
            return {"memory_context": ""}

    return hook
