"""How much a memory matters, decided once when it is stored and nudged later by corroboration and use. No model."""
from __future__ import annotations

import re

from ..types import Kind

BASE = {Kind.decision: 0.7, Kind.preference: 0.65, Kind.procedure: 0.6, Kind.relationship: 0.55, Kind.fact: 0.5,
        Kind.episode: 0.3}
EMPHASIS = re.compile(r"\b(always|never|must|important|critical|essential|deadline|required|do not|don't|cannot|can't|remember)\b",
                      re.I)


def score_importance(text: str, kind: Kind, explicit: bool = False) -> float:
    """Kind sets the base. Emphasis words ("never", "must") and a concrete number raise it, and so does the person
    asking for it to be saved (`explicit`). Always in [0, 1]."""
    score = BASE.get(kind, 0.5)
    if EMPHASIS.search(text):
        score += 0.2
    if re.search(r"\d", text):
        score += 0.05
    if explicit:
        score += 0.1
    return round(min(1.0, score), 3)
