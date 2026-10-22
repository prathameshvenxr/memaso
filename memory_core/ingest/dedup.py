import re

from ..types import Memory


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?")


def exact_duplicate(text: str, neighbours: list[Memory]) -> Memory | None:
    n = normalize(text)
    return next((m for m in neighbours if normalize(m.text) == n), None)
