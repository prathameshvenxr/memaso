<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img src="assets/logo-light.svg" alt="Memaso" height="52">
  </picture>
</p>

<h3 align="center">Memory for AI agents that stays true as things change.</h3>

<p align="center">
  Stores what was said, keeps the facts inside it dated, and recalls a small ranked block within a token budget.<br>
  No model is required. Bring your own when you want one.
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> &nbsp;·&nbsp;
  <a href="#how-it-works">How it works</a> &nbsp;·&nbsp;
  <a href="#use-it-inside-the-v3-backend">Library mode</a> &nbsp;·&nbsp;
  <a href="#run-it-as-a-service">Service</a> &nbsp;·&nbsp;
  <a href="#swap-anything">Configuration</a> &nbsp;·&nbsp;
  <a href="#guarantees-and-known-limits">Limits</a>
</p>

<p align="center">
  <img alt="python 3.12" src="https://img.shields.io/badge/python-3.12-1A1630?style=flat-square">
  <img alt="postgres + pgvector" src="https://img.shields.io/badge/postgres-pgvector-6A4CFF?style=flat-square">
  <img alt="170 tests" src="https://img.shields.io/badge/tests-170_passing-0F8F83?style=flat-square">
  <img alt="no model required" src="https://img.shields.io/badge/LLM-optional-F0397A?style=flat-square">
</p>

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.svg">
    <img src="assets/hero-light.svg" alt="A conversation is stored twice, as verbatim chunks and as dated facts. A later statement closes the old fact, and search returns the current fact plus the surviving words of old text." width="100%">
  </picture>
</p>

## What it does

- **Remembers the words and the facts.** Every turn is kept verbatim (searchable, nothing lost to a summary) and mined for facts that carry `valid_from` and `valid_to`, so "the budget is 80k" closes "the budget is 50k" instead of sitting beside it.
- **Recalls cheaply.** Search is embeddings plus full-text, fused, then packed to the token budget you give it. It makes no model call.
- **Uses a model only where one helps.** Plain statements are read by rules. A model (your app's own, or one you configure) is asked only when a case is genuinely ambiguous.
- **Stays inside the lines.** Row-level tenant isolation, account scoping, real deletion, and recalled text that is escaped and labelled as untrusted data.
- **Fits the AdsGPT v3 backend.** Library mode runs in-process on v3's Postgres, in its own schema, with hooks for the run loop, compaction and the tool registry.

## How it works

### Architecture

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EFEBFF","primaryBorderColor":"#6A4CFF","primaryTextColor":"#1A1630","secondaryColor":"#E3F5F2","tertiaryColor":"#F6F5FB","lineColor":"#8A86A6","clusterBkg":"#F6F5FB","clusterBorder":"#B3AFC6","titleColor":"#1A1630","edgeLabelBackground":"#F6F5FB","actorBkg":"#EFEBFF","actorBorder":"#6A4CFF","actorTextColor":"#1A1630","actorLineColor":"#8A86A6","signalColor":"#8A86A6","signalTextColor":"#8A86A6","labelBoxBkgColor":"#EFEBFF","labelBoxBorderColor":"#6A4CFF","labelTextColor":"#1A1630","loopTextColor":"#8A86A6","noteBkgColor":"#FFE9F1","noteBorderColor":"#F0397A","noteTextColor":"#1A1630","sequenceNumberColor":"#FFFFFF"}}}%%
flowchart LR
  subgraph clients[" Clients "]
    direction TB
    LIB["Library<br/>Memory.create(cfg, llm=call_llm)"]
    REST["REST + Python SDK"]
    MCP["MCP server<br/>4 tools"]
    LG["LangGraph hook<br/>remember / context"]
  end
  subgraph core[" memory_core "]
    direction TB
    API["API layer<br/>keys · rate limit · token budget"]
    ING["Ingest<br/>chunk · embed · extract · judge"]
    RET["Recall<br/>hybrid search · fuse · pack"]
    MAN["Manage<br/>edit · forget · export"]
  end
  subgraph ports[" Ports: every one swappable by config "]
    direction TB
    LLM[["LLM<br/>none · host · anthropic · openai · compat"]]
    EMB[["Embedder<br/>fastembed (local) · openai"]]
    STO[["Store<br/>postgres + pgvector"]]
    QUE[["Queue<br/>in-process · redis"]]
    AUTH[["Auth · Parser"]]
  end
  LIB --> ING
  LIB --> RET
  REST --> API
  MCP --> API
  LG --> REST
  API --> ING
  API --> RET
  API --> MAN
  ING --> LLM
  ING --> EMB
  ING --> STO
  ING --> QUE
  RET --> EMB
  RET --> STO
  MAN --> STO
  API --> AUTH
  classDef verb fill:#E3F5F2,stroke:#0F8F83,color:#1A1630
  classDef fact fill:#EFEBFF,stroke:#6A4CFF,color:#1A1630
  classDef now fill:#FFE3EE,stroke:#F0397A,color:#1A1630
  classDef port fill:#FFFFFF,stroke:#6B6785,stroke-dasharray:4 3,color:#1A1630
  classDef plain fill:#F6F5FB,stroke:#B3AFC6,color:#1A1630
  class LIB,REST,MCP,LG plain
  class API,ING,RET,MAN fact
  class LLM,EMB,STO,QUE,AUTH port
```

Core code depends on the ports only. `tests/unit/test_import_discipline.py` fails the build if a core file imports an adapter or a vendor SDK.

### A conversation, end to end

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EFEBFF","primaryBorderColor":"#6A4CFF","primaryTextColor":"#1A1630","secondaryColor":"#E3F5F2","tertiaryColor":"#F6F5FB","lineColor":"#8A86A6","clusterBkg":"#F6F5FB","clusterBorder":"#B3AFC6","titleColor":"#1A1630","edgeLabelBackground":"#F6F5FB","actorBkg":"#EFEBFF","actorBorder":"#6A4CFF","actorTextColor":"#1A1630","actorLineColor":"#8A86A6","signalColor":"#8A86A6","signalTextColor":"#8A86A6","labelBoxBkgColor":"#EFEBFF","labelBoxBorderColor":"#6A4CFF","labelTextColor":"#1A1630","loopTextColor":"#8A86A6","noteBkgColor":"#FFE9F1","noteBorderColor":"#F0397A","noteTextColor":"#1A1630","sequenceNumberColor":"#FFFFFF"}}}%%
sequenceDiagram
  autonumber
  participant C as Client
  participant A as API
  participant Q as Queue
  participant W as Worker
  participant S as Postgres (schema memaso)
  C->>A: add("Actually the budget is 80k")
  A->>S: put document (idempotent on tenant + content)
  A->>Q: enqueue job
  A-->>C: 202 queued
  Q->>W: job
  W->>S: take the subject's advisory lock
  Note over W,S: older pending documents of this subject run first
  W->>W: split into ~800 character chunks
  W->>S: store chunks + local embeddings (no model call)
  W->>W: facts by rules, or by the model if facts.extract = llm
  loop each candidate fact
    W->>S: neighbours in the same account (cosine 0.40 or more)
    W->>W: rules judge, a model only if ambiguous
    W->>S: insert new fact, link, close the old one (one transaction)
  end
  W->>S: mark document indexed
```

### Facts that know when they stopped being true

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EFEBFF","primaryBorderColor":"#6A4CFF","primaryTextColor":"#1A1630","secondaryColor":"#E3F5F2","tertiaryColor":"#F6F5FB","lineColor":"#8A86A6","clusterBkg":"#F6F5FB","clusterBorder":"#B3AFC6","titleColor":"#1A1630","edgeLabelBackground":"#F6F5FB","actorBkg":"#EFEBFF","actorBorder":"#6A4CFF","actorTextColor":"#1A1630","actorLineColor":"#8A86A6","signalColor":"#8A86A6","signalTextColor":"#8A86A6","labelBoxBkgColor":"#EFEBFF","labelBoxBorderColor":"#6A4CFF","labelTextColor":"#1A1630","loopTextColor":"#8A86A6","noteBkgColor":"#FFE9F1","noteBorderColor":"#F0397A","noteTextColor":"#1A1630","sequenceNumberColor":"#FFFFFF"}}}%%
flowchart LR
  C(["candidate fact"]) --> N["neighbours?<br/>same account<br/>cosine ≥ 0.40"]
  N -- none --> NEW["new fact"]
  N -- some --> D["same text?"]
  D -- yes --> DUP["duplicate<br/>confidence + 0.1"]
  D -- no --> P["a preference?"]
  P -- "opposite verb,<br/>same thing" --> CON["contradicts<br/>supersede"]
  P -- "any other<br/>preference" --> AMB
  P -- "plain 'X is Y'" --> K["same subject?"]
  K -- "same attribute" --> UPD["updates<br/>supersede"]
  K -- "different attribute" --> NEW
  K -- "overlapping<br/>budget / monthly budget" --> AMB
  AMB["ambiguous"] -- "a model is available" --> LLMJ["ask it once<br/>candidate + 5 neighbours"]
  AMB -- "no model" --> KEEP["keep both<br/>never guess"]
  classDef verb fill:#E3F5F2,stroke:#0F8F83,color:#1A1630
  classDef fact fill:#EFEBFF,stroke:#6A4CFF,color:#1A1630
  classDef now fill:#FFE3EE,stroke:#F0397A,color:#1A1630
  classDef plain fill:#F6F5FB,stroke:#B3AFC6,color:#1A1630
  class NEW,DUP,KEEP plain
  class UPD,CON now
  class LLMJ verb
  class N,D,P,K,AMB fact
```

A fact is never overwritten. The old one keeps its history (`valid_to` is set), so `as_of` queries can still answer "what did we think on March 1" and `include_history` shows everything. Forgetting a value never brings an older one back.

### Recall

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EFEBFF","primaryBorderColor":"#6A4CFF","primaryTextColor":"#1A1630","secondaryColor":"#E3F5F2","tertiaryColor":"#F6F5FB","lineColor":"#8A86A6","clusterBkg":"#F6F5FB","clusterBorder":"#B3AFC6","titleColor":"#1A1630","edgeLabelBackground":"#F6F5FB","actorBkg":"#EFEBFF","actorBorder":"#6A4CFF","actorTextColor":"#1A1630","actorLineColor":"#8A86A6","signalColor":"#8A86A6","signalTextColor":"#8A86A6","labelBoxBkgColor":"#EFEBFF","labelBoxBorderColor":"#6A4CFF","labelTextColor":"#1A1630","loopTextColor":"#8A86A6","noteBkgColor":"#FFE9F1","noteBorderColor":"#F0397A","noteTextColor":"#1A1630","sequenceNumberColor":"#FFFFFF"}}}%%
flowchart LR
  Q(["query + token_budget"]) --> E["embed locally"]
  E --> FV["facts<br/>vector"]
  Q --> FK["facts<br/>full-text"]
  E --> CV["chunks<br/>vector"]
  Q --> CK["chunks<br/>full-text"]
  FV --> F["rank fusion<br/>weights 1 · 1 · 0.8 · 0.8"]
  FK --> F
  CV --> F
  CK --> F
  F --> R["redact superseded words<br/>inside chunks"]
  R --> P["pack to the token budget<br/>escaped, labelled untrusted"]
  P --> O(["context block"])
  FLOOR["cosine floor 0.30<br/>weak matches are dropped"] -.-> F
  classDef verb fill:#E3F5F2,stroke:#0F8F83,color:#1A1630
  classDef fact fill:#EFEBFF,stroke:#6A4CFF,color:#1A1630
  classDef now fill:#FFE3EE,stroke:#F0397A,color:#1A1630
  classDef port fill:#FFFFFF,stroke:#6B6785,stroke-dasharray:4 3,color:#1A1630
  classDef plain fill:#F6F5FB,stroke:#B3AFC6,color:#1A1630
  class FV,FK fact
  class CV,CK verb
  class O now
  class FLOOR plain
```

A chunk whose words were superseded stays searchable: only the superseded span is replaced by `[superseded]`, so the rest of the chunk is never hidden.

## Quick start

```bash
docker compose up -d                    # Postgres + pgvector, and the API. No model, no key.
docker compose exec api python -m memory_core create-tenant acme
docker compose exec api python -m memory_core create-key acme --subject alice   # printed once
docker compose port api 8080            # REST here; OpenAPI at /openapi.json
```

```python
from memory_core.sdk import MemoryClient

m = MemoryClient("http://localhost:8080", "mc_...", subject="alice")
await m.add("Budget is 50k. I want weekly reports in INR.")
await m.add("Actually the budget is 80k.")             # closes the 50k fact
res = await m.search("what is the budget?")             # res.context goes into your prompt
```

The first start downloads a ~90 MB embedding model and caches it in a volume. Docker is optional: see [Run it as a service](#run-it-as-a-service).

## Use it inside the v3 backend

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EFEBFF","primaryBorderColor":"#6A4CFF","primaryTextColor":"#1A1630","secondaryColor":"#E3F5F2","tertiaryColor":"#F6F5FB","lineColor":"#8A86A6","clusterBkg":"#F6F5FB","clusterBorder":"#B3AFC6","titleColor":"#1A1630","edgeLabelBackground":"#F6F5FB","actorBkg":"#EFEBFF","actorBorder":"#6A4CFF","actorTextColor":"#1A1630","actorLineColor":"#8A86A6","signalColor":"#8A86A6","signalTextColor":"#8A86A6","labelBoxBkgColor":"#EFEBFF","labelBoxBorderColor":"#6A4CFF","labelTextColor":"#1A1630","loopTextColor":"#8A86A6","noteBkgColor":"#FFE9F1","noteBorderColor":"#F0397A","noteTextColor":"#1A1630","sequenceNumberColor":"#FFFFFF"}}}%%
sequenceDiagram
  autonumber
  participant U as User
  participant R as v3 run (LangGraph)
  participant M as Memaso
  participant L as call_llm (the app's own model)
  U->>R: question
  R->>M: context(scope, question, 1500)
  M-->>R: small ranked block, no model call
  Note over R: replaces "inject every memory"
  R->>L: prompt + memory block + tools
  L-->>R: tool call remember("CPA target is 420")
  R->>M: remember(scope, fact)
  Note over M: no extraction call, the agent already did it
  L-->>R: answer
  R->>M: ingest_turn(scope, messages)
  Note over M: stored verbatim, facts by rules, queued
  R-->>U: answer
  Note over R,M: window past 55%: archive(turns) first
```

```python
from memory_core.memory import Memory
from memory_core.types import Scope

mem = await Memory.create("config.yaml", llm=call_llm)    # llm=None: no model at all
scope = Scope(tenant=team_id, subject=user_id, container=ad_account_id)

block = await mem.context(scope, user_text, token_budget=1500)   # replaces "inject every memory"
await mem.remember(scope, "CPA target is 420", kind="fact")      # the agent's own tool call
await mem.ingest_turn(scope, messages)                           # after the answer: verbatim, queued
await mem.archive(scope, old_turns)                              # before compaction summarises them
```

`tenant` is the team, `subject` the user, `container` the ad account. A fact stored with no container is global and shows up in every account; an account fact stays in its account, and supersession only compares facts inside one account. `memory_core.integrations.v3` returns `remember` and `recall` tool specs shaped for v3's `@tool` decorator, plus a compaction hook. They were written from the white paper and have not been run against v3's code.

## Run it as a service

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EFEBFF","primaryBorderColor":"#6A4CFF","primaryTextColor":"#1A1630","secondaryColor":"#E3F5F2","tertiaryColor":"#F6F5FB","lineColor":"#8A86A6","clusterBkg":"#F6F5FB","clusterBorder":"#B3AFC6","titleColor":"#1A1630","edgeLabelBackground":"#F6F5FB","actorBkg":"#EFEBFF","actorBorder":"#6A4CFF","actorTextColor":"#1A1630","actorLineColor":"#8A86A6","signalColor":"#8A86A6","signalTextColor":"#8A86A6","labelBoxBkgColor":"#EFEBFF","labelBoxBorderColor":"#6A4CFF","labelTextColor":"#1A1630","loopTextColor":"#8A86A6","noteBkgColor":"#FFE9F1","noteBorderColor":"#F0397A","noteTextColor":"#1A1630","sequenceNumberColor":"#FFFFFF"}}}%%
flowchart LR
  USER(["agents · apps"]) --> PROXY["reverse proxy"]
  PROXY --> API["api × N<br/>stateless<br/>local ONNX embedder"]
  API --> PG[("postgres 16 + pgvector<br/>schema memaso<br/>row-level security")]
  API --> REDIS[("redis<br/>job queue<br/>when replicas > 1")]
  API -. "optional" .-> HOST["the app's own model<br/>call_llm"]
  classDef verb fill:#E3F5F2,stroke:#0F8F83,color:#1A1630
  classDef fact fill:#EFEBFF,stroke:#6A4CFF,color:#1A1630
  classDef port fill:#FFFFFF,stroke:#6B6785,stroke-dasharray:4 3,color:#1A1630
  classDef plain fill:#F6F5FB,stroke:#B3AFC6,color:#1A1630
  class API fact
  class REDIS,PG verb
  class HOST port
  class PROXY,USER plain
```

```bash
MEMASO_QUEUE=redis docker compose --profile scale up -d --scale api=3   # Redis queue + three replicas
```

- **Stateless API.** Replicas share Postgres and the queue. A per-subject advisory lock keeps one subject's documents ordered and safe across workers.
- **Without Docker.** `python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"`, start a Postgres with `pgvector` and an unprivileged `memaso_app` role (`docker/initdb.sql`), copy `config.example.yaml` to `config.yaml`, then `python -m memory_core migrate | create-tenant | create-key | serve`.
- **MCP.** `MEMASO_API_KEY=mc_... MEMASO_SUBJECT=alice python -m memory_core mcp`.
- **LangGraph.** `memory_core.integrations.langgraph.memory_hook` and `remember_tool`.

## Swap anything

| Port | Adapters | Config |
|---|---|---|
| LLM | `none` · `host` (the app's own) · `anthropic` · `openai` · `compat` | `llm.adapter` |
| Embedder | `fastembed` (local ONNX) · `openai` | `embedder.adapter` |
| Store | `postgres` | `store.adapter` |
| Queue | `inproc` · `redis` | `queue.adapter` |
| Auth · Parser | `apikey` · `text` | `auth.adapter` · `parser.adapter` |

Edit `config.yaml`, or set `MEMASO__LLM__ADAPTER=openai` style variables. A third-party adapter plugs in as `adapter: "package.module:factory"` with no change to this repository. To certify one, subclass the suites in `tests/contract/base.py`. After an embedder change, ingestion re-embeds old data before judging new data, and vectors are tagged with the embedder's `model_id` so old and new never mix.

```yaml
llm:      {adapter: none}                 # or host / anthropic / openai / compat
embedder: {adapter: fastembed}
facts:    {extract: rules}                # rules (no model) | llm | none
truth:    {judge: auto, neighbour_threshold: 0.40}
retrieval: {min_similarity: 0.30}
```

## Data model

```mermaid
%%{init: {"theme":"base","themeVariables":{"primaryColor":"#EFEBFF","primaryBorderColor":"#6A4CFF","primaryTextColor":"#1A1630","secondaryColor":"#E3F5F2","tertiaryColor":"#F6F5FB","lineColor":"#8A86A6","clusterBkg":"#F6F5FB","clusterBorder":"#B3AFC6","titleColor":"#1A1630","edgeLabelBackground":"#F6F5FB","actorBkg":"#EFEBFF","actorBorder":"#6A4CFF","actorTextColor":"#1A1630","actorLineColor":"#8A86A6","signalColor":"#8A86A6","signalTextColor":"#8A86A6","labelBoxBkgColor":"#EFEBFF","labelBoxBorderColor":"#6A4CFF","labelTextColor":"#1A1630","loopTextColor":"#8A86A6","noteBkgColor":"#FFE9F1","noteBorderColor":"#F0397A","noteTextColor":"#1A1630","sequenceNumberColor":"#FFFFFF"}}}%%
erDiagram
  TENANTS ||--o{ API_KEYS : has
  DOCUMENTS ||--o{ CHUNKS : "split into"
  DOCUMENTS ||--o{ MEMORY_SOURCES : cites
  MEMORIES ||--o{ MEMORY_SOURCES : "derived from"
  MEMORIES ||--o{ MEMORY_LINKS : supersedes
  MEMORIES ||--o{ EMBEDDINGS : "vector per model_id"
  MEMORIES ||--o{ MEMORY_CHUNKS : "found in"
  CHUNKS ||--o{ MEMORY_CHUNKS : "holds"
  DOCUMENTS {
    text id PK
    text tenant
    text subject
    text container
    text status "queued extracting indexed failed"
    timestamptz created_at "observed time"
  }
  CHUNKS {
    text id PK
    text document_id FK
    text text
    timestamptz observed_at
    vector vector
    text model_id
  }
  MEMORIES {
    text id PK
    text text
    text kind "fact preference decision"
    real confidence
    timestamptz valid_from
    timestamptz valid_to "null while current"
  }
  MEMORY_CHUNKS {
    text memory_id FK
    text chunk_id FK
    bool exact "own words found in the chunk"
  }
```

Everything lives in the `memaso` schema (the `vector` extension in `public`), so it can share a database with v3's own tables. Each memory also has `memory_links` (supersession history with the judge's reason) and `embeddings` (one vector per `model_id`). Operational tables, `audit_log`, `deletion_receipts`, `jobs_dead_letter` and `usage`, hold ids and counts, never memory text.

## Where a model is used

| Step | Model tokens |
|---|---|
| Embedding chunks and queries (local ONNX) | 0 |
| Recall: vector and full-text over chunks and facts, fused | 0 |
| Facts from `remember` (inside the agent's own turn) or from rules | 0 extra |
| Facts by model (`facts.extract: llm`, off by default) | the configured or host model, per document |
| Judging update versus new (`truth.judge: auto`) | only free text close to a stored fact; rules decide plain statements |

## Tests and eval

```bash
.venv/bin/pytest -q            # needs the Postgres container; fully offline (fake LLM, hash embedder)
.venv/bin/pytest -m live       # real services: real embedder, Redis, contract suites for real adapters
```

170 offline tests, including raw-SQL row-level-security checks, a cross-tenant fuzz, concurrency tests and regression tests for every issue found in five independent review rounds. `tests/acceptance/` is frozen: its hashes are in `.frozen` and the builder never edits those files to make them pass.

The eval is 240 synthetic, template-based ads conversations (`tests/eval/`). Treat it as a regression check, not a claim about your data.

| adapters | recall@5 | correct after update | abstention (near-domain probes) | stale leaks |
|---|---|---|---|---|
| fake LLM + hash embedder, model-based facts (offline, frozen test) | 1.00 | 1.00 | 0.95 | 0 |
| **no LLM**, rules + verbatim, real local MiniLM (`fastembed`) | 1.00 | 1.00 | 0.00 | 0 |

The 0.00 is a measured limit, not a bug: a query like "churn of campq41" beside stored ad metrics scores about 0.5 against MiniLM, the same as a vague real question ("how much are we spending per month" scores 0.39), so no similarity cut separates them. The floor (`retrieval.min_similarity: 0.30`) does abstain on off-domain questions (cricket, weather: 0.05 to 0.22). For "close but not stored" the agent decides from the returned text, as with any retrieval system.

## Guarantees and known limits

- **Own schema.** Everything lives in Postgres schema `memaso` (the `vector` extension in `public`), so it can share a
  database with v3's own tables without a name clash. `migrate` never touches other schemas. The app role needs
  `USAGE` on schema `memaso` (the migration grants it to a role named `memaso_app`).
- Tenant isolation: row-level security on every data table, tenant taken only from the API key; startup refuses a
  database role that bypasses it.
- **Account scope.** A fact stored with no `container` is global: it shows up in every account's search and profile.
  A fact stored for an account is visible only in that account. A subject-level scope (no container) sees both.
  Supersession only compares facts inside the same container, so "Budget is 50k" in account A and "Budget is 80k"
  in account B coexist.
- Hard delete removes rows, embeddings, chunks and links in one cascade and returns a receipt. Forgetting a memory
  deletes the chunk its words were found in and redacts those words from the source document and its other chunks
  when they appear verbatim. A model's paraphrased fact cannot be matched textually: forget its document by id.
  Forgetting a value never brings back the older value it replaced. Audit rows hold ids only.
- **Stale values in verbatim text.** When a fact is superseded, its exact words are replaced by `[superseded]` inside
  the chunk it came from in "current" search; the rest of the chunk stays. A chunk tied to a model's paraphrase retires
  whole. `include_history` and `as_of` show the original text.
- Recalled text is escaped and wrapped in `<memory>` / `<chunk>` tags and labelled untrusted.
- Vector search is an exact scan filtered by tenant, subject and model (add a per-model HNSW index past ~100k rows
  per tenant). Full-text search is OR-of-words on an English config; the vector leg carries multilingual recall.
- The rules extractor is conservative and English-only: plain "X is Y" statements and preferences ("prefers", "likes",
  "hates", "wants", and first-person "I ..."), from the user's own lines, without a leading "actually/so/ok". It skips
  questions, hedges, and compound sentences ("X is 1 and Y is 2"), which stay verbatim-only. For richer facts use
  `remember` or `facts.extract: llm`. Two preferences about different things are never merged by rules; only an exact
  opposite (hates vs likes the same thing) is. Paraphrased subjects ("budget" vs "monthly budget") are ambiguous: with
  a model it decides, without one both facts stay.
- Order: one subject's documents are applied oldest-first under a per-subject lock, whichever worker runs first.
- The in-process queue and rate limiter are per process; use the Redis queue (and a shared limiter) for replicas.

**Open items (known, not fixed):**
- Hard delete by memory id is several transactions; a crash between them can leave source text that a retry cannot
  reach (delete by document id instead).
- A failing older document is retried inline before a newer one and can delay it until it dead-letters.
- The compose API container holds the admin database URL so it can migrate; split migration into a one-shot service for
  production. Postgres is published on 127.0.0.1 only.
- Token counts are an estimate (3 bytes per token); a real tokenizer can count `<memory id=...>` tags higher.
- The store contract suite does not yet cover every store method (doc/subject deletes, redaction, usage records).
- The eval is synthetic and template-based; "stale leaks 0" is a regression check, not evidence for real chat text.
- The v3 tool shims were written from the white paper and have not been run against v3's code.
- Not built: entity/triple graph, entity-aware ranking boosts, file/URL ingestion, decay, rerank, inspector UI,
  v3 data migration, webhooks, TS SDK, LongMemEval-style runner.

## Repository map

```
memory_core/
  ingest/      chunking, rules, truth maintenance, the pipeline
  retrieval/   fusion, packing, search, profile
  manage/      edit, forget, export, audit
  api/         REST routes, auth dependency, rate limit
  adapters/    postgres store, LLM, embedders, queues (the only place vendor code lives)
  memory.py    library facade          mcp_server.py   MCP server         sdk.py   Python client
  integrations/  LangGraph hook and v3 shims
assets/        logo and diagrams, regenerated by make_assets.py
docs/          PRD.md, PLAN.md, V3_ALIGNMENT.md
tests/         unit, integration, contract, acceptance (frozen), eval
```

## Credits

The wordmark and diagram text are outlined from [Bricolage Grotesque](https://github.com/ateliertriay/bricolage) and [IBM Plex Mono](https://github.com/IBM/plex), both under the SIL Open Font License. The ideas behind verbatim-first storage and temporal facts owe a lot to [MemPalace](https://github.com/MemPalace/mempalace).
