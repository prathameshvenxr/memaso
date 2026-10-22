def rrf(rankings: list[list[str]], k: int = 60, weights: list[float] | None = None) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: items ranked well by several lists win."""
    score: dict[str, float] = {}
    for w, ranking in zip(weights or [1.0] * len(rankings), rankings):
        for rank, id in enumerate(ranking):
            score[id] = score.get(id, 0.0) + w / (k + rank + 1)
    return sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
