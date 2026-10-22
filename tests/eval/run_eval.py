from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from memory_core.ingest import pipeline
from memory_core.retrieval.search import search
from memory_core.types import Scope

TENANT = "eval"


async def run_eval(c, dataset: str | Path, limit: int | None = None) -> dict:
    items = [json.loads(l) for l in Path(dataset).read_text().splitlines() if l.strip()][:limit]
    hit = {"recall": [], "update": [], "abstain": []}
    stale = 0
    for it in items:
        scope = Scope(TENANT, f"eval-{it['id']}")
        for s in it["sessions"]:
            await pipeline.add(c, scope, s["text"], observed_at=datetime.fromisoformat(s["at"]))
        await c.queue.join()
        as_of = datetime.fromisoformat(it["as_of"]) if it.get("as_of") else None
        ctx = (await search(c, scope, it["question"], k=5, token_budget=1000, as_of=as_of)).context
        if it.get("expect_none"):
            hit["abstain"].append(ctx == "")
            continue
        found = all(x in ctx for x in it["expect_contains"])
        leaked = any(x in ctx for x in it.get("expect_absent", []))
        if it["type"] in ("update", "temporal"):
            hit["update"].append(found and not leaked)
            stale += leaked
        else:
            hit["recall"].append(found)
    rate = lambda xs: sum(xs) / len(xs) if xs else 1.0
    return {"recall_at_5": rate(hit["recall"]), "correct_after_update": rate(hit["update"]),
            "abstention": rate(hit["abstain"]), "stale_leak": stale, "items": len(items)}


async def run_by_type(c, dataset: str | Path) -> dict:
    """Pass rate per item type: an item passes if every expected string is in the context and no forbidden one is."""
    items = [json.loads(l) for l in Path(dataset).read_text().splitlines() if l.strip()]
    passed: dict[str, list[bool]] = {}
    for it in items:
        scope = Scope(TENANT, f"messy-{it['id']}")
        for s in it["sessions"]:
            await pipeline.add(c, scope, s["text"], observed_at=datetime.fromisoformat(s["at"]))
        await c.queue.join()
        ctx = (await search(c, scope, it["question"], k=5, token_budget=1000)).context
        ok = all(x in ctx for x in it["expect_contains"]) and not any(x in ctx for x in it.get("expect_absent", []))
        passed.setdefault(it["type"], []).append(ok)
    return {t: sum(v) / len(v) for t, v in passed.items()}
