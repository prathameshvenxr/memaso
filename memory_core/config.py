from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field

import yaml

# Names only: config validation must not import adapters (or their vendor code).
ADAPTERS = {
    "llm": ["anthropic", "openai", "compat", "fake", "host", "none"],
    "embedder": ["openai", "fastembed", "local", "fake"],
    "store": ["postgres"],
    "queue": ["inproc", "redis"],
    "auth": ["apikey"],
    "parser": ["text"],
}
DEFAULTS = {
    "llm": {"adapter": "none"},
    "embedder": {"adapter": "fastembed"},   # local ONNX: free, no API
    "store": {"adapter": "postgres", "dsn": "", "admin_dsn": ""},
    "queue": {"adapter": "inproc", "workers": 2},
    "auth": {"adapter": "apikey"},
    "parser": {"adapter": "text"},
    "truth": {"neighbour_threshold": 0.40,  # calibrated on MiniLM: same-attribute pairs scored 0.53-0.86
               "neighbours": 5, "judge": "auto",
               "related_ratio": 4.0,  # "monthly budget" updates "budget" only when the numbers are within this factor
               "aliases": [["cpa", "cost per acquisition", "cost per action"], ["roas", "return on ad spend"]]},
    "ingest": {"verbatim": True, "chunk_chars": 800},
    "facts": {"extract": "rules"},     # rules | llm | none: how facts are derived from ingested text
    "limits": {"requests_per_minute": 600},
    "retrieval": {"min_similarity": 0.30,  # MiniLM: off-domain noise <= 0.22, real questions >= 0.37
                  "graph_hops": 2},         # 0 turns the entity graph off; 1 = facts naming the entity; 2 = and their neighbours
    "consolidation": {"archive_after_days": 180, "archive_below": 0.35, "max_current": 5000,
                      "half_life_days": 90, "batch": 2000},   # explicit run: Memory.consolidate / POST /v1/consolidate
}


@dataclass
class Config:
    llm: dict = field(default_factory=dict)
    embedder: dict = field(default_factory=dict)
    store: dict = field(default_factory=dict)
    queue: dict = field(default_factory=dict)
    auth: dict = field(default_factory=dict)
    parser: dict = field(default_factory=dict)
    truth: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    retrieval: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    ingest: dict = field(default_factory=dict)
    consolidation: dict = field(default_factory=dict)


def _coerce(v: str):
    for cast in (int, float):
        try:
            return cast(v)
        except ValueError:
            pass
    return v


def load_config(path: str | None = None, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    raw: dict = copy.deepcopy(DEFAULTS)  # nested lists (aliases) must not be shared between Config instances
    if path:
        with open(path) as f:
            for section, vals in (yaml.safe_load(f) or {}).items():
                raw.setdefault(section, {}).update(vals or {})
    for key, val in env.items():  # MEMASO__LLM__ADAPTER=fake
        if key.startswith("MEMASO__"):
            parts = key[len("MEMASO__"):].lower().split("__")
            if len(parts) == 2:
                raw.setdefault(parts[0], {})[parts[1]] = _coerce(val)
    for section, names in ADAPTERS.items():
        name = raw[section].get("adapter")
        if ":" in str(name):  # "package.module:factory" plugs in a third-party adapter without touching this file
            continue
        if name not in names:
            raise ValueError(f"unknown {section} adapter {name!r}; valid: {', '.join(names)}")
    return Config(**{k: raw[k] for k in DEFAULTS})
