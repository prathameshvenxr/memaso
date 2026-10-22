"""Truth maintenance: decide how a candidate relates to what is already remembered, and version accordingly."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources

import jsonschema

from ..errors import LLMSchemaError
from ..types import Candidate, Document, Kind, Memory, Scope, Verdict
from .dedup import exact_duplicate
from .extract import VERSION
from .rules import POSITIVE, key_of, key_relation, pref_parts

_DIR = resources.files("memory_core.prompts") / VERSION
JUDGE_SYSTEM = (_DIR / "judge.md").read_text()
JUDGE_SCHEMA = json.loads((_DIR / "schema.json").read_text())["judge"]


@dataclass
class Decision:
    verdict: Verdict
    ids: list[str] = field(default_factory=list)
    reason: str | None = None


def rules_judge(cand_text: str, neighbours: list[Memory]) -> Decision | None:
    """Decide without a model when the text is a plain 'subject is value' statement. None means ambiguous."""
    cp = pref_parts(cand_text)
    if cp is not None:
        # "prefers INR" then "prefers weekly reports" are two preferences, not an update. Only an exact opposite
        # (same subject and value, opposite verb: hates / likes) is decidable without a model.
        for n in neighbours:
            np_ = pref_parts(n.text)
            if np_ and np_[0] == cp[0] and np_[2] == cp[2] and (np_[1] in POSITIVE) != (cp[1] in POSITIVE):
                return Decision(Verdict.contradicts, [n.id], "opposite preference")
        return None
    ck = key_of(cand_text)
    if ck is None:
        return None
    ambiguous = False
    for n in neighbours:
        nk = key_of(n.text)
        if nk is None:
            ambiguous = True  # free text near a stored fact: it might be about the same thing
            continue
        rel = key_relation(ck[0], nk[0])
        if rel == "same":
            return Decision(Verdict.updates, [n.id], "same attribute, new value")
        ambiguous = ambiguous or rel == "related"
    return None if ambiguous else Decision(Verdict.new, [], "different attribute")


async def resolve(cand: Candidate, vec: list[float], scope: Scope, c) -> Decision:
    # neighbours are the same subject's facts in the same container (ad account): "budget" in account A must
    # never be superseded by "budget" in account B
    hits = await c.vectors.query(scope, vec, c.embedder.model_id, int(c.cfg.truth["neighbours"]),
                                 {"include_future": True, "container_is": scope.container}, None)
    near = [i for i, s in hits if s >= float(c.cfg.truth["neighbour_threshold"])]
    if not near:
        return Decision(Verdict.new)
    neighbours = await c.mems.get_many(scope.tenant, near)
    dup = exact_duplicate(cand.text, neighbours)
    if dup:
        return Decision(Verdict.duplicate, [dup.id], "same text")
    mode = c.cfg.truth.get("judge", "auto")  # auto: rules first, model only if ambiguous | rules | llm
    if mode in ("auto", "rules"):
        decided = rules_judge(cand.text, neighbours)
        if decided is not None:
            return decided
        if mode == "rules" or c.llm is None:
            return Decision(Verdict.new, [], "ambiguous, no model: kept as a separate fact")
    # Only a malformed answer degrades to "new". A transport or provider error propagates so the queue retries:
    # swallowing an outage here would store the new value beside the stale one, permanently.
    try:
        out = await c.llm.complete(
            [{"role": "system", "content": JUDGE_SYSTEM},
             {"role": "user", "content": json.dumps({"candidate": cand.text,
                                                     "neighbours": [{"id": m.id, "text": m.text} for m in neighbours]})}],
            JUDGE_SCHEMA)
        jsonschema.validate(out, JUDGE_SCHEMA)
    except (LLMSchemaError, jsonschema.ValidationError) as e:
        await c.aux.audit(scope.tenant, "system", "judge_failed", None, {"error": type(e).__name__})
        return Decision(Verdict.new, [], "judge answer invalid")
    verdict, target = Verdict(out["verdict"]), out.get("target_id")
    if verdict == Verdict.new:
        return Decision(Verdict.new, [], out.get("reason"))  # a target named alongside "new" is ignored
    if target not in {m.id for m in neighbours}:
        return Decision(Verdict.new, [], "judge named an unknown target")
    return Decision(verdict, [target], out.get("reason"))


async def apply(decision: Decision, cand: Candidate, vec: list[float], scope: Scope, doc: Document,
                version: str, c) -> Memory | None:
    if decision.verdict == Verdict.duplicate:
        await c.mems.bump_confidence(scope.tenant, decision.ids[0], doc.id)
        if c.chunks is not None:
            await c.chunks.link(scope.tenant, decision.ids[0], doc.id, cand.text, vec, c.embedder.model_id)
        return None
    valid_from = cand.valid_from or doc.created_at
    old = await c.mems.get(scope.tenant, decision.ids[0]) if decision.ids else None
    supersede = None
    valid_to = None
    if old is not None:
        if valid_from <= old.valid_from and doc.created_at < old.valid_from:
            valid_to = old.valid_from  # observed earlier than the fact that already exists: history only
        else:
            valid_from = max(valid_from, old.valid_from)  # a same-day, date-only hint must not predate the old fact
            supersede = (old.id, decision.verdict.value, decision.reason, valid_from)
    mem = Memory(id=str(uuid.uuid4()), scope=Scope(scope.tenant, scope.subject, doc.scope.container), text=cand.text,
                 kind=cand.kind, entities=cand.entities, confidence=cand.confidence,
                 importance=0.7 if cand.kind in (Kind.decision, Kind.preference) else 0.5, valid_from=valid_from,
                 valid_to=valid_to, supersedes=[old.id] if supersede else [], source_doc_ids=[doc.id],
                 extractor_version=version, created_at=datetime.now(UTC), meta=doc.meta)
    await c.mems.insert(mem, vec, c.embedder.model_id, supersede)  # insert + link + close old: one transaction
    if c.chunks is not None:
        # so superseded facts retire their chunks; a paraphrase falls back to the document's nearest chunk
        await c.chunks.link(scope.tenant, mem.id, doc.id, cand.text, vec, c.embedder.model_id)
    return mem
