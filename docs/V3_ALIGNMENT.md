# Memaso: alignment with the AdsGPT v3 architecture, and what we take from MemPalace

Addendum to `PRD.md`. Where the two disagree, this document wins. Sources: the v3 white paper (October 2026) and the
MemPalace repository (README, `docs/CLOSETS.md`, `docs/schema.sql`, `mempalace/backends/base.py`). MemPalace's benchmark
numbers are its own claims; they are not reproduced here.

## 1. What v3 already gives us (white paper section in brackets)

- One FastAPI backend, Postgres for everything, LangGraph agent loop `prepare -> agent <-> tools -> finalize` [3, 5].
- `call_llm` in front of the Anthropic and OpenAI models, switchable per message, with a fast model already used for compaction [14, 12].
- Tools are Python functions registered with a decorator, with pre-hooks (gates) and post-hooks (redact, cap) [8, 20].
- Memory today: a flat `memories` table (global or per account), written by the `remember` tool and injected whole into
  every chat; context compaction at 55% of the window condenses old turns into a lossy summary [12].
- Guardrail episodes (breach -> recovered) already persist state per entity [15, 16].
- Single backend worker, in-process run state; scale-out means Redis [3, 22].

## 2. What MemPalace teaches

| MemPalace idea | What it means for us |
|---|---|
| Stores conversations **verbatim** (~800-char "drawers"); never summarises or extracts | The source of truth is the raw text. Nothing is lost to a lossy summary. |
| Default retrieval makes **no LLM call** (vector + BM25, 0.6/0.4, temporal boost) | Recall costs zero model tokens. The LLM is an optional extra, not a dependency. |
| Structure by wing (person/project) -> room (topic) -> drawer | Our scope: tenant -> subject -> container, plus an optional topic label. |
| "Closets": a cheap regex-built index that only **boosts** ranking and never gates results | Weak extraction must never hide a hit. Our entity/keyword index is a ranking signal. |
| Temporal knowledge graph: triples with `valid_from`/`valid_to`, confidence, source pointer | Matches our versioned facts, and fits ads (budgets, targets, bids change over time). |
| Pluggable backends with a strict contract; embedder identity recorded; dimension/identity mismatch is an error | Same as our ports and `model_id` tagging. We keep it. |
| Save hooks fire **before context compression** | v3's compaction step becomes an archive step (see 4). |

Caveat: verbatim retrieval alone cannot say which of two stored statements is current ("budget is 50k" vs "80k").
MemPalace answers that with its graph; we answer it with the versioned facts layer. We keep both layers.

## 3. Decisions (these amend the PRD)

1. **Two layers, searched together.** Layer A: verbatim chunks (source of truth, zero LLM). Layer B: versioned facts
   (current-vs-stale, `as_of` queries, hard delete). Search queries both and fuses by rank.
2. **The LLM is optional.** `llm.adapter` may be `none`. Facts then come from (a) the agent's own `remember` tool call,
   which is already inside the model turn so it costs no extra call, and (b) rule-based extractors for ads patterns
   (targets, budgets, bids). A batched LLM extraction pass on the fast model stays available but is off by default.
3. **A `host` LLM adapter.** In library mode the backend hands Memaso its own `call_llm`, so those same models are used with
   the same keys, model switching and usage accounting. No second model service, no extra credentials.
4. **A model judges only what the rules cannot read.** Embedding similarity cannot tell a duplicate from an update
   ("Budget is 50k" vs "Budget is 80k" measured 0.75 with MiniLM, while "CPA target is 420" vs "ROAS target is 4" measured 0.49), so it is only used to find neighbours (>= 0.40, calibrated on MiniLM).
   Exact text is a duplicate. For plain "subject is value" statements the rules decide: same subject and attribute
   with a different value is an update, a different attribute is new. Only free text near a stored fact is
   ambiguous, and only then is the model asked, with one candidate and at most five neighbours. With
   `llm.adapter: none` an ambiguous case is kept as a separate fact. (`truth.judge`: auto | rules | llm.)
5. **Embeddings run locally by default** (`fastembed`: ONNX, MiniLM, free, no torch). OpenAI embeddings stay available. The embedder
   identity is recorded; a mismatch triggers re-index, never mixed vectors.
6. **Library mode first.** `from memory_core import Memory` runs inside the v3 backend on its existing Postgres (own
   `memaso` schema, pgvector). REST and MCP stay for other products. One runtime, one deployment, nothing new to operate.
7. **Scope mapping.** tenant = team, subject = user, container = ad account (v3's "global or per account"), topic = room.

## 4. How it plugs into the v3 run

| v3 step | Memaso hook |
|---|---|
| Start the run: "load memories" | `memory.context(scope, query, token_budget)` returns a small ranked block instead of every memory |
| `prepare`: compact if > 55% full | Before summarising, `memory.archive(turns)` stores the old turns verbatim, so detail stays retrievable |
| `agent` call | `recall` tool (model-driven search) next to the existing `remember` tool |
| Tool pre-hooks | `account_scope` gate applies to memory reads and writes |
| Tool post-hooks | recalled text is escaped, wrapped and labelled untrusted data (matches v3's "fetched text is data" rule) |
| `save + done` | `memory.ingest_turn(...)` stores the turn's chunks, asynchronously, with no model call |
| Governance engine | breach/recovered episodes are written as dated facts about the entity |
| Specialists | read the same memory with fresh context; they do not write directly |

## 5. Token cost per turn

| Step | LLM tokens |
|---|---|
| Embed turn chunks and queries (local) | 0 |
| Retrieval (vector + full-text + fusion) | 0 |
| Fact capture through `remember` | 0 extra (inside the agent's own call) |
| Context injection | capped by `token_budget` (instead of all memories) |
| Optional model extraction (`facts.extract: llm`) / judge | the app's own model, only if enabled / only for ambiguous free text |

## 6. Status

Built and tested: verbatim chunk layer fused with the facts layer (a chunk whose facts are all superseded becomes
history: the superseded words are redacted inside the chunk in "current" search and the rest stays, while `as_of` and
`include_history` still show the original),
`llm.adapter: none | host | ...`, rules extractor and judge, `remember` direct path, `Memory` facade, v3 tool shims
(written from the white paper; v3 code was not available), `fastembed` adapter, store swappable by config with
contract suites. Deferred: entity/triple graph, LongMemEval-style runner, entity-aware boosts (MemPalace closets).

## 7. Original build delta (done unless listed above as deferred)

1. `chunks` table and layer A: chunker, local ONNX embedder adapter, direct chunk search, rank fusion with facts.
2. `llm.adapter: none | host`; heuristic extractor and judge; judge band thresholds in config.
3. `Memory` facade for library mode with `context`, `archive`, `ingest_turn`, `remember`, `recall`.
4. v3 integration shims: `remember`/`recall` tool wrappers matching the v3 `@tool` signature, compaction hook.
5. Entity/triple graph with validity windows (P1), fed by guardrail episodes.
6. Eval: keep the ads set, add a LongMemEval-style R@5 runner.

Not changed: scopes, RLS, versioned facts, hard delete, REST/MCP, ports and contract suites.
