"""Deterministic rule-based LLM for tests and offline demos. Not a model: it understands only simple sentences."""
from __future__ import annotations

import json
import re


class FakeLLM:
    async def complete(self, messages: list[dict], schema: dict) -> dict:
        payload = json.loads(messages[-1]["content"])
        if schema.get("title") == "extract":
            out = []
            for s in re.split(r"[.!?\n]+", payload["content"]):
                s = s.strip()
                if not s:
                    continue
                kind = "preference" if re.search(r"\b(prefer|prefers|likes?|hates?)\b", s, re.I) else "fact"
                ents = sorted({w for w in re.findall(r"\b[A-Z][a-zA-Z]+\b", s)[1:]})
                out.append({"text": s, "kind": kind, "entities": ents, "confidence": 0.9, "valid_from": None})
            return {"memories": out}
        cand = payload["candidate"].strip().lower()
        for n in payload["neighbours"]:
            if n["text"].strip().lower() == cand:
                return {"verdict": "duplicate", "target_id": n["id"], "reason": "same text"}
        key = cand.split(" is ")[0]
        for n in payload["neighbours"]:
            if " is " in cand and n["text"].strip().lower().split(" is ")[0] == key:
                return {"verdict": "updates", "target_id": n["id"], "reason": "same attribute, new value"}
        return {"verdict": "new", "target_id": None, "reason": "unrelated"}
