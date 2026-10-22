"""Messier synthetic chat (seeded): python -m tests.eval.make_messy. The first dataset is one clean sentence per turn;
this one uses first-person phrasing, 'actually...' corrections, compound sentences, paraphrased updates, buried facts
and opposite preferences. Some categories are known to be hard without a model; the point is to measure, not to pass."""
import json
import random
from pathlib import Path

PER = 10
T = lambda m, d: f"2026-{m:02d}-{d:02d}T09:00:00+00:00"
CADENCES = ["daily", "weekly", "monthly", "quarterly"]
THINGS = ["pie charts", "carousels", "long captions", "video ads", "dark mode reports", "emoji", "stock photos"]
NAMES = ["Priya", "Rahul", "Meera", "Arjun", "Kavya", "Dev", "Isha", "Nikhil", "Tara", "Vikram"]
CHATTER = ["Thanks for the update.", "Let me know if anything changes.", "The creative review went well.",
           "We should sync again on Friday.", "Priya joined the call late.", "The agency sent the new deck."]


def build() -> list[dict]:
    rnd = random.Random(7)
    items, n = [], iter(range(10_000))
    val = lambda: str(rnd.randint(1000, 9999))
    for _ in range(PER):
        cad = rnd.choice(CADENCES)
        items.append({"id": next(n), "type": "first_person_preference",
                      "sessions": [{"at": T(1, 5), "text": f"I prefer {cad} reports."}],
                      "question": "which reports do I prefer", "expect_contains": [cad]})
    for _ in range(PER):
        v1, v2 = val(), val()
        items.append({"id": next(n), "type": "marker_update",
                      "sessions": [{"at": T(1, 5), "text": f"Budget is {v1}"},
                                   {"at": T(2, 1), "text": f"Actually the budget is {v2}"}],
                      "question": "what is the budget", "expect_contains": [v2], "expect_absent": [v1]})
    for _ in range(PER):
        v, cpa = val(), val()
        items.append({"id": next(n), "type": "compound_sentence",
                      "sessions": [{"at": T(1, 5), "text": f"The budget is {v} and the CPA is {cpa}."}],
                      "question": "what is the CPA", "expect_contains": [cpa]})
    for _ in range(PER):
        v1, v2 = val(), val()
        items.append({"id": next(n), "type": "paraphrased_update",
                      "sessions": [{"at": T(1, 5), "text": f"Budget is {v1}"},
                                   {"at": T(2, 1), "text": f"We've raised our monthly budget to {v2}."}],
                      "question": "what is the budget", "expect_contains": [v2], "expect_absent": [v1]})
    for _ in range(PER):
        v = val()
        text = " ".join(rnd.sample(CHATTER, 3) + [f"The CPA target is {v}."] + rnd.sample(CHATTER, 2))
        items.append({"id": next(n), "type": "buried_fact", "sessions": [{"at": T(1, 5), "text": text}],
                      "question": "what is the CPA target", "expect_contains": [v]})
    for _ in range(PER):
        th = rnd.choice(THINGS)
        items.append({"id": next(n), "type": "opposite_preference",
                      "sessions": [{"at": T(1, 5), "text": f"I hate {th}"}, {"at": T(2, 1), "text": f"I like {th}"}],
                      "question": f"do I like {th}", "expect_contains": [f"I like {th}"],
                      "expect_absent": [f"I hate {th}"]})
    # categories below were added later and come last, so every item above keeps its id and text
    for _ in range(PER):
        v1, v2 = val(), val()
        items.append({"id": next(n), "type": "passive_update",
                      "sessions": [{"at": T(1, 5), "text": f"Budget is {v1}"},
                                   {"at": T(2, 1), "text": f"The budget has been updated to {v2}."}],
                      "question": "what is the budget", "expect_contains": [v2], "expect_absent": [v1]})
    for _ in range(PER):
        v1, v2 = val(), val()
        items.append({"id": next(n), "type": "from_to_update",
                      "sessions": [{"at": T(1, 5), "text": f"The CPA target is {v1}"},
                                   {"at": T(2, 1), "text": f"We changed the CPA target from {v1} to {v2}."}],
                      # "from 4123 to 5678" rightly mentions the old number, so what must be gone is the stale claim
                      "question": "what is the CPA target", "expect_contains": [v2], "expect_absent": [f"target is {v1}"]})
    for _ in range(PER):
        v1, v2 = val(), val()
        items.append({"id": next(n), "type": "went_to_update",
                      "sessions": [{"at": T(1, 5), "text": f"Budget is {v1}"},
                                   {"at": T(2, 1), "text": f"The budget went up to {v2}."}],
                      "question": "what is the budget", "expect_contains": [v2], "expect_absent": [v1]})
    for _ in range(PER):  # a control: a daily figure must not erase the monthly one
        v1, small = val(), str(rnd.randint(11, 99))
        items.append({"id": next(n), "type": "daily_is_not_monthly",
                      "sessions": [{"at": T(1, 5), "text": f"Budget is {v1}"},
                                   {"at": T(2, 1), "text": f"The daily budget is {small}."}],
                      "question": "what is the budget", "expect_contains": [v1, small]})
    for _ in range(PER):  # the answer is one entity away from anything the question says
        name, v = rnd.choice(NAMES), val()
        items.append({"id": next(n), "type": "multi_hop",
                      "sessions": [{"at": T(1, 5), "text": f"The account lead for Acme is {name}."},
                                   {"at": T(1, 6), "text": f"The Acme CPA target is {v}."}],
                      "question": f"what number should {name} hit", "expect_contains": [v]})
    return items


if __name__ == "__main__":
    path = Path(__file__).with_name("dataset_messy.jsonl")
    path.write_text("\n".join(json.dumps(i) for i in build()) + "\n")
    print(f"wrote {path} ({len(build())} items)")
