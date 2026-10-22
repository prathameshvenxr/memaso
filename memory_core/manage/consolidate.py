"""Consolidation: keep a subject's memory small and true without a model.

  merge    two current facts with the same attribute and value (the same statement in other words) become one.
  archive  cold, low-value facts leave current search and give back their vectors. Nothing is deleted: the row, its
           source text and its history stay, and `restore` brings a fact back.

It is an explicit, bounded operation (the host or a scheduler calls it), never something a request waits for."""
from __future__ import annotations

from datetime import UTC, datetime

from ..ingest.dedup import exact_duplicate
from ..ingest.rules import canon_key, key_of, key_tokens
from ..ingest.truth import rules_judge
from ..metering import current_tenant
from ..retrieval.salience import salience
from ..types import Memory, Scope, Verdict

KEEP_ABOVE = 0.8  # importance at or above this is never archived, however cold


def _identity(m: Memory, aliases) -> tuple | None:
    k = key_of(m.text)
    return None if k is None else (m.scope.container, key_tokens(canon_key(k[0], aliases)), k[1])


async def consolidate(c, scope: Scope, now: datetime | None = None) -> dict:
    """One subject at a time, under the same per-subject lock ingest takes, so two runs (or a run and an ingest)
    never interleave."""
    async with c.lock(scope.tenant, scope.subject):
        return await _consolidate(c, scope, now)


async def _consolidate(c, scope: Scope, now: datetime | None) -> dict:
    cfg = c.cfg.consolidation
    now = now or datetime.now(UTC)
    batch = int(cfg.get("batch", 2000))
    half_life = float(cfg.get("half_life_days", 90))
    tenant = scope.tenant
    token = current_tenant.set(tenant)
    try:
        # merge: window over the newest and the coldest memories, so a very large subject stays a bounded read
        seen = {m.id: m for m in await c.mems.list(scope, limit=batch, offset=0)}
        cold = await c.mems.window(scope, batch, own_container_only=True)  # shared facts are not this container's to archive
        seen.update({m.id: m for m in cold})
        groups: dict[tuple, list[Memory]] = {}
        for m in seen.values():
            ident = _identity(m, c.cfg.truth.get("aliases", ()))
            if ident is not None:
                groups.setdefault(ident, []).append(m)
        merged: set[str] = set()
        for members in groups.values():
            if len(members) > 1:
                members.sort(key=lambda m: (m.valid_from, m.created_at, m.id))
                for dup in members[1:]:
                    await c.mems.merge_duplicate(tenant, members[0].id, dup.id, now)
                    merged.add(dup.id)

        # archive: old and unused and unimportant, then whatever exceeds the cap, least salient first
        candidates = [m for m in cold if m.id not in merged and m.importance < KEEP_ABOVE]
        sal = {m.id: salience(m, now, half_life) for m in candidates}
        age = lambda m: (now - (m.last_accessed or m.created_at)).days
        to_archive = [m.id for m in candidates
                      if age(m) >= int(cfg["archive_after_days"]) and sal[m.id] < float(cfg["archive_below"])]
        total = await c.mems.count_current(scope, own_container_only=True)
        over = total - len(to_archive) - int(cfg["max_current"])
        if over > 0:
            rest = sorted((m for m in candidates if m.id not in set(to_archive)), key=lambda m: sal[m.id])
            to_archive += [m.id for m in rest[:over]]
        archived = await c.mems.archive(tenant, to_archive, now)
        report = {"merged": len(merged), "archived": archived}
        await c.aux.audit(tenant, "system", "consolidate", None, {"subject": scope.subject, **report})
        return report
    finally:
        current_tenant.reset(token)


async def restore(c, tenant: str, ids: list[str]) -> int:
    """Bring archived memories back into current search and re-embed them. A memory is left archived when a current
    fact already states the same attribute (a newer value, or the same one): restoring it would put a stale value
    back beside the current one. Returns how many were restored. No model is called."""
    token = current_tenant.set(tenant)
    try:
        mems = [m for m in await c.mems.get_many(tenant, ids) if m.archived_at is not None]
        if not mems:
            return 0
        vecs = await c.embedder.embed([m.text for m in mems])
        restored = 0
        for m, vec in zip(mems, vecs):
            async with c.lock(tenant, m.scope.subject):
                hits = await c.vectors.query(m.scope, vec, c.embedder.model_id, int(c.cfg.truth["neighbours"]),
                                             {"include_future": True, "container_is": m.scope.container}, None)
                near = [i for i, sc in hits if sc >= float(c.cfg.truth["neighbour_threshold"])]
                neighbours = await c.mems.get_many(tenant, near)
                decided = rules_judge(m.text, neighbours, float(c.cfg.truth.get("related_ratio", 4.0)),
                                      c.cfg.truth.get("aliases", ()))
                if exact_duplicate(m.text, neighbours) or (decided is not None and decided.verdict != Verdict.new):
                    continue
                if await c.mems.unarchive(tenant, [m.id]):
                    await c.mems.set_vector(tenant, m.id, vec, c.embedder.model_id)
                    restored += 1
        return restored
    finally:
        current_tenant.reset(token)
