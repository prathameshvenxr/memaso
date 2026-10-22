# PRD: Memory Core, a memory layer for AI agents

Status: draft for approval · Owner: Prathamesh · Date: 2026-10-22
Items marked **[assumed]** are my defaults, not decisions. Change any of them before build.

---

## 1. Why

Agent backends commonly keep long-term memory as one flat table. A `remember` tool writes to it, and everything is injected into every chat.

That breaks as usage grows:
- **No retrieval.** Every memory is injected every time, so cost and noise grow with the number of memories.
- **No extraction.** A memory exists only if the model chooses to call `remember`.
- **No truth maintenance.** "Budget is 50k" and "budget is 80k" both stay.
- **No scoping beyond global or per account.** There is no brand, team, campaign or session scope.
- **Locked to one backend.** The logic is tied to one backend and one model pair.

Memory Core is a standalone, model-agnostic, storage-agnostic memory engine. It is a supermemory-style system. Any product or agent can use it.

## 2. Goals

1. **Remember automatically.** Extract durable facts from chats, documents and events without the caller deciding what matters.
2. **Recall precisely.** Return a small, relevant, ranked context block for a query, within a caller-set token budget.
3. **Stay true over time.** New facts supersede old ones. History is kept, and the engine can answer "what did we believe on date X".
4. **Be modular.** The LLM, embedding model, store, queue and connectors are all swappable adapters behind stable interfaces. No vendor name appears in core logic.
5. **Work for anything.** Memory is typed and scoped generically (user, org, account, project, agent, session), not hard-wired to ads.
6. **Be safe by default.** Hard tenant isolation, encryption, audit, and real deletion.

## 3. Non-goals

- Not a general agent framework or a chat UI.
- Not a replacement for DuckDB data memory, LangGraph checkpoints or the display transcript. It sits beside them.
- No fine-tuning or model training on memories.
- No cross-tenant learning, ever.
- No multi-region replication in v1.

## 4. Anti-goals (a failure even if it technically works)

- A memory leaks across tenants or users.
- A deleted memory still surfaces in recall, embeddings or logs.
- A stale fact beats a newer one.
- Recall injects more tokens than the caller's budget.
- Changing the model or store requires editing core code.

## 5. Users and scenarios

| Who | Scenario |
|---|---|
| Ads analyst agent | "Last month we agreed CPA target is ₹420 for the Diwali account." Next chat: recalled without the user repeating it. |
| Media buyer | Prefers weekly reports in INR, hates pie charts. The agent adapts without being told. |
| Agency admin | Memory is isolated per client account. An export and a wipe are available. |
| Another product or agent | Calls the same API with a different scope. No code changes. |
| Engineer | Swaps the extractor from one model to another via config, then runs the eval suite to compare. |

## 6. Concepts

- **Scope**: `{tenant, subject, container?}`. `tenant` is the hard isolation boundary. `subject` is a user, account or agent. `container` is a free-form project or session tag. Every read and write requires a tenant.
- **Document**: raw input (chat turns, text, file, URL, event). It is immutable and stored with provenance.
- **Memory**: an atomic, self-contained statement extracted from documents. Fields: `id, scope, text, kind, entities[], confidence, valid_from, valid_to, supersedes[], source_doc_ids[], importance, last_accessed, embedding_ref`.
- **Kind**: `fact | preference | decision | episode | procedure | relationship`. The set is extensible via config.
- **Entity and relation**: lightweight graph. Nodes are people, brands, accounts, campaigns and products. Edges are typed and time-bound.
- **Profile**: a derived view per subject, with stable facts (static) and recent context (dynamic). It is cheap to inject.

## 7. Functional requirements

Priority: **P0** = must ship in the one-shot, **P1** = next, **P2** = later.

### 7.1 Ingestion
- FR-1 (P0) `add(scope, content, metadata)` accepts text, chat messages `[{role, content}]`, and structured events. It is idempotent via a caller-supplied or content-hash key.
- FR-2 (P0) Ingestion is async. Return a `document_id` and status immediately. Status: `queued → extracting → indexed | failed`.
- FR-3 (P0) Extraction: a pluggable LLM turns a document into candidate memories with kind, entities, confidence and temporal hints. It is prompt-versioned and output is schema-validated.
- FR-4 (P0) Chunking and dedup. Near-duplicate memories (cosine plus text match) merge instead of stacking.
- FR-5 (P1) File ingestion: PDF, DOCX, CSV, HTML and Markdown via a parser adapter.
- FR-6 (P1) URL ingestion with a fetch adapter. It is SSRF-safe and allowlisted.
- FR-7 (P2) Connectors (Drive, Notion, Slack, email) via the same adapter interface.

### 7.2 Truth maintenance
- FR-8 (P0) Contradiction detection. A new memory is compared to its nearest neighbours in the same scope. The verdict is `new | duplicate | updates | contradicts`, judged by the LLM adapter.
- FR-9 (P0) `updates` and `contradicts` set `valid_to` on the old memory and link `supersedes`. The old memory is never overwritten.
- FR-10 (P0) Point-in-time recall: `as_of=<timestamp>`.
- FR-11 (P1) Decay and forgetting: importance times recency, and TTL per kind. Low-value memories are archived, not deleted.
- FR-12 (P1) Confidence update. Repeated confirmation raises it. A conflicting source lowers it.

### 7.3 Retrieval
- FR-13 (P0) `search(scope, query, k, token_budget, filters, as_of)` does hybrid retrieval. It runs vector and BM25/keyword search in parallel and fuses with reciprocal rank fusion.
- FR-14 (P0) Filters: kind, entity, container, time range, minimum confidence, metadata.
- FR-15 (P0) Token-budget packing. Return the highest-value memories that fit, with a rendered `context` string and structured `results[]` including score and provenance.
- FR-16 (P1) Rerank adapter (cross-encoder or LLM), optional.
- FR-17 (P1) Query rewriting and multi-hop expansion through entity edges.
- FR-18 (P0) `profile(scope)` returns static and dynamic facts as a compact block.
- FR-19 (P0) Superseded memories are excluded from default search, and included with `include_history=true`.

### 7.4 Management
- FR-20 (P0) CRUD for memories: get, list, edit, delete. Edits create a new version.
- FR-21 (P0) Hard delete by memory id, document id, subject, or tenant. It cascades to embeddings, graph edges and caches, and returns a deletion receipt.
- FR-22 (P0) Export of a scope as JSON.
- FR-23 (P1) Memory inspector UI: browse, search, edit, delete, and see why a memory was recalled.
- FR-24 (P1) Bulk import and migration from an existing flat `memories` table.

### 7.5 Interfaces
- FR-25 (P0) REST API with OpenAPI spec.
- FR-26 (P0) MCP server exposing `memory_add`, `memory_search`, `memory_profile` and `memory_forget`, so any MCP-capable agent can use it with no SDK.
- FR-27 (P0) Python SDK (async). A TypeScript SDK is P1.
- FR-28 (P0) A LangGraph integration. It provides a `remember` tool and a pre-model hook that injects `profile + search` into the prompt. It replaces flat injection of every memory.
- FR-29 (P1) Webhooks for `memory.created / superseded / deleted`.

### 7.6 Modularity (core requirement)
- FR-30 (P0) Every external dependency is a **port** with a documented interface and at least one adapter:

| Port | v1 adapters | Contract |
|---|---|---|
| `LLM` | Anthropic, OpenAI, local/OpenAI-compatible | `complete(messages, schema) → json` |
| `Embedder` | OpenAI-style, local sentence-transformer | `embed(texts) → vectors`, declares `dim` and `model_id` |
| `VectorStore` | Postgres+pgvector | upsert, query, delete by filter |
| `KeywordIndex` | Postgres FTS | query, upsert, delete |
| `DocStore` | Postgres | raw documents and versions |
| `Queue` | in-process, Redis | enqueue, ack, retry |
| `Parser` | text, PDF | bytes → text and structure |
| `Auth` | API key, JWT | request → tenant and principal |

- FR-31 (P0) Adapters are selected by config (env or YAML) only. Core imports ports, never adapters.
- FR-32 (P0) Embeddings are tagged with `model_id`. Changing the embedder triggers a background re-index. Old and new vectors never mix in one query.
- FR-33 (P0) Prompts and the extraction schema are versioned files, not inline strings. Each memory records the `extractor_version` that produced it.
- FR-34 (P0) A contract test suite that any adapter must pass. This is how a new model or store is certified.

## 8. Non-functional requirements

| Area | Requirement |
|---|---|
| Latency **[assumed]** | `search` p95 < 300 ms without rerank at 100k memories per tenant. `add` returns < 100 ms. |
| Throughput | Extraction queue scales horizontally. Workers are stateless. |
| Isolation | Tenant id in every table and index. Enforced by Postgres row-level security as well as app code. |
| Security | Secrets are encrypted at rest. TLS in transit. No memory text in logs by default. API keys are hashed. SSRF guard on URL fetch. Prompt-injection hardening: memory text is wrapped as untrusted data when rendered into context. |
| Privacy | Hard delete, export, per-tenant retention policy. PII-redaction hook (optional, adapter). |
| Reliability | Ingestion is at-least-once and idempotent. Failed extractions retry with backoff and land in a dead-letter list. |
| Observability | Structured logs, metrics (add/search latency, queue depth, extraction failures, recall hit-rate), trace ids across ingest to index. |
| Cost | Track tokens and spend per tenant. Per-tenant rate limits and monthly budget cap. |
| Portability | Runs via Docker Compose. Postgres is the only required service in v1. |
| Deploy safety | Separate service, separate schema. It never touches the host application's tables except through the one-time migration (FR-24). |

## 9. Architecture (summary)

```
 clients: REST · MCP · Python SDK · LangGraph hook
                    │
              API layer (auth, validation, rate limit)
                    │
   ┌────────────────┼─────────────────┐
 Ingest pipeline   Retrieval engine   Management
 queue → parse →   hybrid search →    CRUD · delete
 extract → dedup   fuse → rerank →    export · audit
 → contradiction   pack to budget
 → embed → index
                    │
        ports: LLM · Embedder · VectorStore · KeywordIndex
               DocStore · Queue · Parser · Auth
                    │
        default adapters: Postgres + pgvector + FTS
```

Tech defaults **[assumed]**: Python 3.12, FastAPI, SQLAlchemy and Alembic, pgvector, an in-process queue (Redis adapter as P1), pytest. Chosen to match a typical Python agent backend.

## 10. Data model (core tables)

`tenants`, `api_keys`, `documents(id, tenant, scope, raw_ref, hash, status, meta)`, `memories(id, tenant, subject, container, text, kind, confidence, importance, valid_from, valid_to, extractor_version, created_at, last_accessed)`, `memory_sources(memory_id, document_id)`, `memory_links(memory_id, supersedes_id)`, `entities`, `entity_edges(src, dst, type, valid_from, valid_to)`, `embeddings(memory_id, model_id, vector)`, `audit_log`, `deletion_receipts`.

## 11. Evaluation and acceptance

Quality must be measured, not assumed.

- AC-1 Build an in-repo eval set of at least 200 synthetic ads-domain conversations. Questions cover single-fact, preference, update, multi-session, temporal and abstention ("I don't know").
- AC-2 **[assumed targets]** Recall@5 ≥ 0.85, correct-after-update ≥ 0.90, abstention correctness ≥ 0.80. Stale-fact leakage = 0 on the update set.
- AC-3 Tenant-isolation test: a fuzzing suite shows 0 cross-tenant results.
- AC-4 Deletion test: after hard delete, the memory is absent from search, export, embeddings and graph edges.
- AC-5 Swap test: switch the LLM, then the embedder, then the store adapter, by config only. The contract suite passes and the eval result is reported for each.
- AC-6 Token budget is never exceeded across 1,000 randomized queries.
- AC-7 The LangGraph hook runs end to end in a local LangGraph agent with flat memory injection replaced.

## 12. Success metrics (after a backend adopts it)

- Prompt tokens spent on memory per chat is lower than flat injection at 50+ memories.
- Users repeat a preference or fact less often (tracked by dedup-hit rate on `add`).
- Zero cross-tenant incidents. Zero stale-fact complaints.

## 13. Milestones (for the one-shot build, in order)

1. Core types, ports, config loader, contract tests.
2. Postgres adapters, schema, migrations, RLS.
3. Ingest pipeline: extraction, dedup, contradiction, versioning.
4. Retrieval: hybrid search, fusion, packing, profile, `as_of`.
5. REST, MCP server, Python SDK, LangGraph hook.
6. Deletion, export, audit, rate limits, cost tracking.
7. Eval harness and acceptance run.
8. P1 items.

## 14. Risks

| Risk | Mitigation |
|---|---|
| Extraction quality varies by model | Versioned prompts, schema validation, eval suite per adapter |
| Contradiction judge errs and wrongly supersedes | Keep history, allow `restore`, log the verdict and reason |
| Prompt injection stored as a "memory" | Treat memory as untrusted data, render in delimited blocks, no instruction-following on recalled text |
| Re-index cost on embedder change | Background, resumable, and gated by a per-tenant budget |
| Scope creep into a full agent platform | Non-goals above |

## 15. Decisions needed before build

1. Name. **[assumed]** "Memory Core" as a working title.
2. Where the code lives. **[assumed]** a new standalone repo.
3. Is multi-tenant needed on day one, or is the host application's user and account model enough? **[assumed]** Multi-tenant from day one, since retrofitting is expensive.
4. Default LLM and embedder for the first eval run. **[assumed]** Anthropic for extraction and an OpenAI-style embedder.
5. Do we migrate an existing flat memories table in the first release (FR-24)? **[assumed]** P1.
