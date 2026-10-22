# Memory Core Implementation Plan

**Goal:** Build Memory Core, a modular, model-agnostic memory engine (extract, version, hybrid-recall, delete) with REST, MCP, Python SDK and LangGraph interfaces.

**Architecture:** Core logic depends only on Protocol "ports" (`memory_core/ports.py`). Adapters (Postgres+pgvector, Anthropic/OpenAI/OpenAI-compatible LLMs, embedders, queue) are chosen by config and certified by a shared contract suite. Ingest is async: queue → extract → dedup/contradiction → embed → index. Retrieval is vector + FTS fused by RRF, packed to a token budget.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, Postgres 16 + pgvector, pytest + pytest-asyncio, `mcp` Python SDK, LangGraph (hook only).

**Spec:** `memory-core-prd.md` (approved with all section-15 defaults). Requirement ids (FR-n, AC-n) below refer to it.

## Global Constraints

- New standalone repo `memory-core/`, **outside** `/Users/shura/Venxr`. Nothing in Venxr is read-write touched.
- Tests use a **local** Docker Postgres (`pgvector/pgvector:pg16`). Never create a database or role on any server.
- No `git commit` or `git push` unless the user asks. Steps end at "tests pass".
- Core never imports an adapter or a vendor SDK. Only `memory_core/adapters/*` and `memory_core/bootstrap.py` do.
- Every table has a `tenant` column; RLS is on for all of them. Every port call takes a `Scope` or tenant.
- Prompts and the extraction JSON schema live in `memory_core/prompts/v1/` files. Each memory stores `extractor_version`.
- Recalled text is always rendered inside `<memory>` delimiters and described as untrusted data.
- No memory text in logs. No vendor name in docs, comments or prompts shipped to users.
- Defaults: Anthropic LLM, OpenAI-style embedder, in-process queue, `search` p95 < 300 ms at 100k memories (PRD §8).

## Review Focus

1. Empty or whitespace-only `content` / `query`: reject with 422, never store or embed. (Task 6, 7)
2. Same document added twice concurrently: exactly one document and one set of memories. (Task 6)
3. LLM returns malformed or off-schema JSON: retry once, then mark the document `failed`, store nothing partial. (Task 4)
4. Embedder `dim` mismatches the vector column or `model_id` changes mid-flight: refuse to mix, trigger re-index. (Task 2, 7)
5. `token_budget` smaller than the smallest memory: return an empty `context`, not an overrun. (Task 7)

## File Structure

```
memory-core/
  pyproject.toml  docker-compose.yml  alembic.ini  config.example.yaml
  memory_core/
    types.py  ports.py  config.py  bootstrap.py  tokens.py
    prompts/v1/{extract.md,judge.md,schema.json}
    adapters/
      llm_{anthropic,openai,compat}.py   embed_{openai,local}.py
      pg_{docstore,memstore}.py          queue_{inproc,redis}.py
      parser_text.py                     auth_apikey.py
    ingest/{extract.py,dedup.py,truth.py,pipeline.py}
    retrieval/{search.py,fuse.py,pack.py,profile.py}
    manage/{crud.py,delete.py,export.py,audit.py}
    api/{app.py,deps.py,routes.py,limits.py,cost.py}
    mcp_server.py  sdk.py  integrations/langgraph.py
  migrations/versions/0001_init.py
  tests/{contract/,unit/,integration/,eval/}
```

---

### Task 1: Types, ports, config, contract-test scaffolding

**Files:** Create `pyproject.toml`, `docker-compose.yml`, `memory_core/{types,ports,config,tokens}.py`, `tests/contract/base.py`, `tests/unit/test_types_config.py`

**Interfaces:**
- Produces (`types.py`): `Scope(tenant:str, subject:str, container:str|None=None)` (frozen); `Kind` StrEnum (`fact, preference, decision, episode, procedure, relationship`); `DocStatus` (`queued, extracting, indexed, failed`); `Verdict` (`new, duplicate, updates, contradicts`); dataclasses `Candidate(text, kind, entities, confidence, valid_from|None)`, `Memory(id, scope, text, kind, entities, confidence, importance, valid_from, valid_to, supersedes, source_doc_ids, extractor_version, created_at, last_accessed)`, `Document(id, scope, content, hash, status, meta)`, `Hit(memory, score)`, `SearchResult(context:str, results:list[Hit], tokens:int)`.
- Produces (`ports.py`, all `typing.Protocol`, async): `LLM.complete(messages:list[dict], schema:dict)->dict`; `Embedder` (attrs `model_id:str`, `dim:int`; `embed(texts:list[str])->list[list[float]]`); `DocStore` (`put(doc)->Document`, `get(tenant,id)`, `set_status(tenant,id,status,error=None)`, `delete_where(tenant,subject=None,doc_id=None)->int`); `MemoryStore` (`insert(mem, vector, model_id)`, `get(tenant,id)`, `list(scope,limit,offset)`, `close_validity(tenant,id,valid_to)`, `delete_where(...)->int`, `touch(tenant,ids)`); `VectorStore.query(scope, vector, model_id, k, filters, as_of)->list[tuple[str,float]]`; `KeywordIndex.query(scope, text, k, filters, as_of)->list[tuple[str,float]]`; `Queue` (`enqueue(job:dict)`, `consume(handler)`); `Parser.parse(data:bytes, mime:str)->str`; `Auth.resolve(headers:dict)->Principal(tenant, subject, scopes)`.
- Produces (`config.py`): `load_config(path:str|None)->Config`, YAML + env override; keys `llm.adapter`, `embedder.adapter`, `store.dsn`, `queue.adapter`, `auth.adapter`. (`tokens.py`): `count_tokens(text:str)->int`, default `ceil(len/4)`, replaceable via config.

- [ ] **Step 1:** Write `test_types_config.py`: `test_scope_is_hashable_and_requires_tenant` (empty tenant raises `ValueError`); `test_config_env_overrides_yaml`; `test_unknown_adapter_name_raises_at_load`.
- [ ] **Step 2:** Run `pytest tests/unit/test_types_config.py -v`. Expected: FAIL (modules missing).
- [ ] **Step 3:** Implement the types, ports and config as specified. Write `tests/contract/base.py` with abstract suites `LLMContract`, `EmbedderContract` (asserts `len(vec)==dim`, determinism per input), `StoreContract` (insert/get/delete round-trip, tenant A cannot read tenant B), `QueueContract` (at-least-once, retry on handler error). Adapters subclass these.
- [ ] **Step 4:** Run the same command. Expected: PASS.

### Task 2: Postgres schema, RLS, stores (FR-30, FR-32, AC-3)

**Files:** Create `migrations/versions/0001_init.py`, `adapters/pg_docstore.py`, `adapters/pg_memstore.py`, `tests/integration/test_pg_stores.py`; Modify `tests/contract/` to add `test_pg_contract.py`

**Interfaces:** Consumes Task 1 ports. Produces `PgDocStore(dsn)`, `PgMemStore(dsn, dim:int)` implementing `DocStore`, `MemoryStore`, `VectorStore`, `KeywordIndex`. Tables per PRD §10 (`tenants, api_keys, documents, memories, memory_sources, memory_links, entities, entity_edges, embeddings(memory_id, model_id, vector), audit_log, deletion_receipts`) plus `jobs_dead_letter`. Tenant is set per connection with `SET LOCAL app.tenant = :t`; RLS policy `tenant = current_setting('app.tenant')`.

- [ ] **Step 1:** Write `test_pg_stores.py`: `test_rls_blocks_cross_tenant_read_even_with_raw_sql`; `test_vector_query_only_matches_same_model_id`; `test_insert_with_wrong_dim_raises`; `test_keyword_query_respects_as_of` (memory with `valid_to` before `as_of` is excluded, one valid at `as_of` is included).
- [ ] **Step 2:** `docker compose up -d db` then `pytest tests/integration/test_pg_stores.py -v`. Expected: FAIL.
- [ ] **Step 3:** Write the Alembic migration (pgvector extension, HNSW index on vectors per `model_id`, GIN FTS index on `memories.text`, RLS enabled and forced on every table). Implement the two adapters. Run the Task 1 `StoreContract` against them.
- [ ] **Step 4:** Run integration and contract tests. Expected: PASS.

### Task 3: LLM and embedder adapters with fakes (FR-30, FR-31, AC-5)

**Files:** Create `adapters/llm_{anthropic,openai,compat}.py`, `adapters/embed_{openai,local}.py`, `tests/fakes.py`, `bootstrap.py`, `tests/unit/test_adapters.py`

**Interfaces:** Consumes `LLM`, `Embedder`, `Config`. Produces `build(config)->Container` where `Container` holds one instance of every port; `FakeLLM(script:list[dict])`, `FakeEmbedder(dim=8)` (hash-seeded deterministic vectors) in `tests/fakes.py`. `LLM.complete` enforces `schema` via the vendor's native structured-output or JSON mode, and raises `LLMSchemaError` on invalid output.

- [ ] **Step 1:** Write `test_adapters.py`: `test_bootstrap_builds_fakes_from_config`; `test_unknown_adapter_name_error_lists_valid_names`; each real adapter's contract test is marked `@pytest.mark.live` and skipped without its API key env var.
- [ ] **Step 2:** Run `pytest tests/unit/test_adapters.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement adapters (thin wrappers over each vendor SDK or plain `httpx` for the compat one) and `bootstrap.build`. Only `bootstrap.py` and `adapters/` import vendor SDKs; add a test `test_core_has_no_vendor_imports` that greps `ingest/`, `retrieval/`, `manage/`, `api/`.
- [ ] **Step 4:** Run. Expected: PASS (live tests skipped).

### Task 4: Extraction (FR-3, FR-33)

**Files:** Create `prompts/v1/{extract.md,schema.json}`, `ingest/extract.py`, `tests/unit/test_extract.py`

**Interfaces:** Consumes `LLM`. Produces `async extract(llm:LLM, content:str, as_of:datetime)->tuple[list[Candidate], str]` returning candidates plus `extractor_version` (`"v1"`, read from the prompt directory name).

- [ ] **Step 1:** Write tests with `FakeLLM`: `test_extract_returns_atomic_candidates_with_kind`; `test_malformed_json_retries_once_then_raises_ExtractionFailed` (FakeLLM called exactly twice); `test_off_schema_kind_is_rejected`; `test_relative_dates_resolved_against_as_of` ("next month" with `as_of=2026-10-22` gives `valid_from` in November 2026).
- [ ] **Step 2:** Run `pytest tests/unit/test_extract.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `extract` and the prompt files. Validate output with `jsonschema` against `schema.json`.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 5: Dedup, contradiction, versioning (FR-4, FR-8, FR-9, FR-12)

**Files:** Create `ingest/{dedup.py,truth.py}`, `prompts/v1/judge.md`, `tests/unit/test_truth.py`, `tests/integration/test_truth_pg.py`

**Interfaces:** Consumes `VectorStore`, `MemoryStore`, `LLM`, `Embedder`. Produces `async resolve(cand:Candidate, vec:list[float], scope:Scope, c:Container)->tuple[Verdict, list[str]]` (verdict plus affected memory ids) and `async apply(verdict, ids, cand, vec, scope, doc_id, c)->Memory|None`. Neighbours: top 5 within the same scope above cosine 0.80. Duplicate raises confidence of the existing memory, inserts nothing. Updates/contradicts inserts the new memory, sets `valid_to=now` on the old, fills `supersedes`.

- [ ] **Step 1:** Write tests: `test_exact_restatement_is_duplicate_and_bumps_confidence`; `test_budget_change_supersedes_old_and_keeps_it_in_history`; `test_no_neighbours_returns_new_without_calling_llm`; `test_judge_failure_defaults_to_new_and_logs_reason` (never silently supersede); `test_superseded_memory_not_in_default_list`.
- [ ] **Step 2:** Run both test files. Expected: FAIL.
- [ ] **Step 3:** Implement. The judge prompt returns `{verdict, target_id, reason}`; persist `reason` in `audit_log`.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 6: Queue and ingest pipeline (FR-1, FR-2)

**Files:** Create `adapters/{queue_inproc,queue_redis,parser_text}.py`, `ingest/pipeline.py`, `tests/integration/test_pipeline.py`

**Interfaces:** Consumes Tasks 2-5. Produces `async add(c:Container, scope:Scope, content:str|list[dict], metadata:dict|None=None, idempotency_key:str|None=None)->Document` (status `queued`, returns fast), and the queue handler `async process(c, job:dict)` running extract → embed → resolve/apply → mark `indexed`. Idempotency key defaults to `sha256(tenant+subject+normalized content)`. Handler failure retries 3 times with backoff, then `failed` plus a `jobs_dead_letter` row. `add` rejects blank content with `ValueError`.

- [ ] **Step 1:** Write tests: `test_add_returns_queued_immediately`; `test_same_content_twice_creates_one_document`; `test_concurrent_adds_create_one_document` (20 `asyncio.gather` calls); `test_blank_content_rejected`; `test_failed_extraction_marks_failed_and_stores_no_memories`; `test_chat_message_list_is_flattened_with_roles`.
- [ ] **Step 2:** Run `pytest tests/integration/test_pipeline.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement. Use the unique constraint on `(tenant, hash)` plus `INSERT ... ON CONFLICT DO NOTHING RETURNING` for idempotency.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 7: Retrieval (FR-13 to FR-15, FR-18, FR-19, FR-10)

**Files:** Create `retrieval/{fuse,pack,search,profile}.py`, `tests/unit/test_fuse_pack.py`, `tests/integration/test_search.py`

**Interfaces:** Produces `rrf(rankings:list[list[str]], k:int=60)->list[tuple[str,float]]`; `pack(hits:list[Hit], budget:int, count=count_tokens)->tuple[str,list[Hit],int]` (renders `<memory id=… kind=… valid_from=…>text</memory>` lines); `async search(c, scope, query:str, k:int=10, token_budget:int=1500, filters:dict|None=None, as_of:datetime|None=None, include_history:bool=False)->SearchResult`; `async profile(c, scope)->SearchResult` (static = kinds `fact`+`preference` with confidence ≥ 0.7 and age > 7 days; dynamic = last 10 others). Search embeds the query with the embedder's current `model_id` and only searches vectors of that `model_id`.

- [ ] **Step 1:** Write tests: `test_rrf_ranks_item_present_in_both_lists_first`; `test_pack_never_exceeds_budget_over_1000_random_inputs` (AC-6); `test_budget_smaller_than_smallest_memory_returns_empty_context`; `test_blank_query_raises_ValueError`; `test_as_of_returns_old_fact_not_new` (AC-2 update set); `test_search_excludes_superseded_unless_include_history`; `test_filters_by_kind_entity_container`; `test_context_wraps_each_memory_in_delimiters`; `test_other_embedder_model_vectors_ignored`.
- [ ] **Step 2:** Run both files. Expected: FAIL.
- [ ] **Step 3:** Implement. Run vector and keyword queries with `asyncio.gather`, fuse, load memories, call `MemoryStore.touch` on returned ids.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 8: Management, deletion, export, audit (FR-20 to FR-22, AC-4)

**Files:** Create `manage/{crud,delete,export,audit}.py`, `tests/integration/test_manage.py`

**Interfaces:** Produces `get_memory`, `list_memories(scope, limit, offset, include_history)`, `edit_memory(tenant, id, text)->Memory` (creates a new version via supersedes), `async hard_delete(c, tenant, *, memory_id=None, doc_id=None, subject=None)->DeletionReceipt(id, counts:dict, at)`; `export_scope(c, scope)->dict`; `audit(c, tenant, actor, action, target, detail=None)`.

- [ ] **Step 1:** Write tests: `test_hard_delete_removes_from_search_export_embeddings_and_edges` (AC-4, asserts raw row counts are 0); `test_delete_by_doc_removes_only_its_memories`; `test_delete_subject_leaves_other_subjects`; `test_edit_creates_new_version_and_hides_old`; `test_export_contains_history`; `test_every_mutation_writes_audit_row`; `test_receipt_stored_and_retrievable`.
- [ ] **Step 2:** Run `pytest tests/integration/test_manage.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement in one transaction per operation.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 9: REST API, auth, limits, cost (FR-25, NFR)

**Files:** Create `api/{app,deps,routes,limits,cost}.py`, `adapters/auth_apikey.py`, `tests/integration/test_api.py`

**Interfaces:** Routes: `POST /v1/memories` (add), `GET /v1/documents/{id}`, `POST /v1/search`, `GET /v1/profile`, `GET/PATCH/DELETE /v1/memories/{id}`, `POST /v1/forget`, `GET /v1/export`, `GET /healthz`. API keys are stored as sha256 hashes. Tenant comes only from the resolved `Principal`, never the request body. `limits.py`: token bucket per tenant. `cost.py`: records LLM and embed tokens per tenant per day, returns 429 past `tenant.monthly_budget`.

- [ ] **Step 1:** Write tests with `httpx.AsyncClient`: `test_missing_key_401`; `test_body_tenant_field_is_ignored`; `test_tenant_a_key_cannot_get_tenant_b_memory_404`; `test_rate_limit_returns_429_with_retry_after`; `test_budget_cap_returns_429`; `test_openapi_schema_served`; `test_blank_content_422`; `test_isolation_fuzz` (500 random cross-tenant reads/searches, all empty, AC-3).
- [ ] **Step 2:** Run `pytest tests/integration/test_api.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement; each request opens its connection with `SET LOCAL app.tenant`.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 10: MCP server and Python SDK (FR-26, FR-27)

**Files:** Create `mcp_server.py`, `sdk.py`, `tests/integration/test_mcp_sdk.py`

**Interfaces:** MCP tools `memory_add(content, container?)`, `memory_search(query, k?, token_budget?)`, `memory_profile()`, `memory_forget(memory_id)`; the tenant and subject come from the server's API key, not tool arguments. SDK: `class MemoryClient(base_url, api_key)` with `async add(...)`, `search(...)`, `profile(...)`, `forget(...)`, `export(...)`, returning the same dataclasses as Task 1.

- [ ] **Step 1:** Write tests: `test_mcp_lists_four_tools`; `test_mcp_add_then_search_round_trip`; `test_sdk_round_trip_against_test_app`; `test_mcp_tool_args_cannot_override_tenant`.
- [ ] **Step 2:** Run `pytest tests/integration/test_mcp_sdk.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement both as thin wrappers over the API layer or core functions.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 11: LangGraph integration (FR-28, AC-7)

**Files:** Create `integrations/langgraph.py`, `tests/integration/test_langgraph.py`

**Interfaces:** `remember_tool(client:MemoryClient, scope_fn)->BaseTool`; `memory_hook(client, scope_fn, token_budget=1500)->Callable[[dict], dict]`, a pre-model node that sets `state["memory_context"]` from `profile + search(last user message)`.

- [ ] **Step 1:** Write a test using a tiny two-node LangGraph agent with `FakeLLM`: `test_fact_from_chat_one_is_in_prompt_of_chat_two`; `test_hook_respects_token_budget`; `test_hook_failure_degrades_to_empty_context_not_crash`.
- [ ] **Step 2:** Run `pytest tests/integration/test_langgraph.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run. Expected: PASS.

### Task 12: Eval harness and acceptance (AC-1 to AC-6)

**Files:** Create `tests/eval/{dataset.jsonl,run_eval.py,test_acceptance.py}`, `tests/contract/test_swap.py`, `README.md`

**Interfaces:** `run_eval(container, dataset_path)->dict` with `recall_at_5, correct_after_update, abstention, stale_leak`. Dataset: ≥ 200 synthetic ads-domain conversations (single-fact, preference, update, multi-session, temporal, abstention). `test_swap.py` runs the full eval with each LLM, embedder and store adapter selected by config only.

- [ ] **Step 1:** Write `test_acceptance.py`: asserts the PRD targets (recall@5 ≥ 0.85, correct-after-update ≥ 0.90, abstention ≥ 0.80, `stale_leak == 0`) and `test_swap_changes_only_config`.
- [ ] **Step 2:** Generate the dataset (a script using the live LLM, reviewed by you before it is trusted). Run `pytest tests/eval -v -m live`. Expected: FAIL until quality targets are met.
- [ ] **Step 3:** Tune the prompts in `prompts/v1/` and the neighbour threshold until targets pass. Record results per adapter in `README.md`.
- [ ] **Step 4:** Run the whole suite `pytest -q` and `pytest -m live`. Expected: PASS.

---

## Out of scope for this plan (P1/P2 in the PRD)

File and URL ingestion, decay, rerank, multi-hop, inspector UI, v3 migration, webhooks, TS SDK, connectors.

## Self-review

- Coverage: all P0 requirements map to Tasks 1-12 (FR-1..4, 8..10, 13..15, 18..22, 25..28, 30..34). FR-5..7, 11, 12 (confidence bump is in Task 5), 16, 17, 23, 24, 29 are P1/P2 and listed out of scope.
- Types are the ones defined in Task 1 throughout. `Container` is defined in Task 3 and used from Task 5 on.
- Known risk: Task 12's targets are my guesses and may need prompt tuning, which is why that task is open-ended on Step 3.
