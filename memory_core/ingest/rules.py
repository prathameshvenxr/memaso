"""Model-free fact extraction and judging. Deliberately conservative: it only fires on plain statements, and
anything it cannot read stays in the verbatim layer, where search still finds it."""
from __future__ import annotations

import re
from collections import Counter

from ..types import Candidate, Kind

ROLE = re.compile(r"^(user|human|assistant|ai|system|tool|developer|function):\s*", re.I)
SPEAKER = {"human": "user", "ai": "assistant"}
SENT = re.compile(r"(?<=[.!?])\s+")
PREF_VERB = r"(?:prefers?|likes?|dislikes?|hates?|wants?)"
KEY = re.compile(rf"^(?P<k>.{{1,80}}?)\s+(?:(?P<be>is|are|was|were|will be|should be|must be|set to|=)|(?P<pv>{PREF_VERB}))\s+(?P<v>.{{1,120}})$", re.I)
MARKERS = re.compile(r"^(?:(?:actually|so|ok|okay|well|now|update|also|and|but|then|btw|fyi|note|anyway)\b[,:]?\s+)+", re.I)
COMPOUND = re.compile(r"(?:[;,]|\s+(?:and|but|while|plus|&)\s+).*\b(?:is|are|was|were|prefers?|likes?|dislikes?|hates?|wants?)\b", re.I)
PRONOUNS = {"it", "this", "that", "there", "he", "she", "they", "you"}
LEAD_IN = re.compile(r"^(?:now\s+|currently\s+)?(?:set\s+to\s+)?", re.I)  # "is now set to 80k" -> value "80k"

# ---------------------------------------------------------------- updates phrased as events
CHANGE = (r"(?:raised|increased|bumped|boosted|lifted|upped|lowered|reduced|cut|dropped|decreased|changed|updated|"
          r"revised|adjusted|moved|switched|set|pushed)")
UNAMBIGUOUS = (r"(?:raised|increased|bumped|boosted|lifted|upped|lowered|reduced|dropped|decreased|changed|updated|"
               r"revised|adjusted|moved|switched|pushed)")  # past forms only: "set" and "cut" are also instructions
# A subject is a pronoun or "the team". A capitalised word may be "Please" or "Never", so it is not one.
SUBJ = r"(?:we|i|they|he|she|you|the\s+team|everyone|someone)(?:'ve|\s+have|\s+had)?"
ADV = r"(?:(?:just|now|already|recently|finally)\s+)?"
VAL = r"(?P<v>[^\s,;:!?]+(?:\s[^\s,;:!?]+){0,2})"
TO = rf"(?:from\s+[^,;]{{1,30}}?\s+)?to\s+{VAL}"
ACTIVE = re.compile(rf"^{SUBJ}\s+{ADV}{CHANGE}\s+(?:up\s+|down\s+)?(?P<k>.{{2,60}}?)\s+{TO}$", re.I)
ACTIVE_NO_SUBJECT = re.compile(rf"^{ADV}{UNAMBIGUOUS}\s+(?:up\s+|down\s+)?(?P<k>.{{2,60}}?)\s+{TO}$", re.I)
PASSIVE = re.compile(rf"^(?P<k>.{{2,60}}?)\s+(?:has\s+been|have\s+been|was|were|got)\s+{ADV}{CHANGE}\s+(?:up\s+|down\s+)?{TO}$", re.I)
INTRANSITIVE = re.compile(
    rf"^(?P<k>.{{2,60}}?)\s+(?:went|has\s+gone|have\s+gone|rose|has\s+risen|climbed|has\s+climbed|fell|has\s+fallen|"
    rf"dropped|has\s+dropped|jumped|slipped)\s+(?:up\s+|down\s+)?{TO}$", re.I)
VALUE_WORDS = {"daily", "weekly", "monthly", "quarterly", "yearly", "inr", "usd", "eur", "gbp", "on", "off", "enabled",
               "disabled", "paused", "active", "manual", "automatic"}
UNIT_WORDS = {"percent", "%", "usd", "inr", "eur", "gbp", "dollars", "rupees", "euros", "pounds", "per", "daily", "weekly",
              "monthly", "yearly"}
POSSESSIVE = re.compile(r"^(?:our|my|their|your|its)\s+", re.I)
TRAILING_TIME = re.compile(r"\s+(?:today|yesterday|now|(?:this|last)\s+(?:week|month|quarter|year))$", re.I)
FUTURE = re.compile(r"\b(?:next|tomorrow|soon|later|tonight)\b", re.I)
# a key that is a pronoun, a particle or a phrase with a preposition in it is not an attribute
BAD_KEY = {"it", "this", "that", "there", "he", "she", "they", "you", "we", "i", "him", "her", "them", "us", "me", "in", "on",
           "at", "by", "into", "out", "up", "down", "off", "half"}


def _clean_value(v: str) -> str | None:
    """A real value is a number (optionally with a unit) or a known setting word. Trailing 'this month' is dropped;
    anything about the future is not a fact yet."""
    v = TRAILING_TIME.sub("", v.rstrip("."))
    if FUTURE.search(v):
        return None
    toks = v.lower().split()
    if not toks or len(toks) > 2 or not (re.search(r"\d", toks[0]) or toks[0] in VALUE_WORDS):
        return None
    if len(toks) == 2 and toks[1] not in UNIT_WORDS:
        return None
    return v


def _key_ok(k: str) -> bool:
    toks = key_tokens(re.sub(r"\s+", " ", k.lower()).strip())
    return 0 < len(toks) <= 4 and not (toks & BAD_KEY)


def rewrite_change(text: str) -> str | None:
    """'We raised our monthly budget to 80k' -> 'monthly budget is 80k'. Only something that already happened:
    instructions, intentions, the future and questions never match."""
    s = text.strip().rstrip(".!").strip()
    for pat in (ACTIVE, ACTIVE_NO_SUBJECT, PASSIVE, INTRANSITIVE):
        m = pat.match(s)
        if m:
            v = _clean_value(m["v"])
            k = POSSESSIVE.sub("", m["k"].strip())
            if v is not None and _key_ok(k):
                return f"{k} is {v}"
    return None


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


def canon_key(key: str, aliases: list[list[str]] | tuple = ()) -> str:
    """Replace each alias phrase by its first (canonical) form: 'cost per acquisition' -> 'cpa'."""
    key = re.sub(r"\s+", " ", key.lower()).strip()
    for group in aliases:
        canon = group[0].lower()
        for alt in group[1:]:
            key = re.sub(rf"(?<!\w){re.escape(alt.lower())}(?!\w)", canon, key)
    return key


def key_relation(a: str, b: str) -> str:
    """same: identical once determiners are dropped. related: one subject contains the other ('budget' vs
    'monthly budget'), which may or may not be the same attribute. different: anything else."""
    ta, tb = key_tokens(a), key_tokens(b)
    if ta == tb:
        return "same"
    return "related" if ta < tb or tb < ta else "different"


PERIODS = {"daily", "weekly", "monthly", "quarterly", "yearly", "annual", "hourly"}
EQUIVALENT = {"total", "overall", "main", "updated", "latest"}  # words that name the same attribute (plus DETERMINERS)
_UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


def parse_number(value: str) -> tuple[float, bool] | None:
    """(magnitude, is_percent) for '50k', '₹80,000', '1.5m', '12%'. None when there is no number."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(%|thousand|million|billion|[kmb]\b)?", value.lower())
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    unit = m.group(2) or ""
    return (n * _UNITS.get(unit, 1.0), unit == "%")


def refined_same(a: str, b: str, va: str, vb: str, ratio: float) -> bool:
    """Is 'monthly budget' the same attribute as 'budget'? Only when the extra words cannot change the meaning
    (total, overall...), or are a period word and the two numbers are of the same size. 'daily budget is 2k'
    next to 'budget is 50k' is 25x apart: a different attribute, so it is not guessed."""
    ta, tb = key_tokens(a), key_tokens(b)
    small, large = (ta, tb) if ta < tb else (tb, ta)
    if not small < large:
        return False
    extra = large - small
    if not extra <= (PERIODS | EQUIVALENT):
        return False
    if not extra & PERIODS:
        return True
    na, nb = parse_number(va), parse_number(vb)
    if na is None or nb is None or na[1] != nb[1] or min(na[0], nb[0]) <= 0:
        return False
    return max(na[0], nb[0]) / min(na[0], nb[0]) <= ratio


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
    if (m["be"] or "").lower() in ("was", "were"):  # history is not a current attribute
        return None
    k = subject_of(m["k"])
    toks = key_tokens(k)
    if not m["pv"] and (k == "user" or not toks or toks <= PRONOUNS):  # "we are", "it is", "this is": no real subject
        return None
    if m["pv"]:
        k += " " + re.sub(r"s$", "", m["pv"].lower())
    return k, re.sub(r"\s+", " ", LEAD_IN.sub("", m["v"].lower().strip())).strip()


def _opening_name(first: str, words: Counter, m) -> bool:
    """A capitalised word that opens a sentence is a name when the text uses it capitalised more than once, never as
    an ordinary lower-case word, and it is not just the attribute of a numeric fact ('Budget is 50k').
    `words` is the document's word count, computed once: a long document must stay linear."""
    w = first.strip(",.;:'\"")
    if not NAME.fullmatch(w) or words[w] < 2 or words[w.lower()]:
        return False
    return not (m and not m["pv"] and re.search(r"\d", m["v"]))


def extract_rules(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    speaker = "user"
    words = Counter(re.findall(r"\w+", text))
    for line in text.splitlines():
        role = ROLE.match(line)
        if role:
            speaker = SPEAKER.get(role.group(1).lower(), role.group(1).lower())
        if speaker != "user":  # only what the person said; an assistant/tool message's later lines stay theirs
            continue
        for s in SENT.split(ROLE.sub("", line).strip()):
            if s.rstrip().endswith("?") or "?" in s:  # questions state nothing: check before trimming punctuation
                continue
            s = strip_markers(s.strip().rstrip(".!").strip())
            if not (6 <= len(s) <= 200) or HEDGE.search(s):
                continue
            s = rewrite_change(s) or s  # "we raised the budget to 80k" is the statement "the budget is 80k"
            m = KEY.match(s)
            if m and (m["be"] or "").lower() in ("was", "were"):  # a past-tense remark stays verbatim-only
                continue
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
            if _opening_name(first, words, m):
                ents.insert(0, first.strip(",.;:'\""))
            out.append(Candidate(text=s, kind=kind, entities=ents, confidence=0.75))
    return out
