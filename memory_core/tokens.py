import math


def count_tokens(text: str) -> int:
    # ponytail: conservative estimate (3 UTF-8 bytes/token overcounts English, and no longer undercounts
    # Devanagari/CJK); swap in a real tokenizer behind this name if budgets must be exact
    return math.ceil(len(text.encode()) / 3)
