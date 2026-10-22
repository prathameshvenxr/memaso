"""Salience: how worth keeping and showing a memory is right now. It blends importance, a forgetting curve that
fades important memories more slowly, and how often the memory has been used. It only ever orders and trims; it
never decides what is true (supersession does that)."""
from __future__ import annotations

import math
from datetime import UTC, datetime

from ..types import Memory


def salience(m: Memory, now: datetime | None = None, half_life_days: float = 90.0) -> float:
    now = now or datetime.now(UTC)
    last = m.last_accessed or m.created_at
    age_days = max((now - last).total_seconds() / 86400.0, 0.0)
    half_life = half_life_days * (0.5 + 2.0 * m.importance)  # 0.5x to 2.5x: what matters fades slowly
    decay = 0.5 ** (age_days / half_life)
    use = min(1.0, math.log1p(m.access_count) / math.log1p(10))
    return max(0.0, min(1.0, 0.5 * m.importance + 0.35 * decay + 0.15 * use))


def boost(sal: float) -> float:
    """A gentle multiplier on a relevance score: salience breaks ties and nudges order, relevance stays in charge."""
    return 0.85 + 0.30 * sal
