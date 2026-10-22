"""Deterministic synthetic ads-domain eval set (seeded, no model calls): python -m tests.eval.make_dataset"""
from __future__ import annotations

import json
import random
from pathlib import Path

METRICS = ["cpa", "roas", "ctr", "cpm", "cpc", "budget", "spend", "reach"]
CADENCES = ["daily", "weekly", "monthly", "quarterly"]
PER_TYPE = 40
T = lambda m, d: f"2026-{m:02d}-{d:02d}T09:00:00+00:00"


def sent(metric, camp, val):
    return f"The {metric} of {camp} is {val}."


def build() -> list[dict]:
    rnd = random.Random(2026)
    items: list[dict] = []
    nxt = iter(range(10_000))

    def fresh_value():
        return str(rnd.randint(1000, 9999))

    def distractors(n, avoid):
        out = []
        while len(out) < n:
            m, c = rnd.choice(METRICS), f"camp{rnd.randint(100, 999)}"
            if (m, c) != avoid:
                out.append(sent(m, c, fresh_value()))
        return out

    for _ in range(PER_TYPE):  # single fact, with distractors in the same conversation
        m, c, v = rnd.choice(METRICS), f"camp{rnd.randint(1, 99)}", fresh_value()
        text = " ".join(rnd.sample([sent(m, c, v)] + distractors(2, (m, c)), 3))
        items.append({"id": next(nxt), "type": "single", "sessions": [{"at": T(1, 5), "text": text}],
                      "question": f"Tell me the {m} of {c}", "expect_contains": [v]})
    for _ in range(PER_TYPE):  # preference
        cad = rnd.choice(CADENCES)
        items.append({"id": next(nxt), "type": "preference",
                      "sessions": [{"at": T(1, 5), "text": f"The client prefers {cad} report."}],
                      "question": "Which report cadence does client prefer", "expect_contains": [cad]})
    for _ in range(PER_TYPE):  # update: same attribute restated with a new value
        m, c = rnd.choice(METRICS), f"camp{rnd.randint(1, 99)}"
        v1, v2 = fresh_value(), fresh_value()
        items.append({"id": next(nxt), "type": "update",
                      "sessions": [{"at": T(1, 5), "text": sent(m, c, v1)}, {"at": T(3, 5), "text": sent(m, c, v2)}],
                      "question": f"Tell me the {m} of {c}", "expect_contains": [v2], "expect_absent": [v1]})
    for _ in range(PER_TYPE):  # multi-session: answer lives in the first of three sessions
        c = f"camp{rnd.randint(1, 99)}"
        ms = rnd.sample(METRICS, 3)
        vs = [fresh_value() for _ in ms]
        items.append({"id": next(nxt), "type": "multi_session",
                      "sessions": [{"at": T(1 + i, 5), "text": sent(m, c, v)} for i, (m, v) in enumerate(zip(ms, vs))],
                      "question": f"Tell me the {ms[0]} of {c}", "expect_contains": [vs[0]]})
    for _ in range(PER_TYPE):  # temporal: ask as of a date between two values
        m, c = rnd.choice(METRICS), f"camp{rnd.randint(1, 99)}"
        v1, v2 = fresh_value(), fresh_value()
        items.append({"id": next(nxt), "type": "temporal",
                      "sessions": [{"at": T(1, 5), "text": sent(m, c, v1)}, {"at": T(3, 5), "text": sent(m, c, v2)}],
                      "question": f"Tell me the {m} of {c}", "as_of": T(2, 1),
                      "expect_contains": [v1], "expect_absent": [v2]})
    for _ in range(PER_TYPE):  # abstention: the topic was never mentioned
        items.append({"id": next(nxt), "type": "abstain",
                      "sessions": [{"at": T(1, 5), "text": " ".join(distractors(3, None))}],
                      "question": f"Tell me the churn of campq{rnd.randint(1, 99)}", "expect_none": True})
    return items


if __name__ == "__main__":
    path = Path(__file__).with_name("dataset.jsonl")
    path.write_text("\n".join(json.dumps(i) for i in build()) + "\n")
    print(f"wrote {path} ({len(build())} items)")
