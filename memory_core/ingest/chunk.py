from __future__ import annotations


def chunk(text: str, size: int = 4000) -> list[str]:
    """Split on paragraph, line or sentence boundaries so each model call stays small.
    ponytail: no overlap between chunks; a fact spanning a boundary is read by the nearest chunk only."""
    if len(text) <= size:
        return [text]
    out, i = [], 0
    while i < len(text):
        end = min(i + size, len(text))
        if end < len(text):
            for sep in ("\n\n", "\n", ". ", " "):  # strongest boundary first, only in the back half
                cut = text.rfind(sep, i + size // 2, end)
                if cut > i:
                    end = cut + 1
                    break
        out.append(text[i:end].strip())
        i = end
    return [c for c in out if c]
