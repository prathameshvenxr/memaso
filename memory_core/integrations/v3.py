"""Shims for the AdsGPT v3 backend (white paper sections 8 and 20). They return plain specs matching v3's
`@tool(name, description, schema, roles=..., category=..., label=...)` + `async def handler(ctx, args, agent)`
shape, so v3 registers them with its own decorator. `scope_fn(ctx)` maps v3's RunContext to a Scope:
tenant = team, subject = user, container = the selected ad account.
ponytail: written from the white paper; v3's code was not available, so check the field names against it."""
from __future__ import annotations

from typing import Callable

from ..memory import Memory
from ..types import Scope

ScopeFn = Callable[[object], Scope]


def memory_tools(mem: Memory, scope_fn: ScopeFn) -> list[dict]:
    async def remember(ctx, args: dict, agent: str):
        m = await mem.remember(scope_fn(ctx), args["fact"], args.get("kind", "fact"), args.get("entities"))
        return {"saved": True, "superseded_older_value": bool(m and m.supersedes)}

    async def recall(ctx, args: dict, agent: str):
        r = await mem.recall(scope_fn(ctx), args["query"], k=int(args.get("k", 8)), token_budget=1200)
        return {"memories": r.context}  # already escaped and labelled as untrusted data

    return [
        {"name": "remember", "handler": remember, "roles": ("main",), "category": "control",
         "description": "Save one durable fact or preference about this user or account (budgets, targets, "
                        "reporting preferences). Do not save numbers fetched from Meta; those are data, not memory.",
         "schema": {"type": "object", "required": ["fact"], "properties": {
             "fact": {"type": "string"}, "kind": {"type": "string", "enum": ["fact", "preference", "decision"]},
             "entities": {"type": "array", "items": {"type": "string"}}}}},
        {"name": "recall", "handler": recall, "roles": ("main", "specialist"), "category": "data",
         "description": "Search what was said and decided in earlier conversations when the injected memory block "
                        "does not cover the question.",
         "schema": {"type": "object", "required": ["query"], "properties": {
             "query": {"type": "string"}, "k": {"type": "integer"}}}},
    ]


async def run_context(mem: Memory, scope: Scope, last_user_text: str, budget: int = 1500) -> str:
    """v3 step 3 ('load memories'): a small ranked block in place of every memory."""
    return await mem.context(scope, last_user_text, budget)


def compaction_hook(mem: Memory, scope_fn: ScopeFn):
    """v3 `prepare` step: call with the turns about to be summarised, before they are dropped."""
    async def hook(ctx, turns: list[dict]) -> None:
        await mem.archive(scope_fn(ctx), turns)

    return hook
