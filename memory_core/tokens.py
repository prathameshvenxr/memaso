import math
from typing import Callable

_counter: Callable[[str], int] | None = None


def set_counter(fn: Callable[[str], int] | None) -> None:
    """Plug in a real tokenizer (tiktoken, the host app's own). None restores the built-in estimate."""
    global _counter
    _counter = fn


def count_tokens(text: str) -> int:
    if _counter is not None:
        return _counter(text)
    # conservative estimate: 3 UTF-8 bytes per token overcounts English and no longer undercounts Devanagari/CJK
    return math.ceil(len(text.encode()) / 3)
