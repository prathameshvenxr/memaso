from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources

import jsonschema

from ..errors import ExtractionFailed, LLMSchemaError
from ..types import Candidate, Kind

VERSION = "v1"
_DIR = resources.files("memory_core.prompts") / VERSION
SYSTEM = (_DIR / "extract.md").read_text()
SCHEMA = json.loads((_DIR / "schema.json").read_text())["extract"]


def _when(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None  # an unparsable date is dropped, the memory is still kept
    return d if d.tzinfo else d.replace(tzinfo=UTC)


async def extract(llm, content: str, as_of: datetime) -> tuple[list[Candidate], str]:
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps({"as_of": as_of.isoformat(), "content": content})}]
    last: Exception | None = None
    for _ in range(2):  # one retry on malformed output
        try:
            out = await llm.complete(messages, SCHEMA)
            jsonschema.validate(out, SCHEMA)
            break
        except (LLMSchemaError, jsonschema.ValidationError) as e:
            last = e
    else:
        raise ExtractionFailed(f"model output invalid after retry: {type(last).__name__}")
    cands = [Candidate(text=m["text"].strip(), kind=Kind(m["kind"]), entities=list(m.get("entities", [])),
                       confidence=float(m.get("confidence", 0.8)), valid_from=_when(m.get("valid_from")))
             for m in out["memories"] if m["text"].strip()]
    return cands, VERSION
