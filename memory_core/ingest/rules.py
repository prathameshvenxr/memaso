"""Model-free fact extraction and judging. Deliberately conservative: it only fires on plain statements, and
anything it cannot read stays in the verbatim layer, where search still finds it."""
from __future__ import annotations

import re

from ..types import Candidate, Kind

ROLE = re.compile(r"^(user|assistant|system|tool):\s*", re.I)
SENT = re.compile(r"(?<=[.!?])\s+")
PREF_VERB = r"(?:prefers?|likes?|dislikes?|hates?|wants?)"
KEY = re.compile(rf"^(?P<k>.{{1,80}}?)\s+(?:(?P<be>is|are|was|were|will be|should be|must be|set to|=)|(?P<pv>{PREF_VERB}))\s+(?P<v>.{{1,120}})$", re.I)
MARKERS = re.compile(r"^(?:(?:actually|so|ok|okay|well|now|update|also|and|but|then|btw|fyi|note|anyway)\b[,:]?\s+)+", re.I)
COMPOUND = re.compile(r"(?:;|,?\s+(?:and|but)\s+).*\b(?:is|are|was|were|prefers?|likes?|dislikes?|hates?|wants?)\b", re.I)


def strip_markers(s: str) -> str:
    return MARKERS.sub("", s.strip())


def subject_of(k: str) -> str:
    """'The CPA' -> 'cpa'; 'I' or 'we' -> 'user', so first-person preferences share one subject."""
    k = re.sub(r"\s+", " ", k.lower()).strip()
    return "user" if k in ("i", "we") else k


DECISION = re.compile(r"\b(decided|agreed|approved|we will|we'll|going forward)\b", re.I)
HEDGE = re.compile(r"\b(maybe|might|perhaps|not sure|i think|probably|could be)\b", re.I)
NAME = re.compile(r"\b[A-Z][a-zA-Z0-9]{2,}\b")


DETERMINERS = {"the", "a", "an", "our", "your", "my", "their", "his", "her", "its", "this", "that", "current", "new"}


def key_tokens(key: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"\w+", key) if t not in DETERMINERS)


def key_relation(a: str, b: str) -> str:
    """same: identical once determiners are dropped. related: one subject contains the other ('budget' vs
    'monthly budget'), which may or may not be the same attribute. different: anything else."""
    ta, tb = key_tokens(a), key_tokens(b)
    if ta == tb:
        return "same"
    return "related" if ta < tb or tb < ta else "different"


POSITIVE = {"prefer", "like", "want"}


def pref_parts(text: str) -> tuple[str, str, str] | None:
    """(subject, verb, value) for 'The client hates pie charts', verb singular; None for anything else."""
    m = KEY.match(strip_markers(text).rstrip(".!?"))
    if not m or not m["pv"]:
        return None
    verb = re.sub(r"s$", "", m["pv"].lower())
    return (" ".join(sorted(key_tokens(subject_of(m["k"])))), verb, re.sub(r"\s+", " ", m["v"].lower()).strip())


def key_of(text: str) -> tuple[str, str] | None:
    """('the cpa of camp7', '420') for 'The CPA of camp7 is 420'. A preference verb is part of the key, so
    'client prefers weekly' and 'client prefers monthly' are the same attribute."""
    m = KEY.match(strip_markers(text).rstrip(".!?"))
    if not m:
        return None
    k = subject_of(m["k"])
    if m["pv"]:
        k += " " + re.sub(r"s$", "", m["pv"].lower())
    return k, re.sub(r"\s+", " ", m["v"].lower()).strip()


def extract_rules(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    speaker = "user"
    for line in text.splitlines():
        role = ROLE.match(line)
        if role:
            speaker = role.group(1).lower()
        if speaker != "user":  # only what the person said; an assistant/tool message's later lines stay theirs
            continue
        for s in SENT.split(ROLE.sub("", line).strip()):
            if s.rstrip().endswith("?") or "?" in s:  # questions state nothing: check before trimming punctuation
                continue
            s = strip_markers(s.strip().rstrip(".!").strip())
            if not (6 <= len(s) <= 200) or HEDGE.search(s):
                continue
            m = KEY.match(s)
            if m and COMPOUND.search(m["v"]):  # "X is 1 and Y is 2": two facts in one clause; leave it verbatim-only
                continue
            if m and m["pv"]:
                kind = Kind.preference
            elif DECISION.search(s):
                kind = Kind.decision
            elif m:
                kind = Kind.fact
            else:
                continue
            first = s.split(" ", 1)[0]
            ents = [w for w in dict.fromkeys(NAME.findall(s)) if w != first]
            out.append(Candidate(text=s, kind=kind, entities=ents, confidence=0.75))
    return out
