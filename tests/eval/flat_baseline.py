"""A model of a flat-note memory, to measure Memaso against: what the user said is appended as notes, and every
recall loads the notes whole, newest first, until the prompt budget is spent. No versions, no search, no forgetting.

This is a deliberately plain stand-in for the note-file style of agent memory. It is not any vendor's actual
implementation, and it is generous: every statement the user makes is kept, and nothing is lost to a bad summary."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from memory_core.ingest.rules import ROLE, SENT
from memory_core.tokens import count_tokens


class FlatNotes:
    def __init__(self) -> None:
        self.notes: list[tuple[datetime, str]] = []

    def add(self, text: str, at: datetime) -> None:
        for line in text.splitlines():
            for s in SENT.split(ROLE.sub("", line).strip()):
                s = s.strip()
                if s and "?" not in s:
                    self.notes.append((at, s))

    def recall(self, budget: int | None = None) -> str:
        """Newest first; `budget=None` is the whole file, which is what an unbounded note file costs."""
        out, used = [], 0
        for _, s in sorted(self.notes, key=lambda n: n[0], reverse=True):
            cost = count_tokens(s) + 1
            if budget is not None and used + cost > budget:
                break
            out.append(s)
            used += cost
        return "\n".join(out)


def run_flat_by_type(dataset: str | Path, budget: int = 1000) -> dict[str, float]:
    """Pass rate per item type with the same pass rule as `run_by_type`: every expected string present, no stale one."""
    passed: dict[str, list[bool]] = {}
    for line in Path(dataset).read_text().splitlines():
        if not line.strip():
            continue
        it = json.loads(line)
        mem = FlatNotes()
        for s in it["sessions"]:
            mem.add(s["text"], datetime.fromisoformat(s["at"]))
        ctx = mem.recall(budget)
        ok = all(x in ctx for x in it["expect_contains"]) and not any(x in ctx for x in it.get("expect_absent", []))
        passed.setdefault(it["type"], []).append(ok)
    return {t: sum(v) / len(v) for t, v in passed.items()}
