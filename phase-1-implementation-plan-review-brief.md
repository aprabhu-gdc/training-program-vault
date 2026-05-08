# Phase 1 Implementation Plan Review Brief

## Purpose

This document summarizes the proposed **Phase 1 refactor plan** for the `training-program-vault` repository so it can be reviewed by an external consultant agent.

The objective of Phase 1 is **not** to implement full MCP yet.  
The objective is to prepare the codebase for MCP and enterprise deployment by:

- extracting shared contracts
- isolating reusable wiki/query/ingest logic into `wiki_core`
- splitting the query plane from the ingest plane
- removing direct Teams-to-backend coupling
- preparing clean interfaces for:
  - Azure AI Foundry model routing
  - Azure AI Search
  - a future Wiki MCP server
  - a future Orchestrator service

## Decisions Already Locked

| Topic | Decision |
|---|---|
| Model platform | Azure AI Foundry |
| Search backend target | Azure AI Search |
| Search ACL future support | Required in adapter design |
| SharePoint action auth | On behalf of the user |
| Approval model | Human-in-the-loop required for publish/finalize |
| Claude Desktop | Read-only, direct Wiki MCP access only |
| Teams bot role | Thin channel client, not orchestrator |
| Long-term orchestration | Dedicated Orchestrator service |

## Core Architectural Position

The main architectural recommendation is:

**MCP should be the capability interface, not the primary orchestration model.**

That means:

- Teams should remain a thin UI/channel client
- reusable backend capabilities should be exposed through clean service boundaries
- a dedicated orchestrator service should be introduced later for multi-server workflows
- action capabilities should not be directly exposed to every client

Phase 1 therefore focuses on **A0** and **A1**, not on the orchestrator or SharePoint action server yet.

## Current Repository Facts

These observations are grounded in the current codebase and are the main reasons Phase 1 is necessary.

- `rag_backend/query.py` imports `WikiQueryAttachment` from `teams_bot/services/wiki_query.py`
- `teams_bot/config.py` embeds `BackendSettings`, so the Teams app currently knows backend/vector/Egnyte settings
- `app.py` runs background ingest in-process
- `app.py` exposes the Egnyte webhook in the bot app
- `rag_backend/query.py` can build the index on the user query path
- `rag_backend/indexer.py` and `rag_backend/query.py` are hard-wired to LanceDB
- `scripts/extract_text.py` is effectively used as a shared library by both bot and ingest code
- `teams_bot/services/wiki_query.py` already contains an HTTP service path, which is a useful intermediate seam for decoupling

## Phase 1 Goals

- remove UI/backend type coupling
- extract shared DTOs and contracts
- create a reusable `wiki_core` package
- split query service from ingest worker
- preserve current functionality via compatibility shims
- prepare adapter interfaces for Azure AI Search
- prepare model interfaces for Azure AI Foundry
- keep future MCP work additive instead of requiring another large refactor

## Proposed Target Structure

```text
packages/
  contracts/
    identity.py
    query.py
    sync.py

  shared/
    documents/
      extract_text.py

  wiki_core/
    settings.py

    ai/
      model_gateway.py
      azure_ai_foundry_gateway.py
      legacy_provider_gateway.py

    content/
      markdown.py
      page_store.py
      file_page_store.py

    retrieval/
      models.py
      vector_store.py
      lancedb_adapter.py
      query_service.py
      index_service.py

    ingest/
      source_sync.py
      egnyte_adapter.py
      ingest_service.py

apps/
  teams_bot_app/
    app.py
    config.py

  wiki_query_api/
    app.py
    config.py

  ingest_api/
    app.py
    config.py

workers/
  egnyte_ingest_worker/
    worker.py
    config.py

teams_bot/
  bot.py
  cards.py
  services/
    query_client.py
    ingest_admin_client.py
    feedback.py

rag_backend/
  query.py
  indexer.py
  auto_ingest.py
  config.py
  markdown.py
  egnyte_client.py
  llm.py

scripts/
  extract_text.py
```

## Step A0: Extract Shared Contracts And `wiki_core`

### A0 Objectives

- move all UI-agnostic logic out of `teams_bot` and `rag_backend`
- move shared request/response types into `packages/contracts`
- isolate retrieval/index/ingest logic into `packages/wiki_core`
- preserve current commands via wrappers and shims
- design interfaces now so Azure AI Search and Azure AI Foundry fit cleanly later

### Exact File Migrations

| Current file | New file(s) | Change |
|---|---|---|
| `teams_bot/services/wiki_query.py` | `packages/contracts/query.py`, `teams_bot/services/query_client.py` | Move `WikiQueryAttachment`, `WikiQueryRequest`, and `WikiQueryResult` into shared contracts. Keep only client/adapter logic in Teams package. |
| `scripts/extract_text.py` | `packages/shared/documents/extract_text.py` | Move `SUPPORTED_EXTENSIONS` and `extract_text()` into a shared library module. Keep `scripts/extract_text.py` only as a wrapper entrypoint. |
| `rag_backend/markdown.py` | `packages/wiki_core/content/markdown.py` | Move pure markdown/frontmatter/chunking helpers unchanged. |
| `rag_backend/query.py` | `packages/wiki_core/retrieval/query_service.py` | Move query engine into `wiki_core`; remove Teams imports and direct env bootstrapping from the core service. |
| `rag_backend/indexer.py` | `packages/wiki_core/retrieval/index_service.py`, `packages/wiki_core/retrieval/vector_store.py`, `packages/wiki_core/retrieval/lancedb_adapter.py` | Split domain indexing logic from LanceDB-specific storage. |
| `rag_backend/llm.py` | `packages/wiki_core/ai/model_gateway.py`, `packages/wiki_core/ai/azure_ai_foundry_gateway.py`, `packages/wiki_core/ai/legacy_provider_gateway.py` | Introduce a model interface shaped for Azure AI Foundry. Keep a temporary legacy adapter if needed during transition. |
| `rag_backend/egnyte_client.py` | `packages/wiki_core/ingest/egnyte_adapter.py`, `packages/wiki_core/ingest/source_sync.py` | Move Egnyte integration behind a source-sync adapter interface. |
| `rag_backend/auto_ingest.py` | `packages/wiki_core/ingest/ingest_service.py`, `packages/wiki_core/content/file_page_store.py` | Split ingest orchestration from wiki file IO/update helpers. |
| `rag_backend/config.py` | `packages/wiki_core/settings.py`, `apps/wiki_query_api/config.py`, `workers/egnyte_ingest_worker/config.py`, `apps/teams_bot_app/config.py` | Separate backend settings from app/channel settings. |
| `app.py` | `apps/teams_bot_app/app.py` | Move bot app bootstrap into its own app package. |
| `teams_bot/config.py` | `apps/teams_bot_app/config.py` | Simplify bot config so it owns bot/channel settings and remote service URLs only. |

### Compatibility Shim Strategy

The current `rag_backend/*` files should remain for one transition phase as wrappers.

| Existing path | Temporary behavior |
|---|---|
| `rag_backend/query.py` | Imports and delegates to `packages/wiki_core/retrieval/query_service.py` |
| `rag_backend/indexer.py` | Imports and delegates to `packages/wiki_core/retrieval/index_service.py` |
| `rag_backend/auto_ingest.py` | Imports and delegates to `packages/wiki_core/ingest/ingest_service.py` |
| `rag_backend/markdown.py` | Re-exports from `packages/wiki_core/content/markdown.py` |
| `rag_backend/egnyte_client.py` | Re-exports from `packages/wiki_core/ingest/egnyte_adapter.py` |
| `scripts/extract_text.py` | Imports from `packages/shared/documents/extract_text.py` |

This keeps current commands usable while the package boundaries are being cut.

## New Interfaces To Add In A0

### 1. Shared Query Contracts

**File:** `packages/contracts/query.py`

```python
from dataclasses import dataclass, field
from typing import Any

@dataclass(frozen=True)
class QueryAttachment:
    name: str
    content_type: str
    text_content: str | None = None
    image_data_url: str | None = None
    blob_ref: str | None = None

@dataclass(frozen=True)
class QueryIdentity:
    user_id: str | None = None
    user_name: str | None = None
    tenant_id: str | None = None
    channel_id: str | None = None
    conversation_id: str | None = None
    locale: str | None = None
    client_app: str | None = None

@dataclass(frozen=True)
class QueryRequest:
    request_id: str
    query: str
    identity: QueryIdentity
    attachments: tuple[QueryAttachment, ...] = ()
    client_context: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class Citation:
    title: str
    path: str
    section: str | None = None
    sources: tuple[str, ...] = ()

@dataclass(frozen=True)
class QueryResponse:
    answer_text: str
    citations: tuple[Citation, ...] = ()
    warnings: tuple[str, ...] = ()
    retrieval_diagnostics: dict[str, Any] = field(default_factory=dict)
```

**Why:**  
The current `WikiQueryResult` only normalizes answer text. For enterprise reuse and future orchestration, responses should carry structured citations and diagnostics.

### 2. Identity Contract

**File:** `packages/contracts/identity.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class CallerIdentity:
    user_id: str | None
    user_name: str | None
    tenant_id: str | None
    client_app: str | None
    channel_id: str | None = None
    conversation_id: str | None = None
    locale: str | None = None
```

**Why:**  
This gives every service call a stable identity envelope now, which matters later for on-behalf-of-user actions.

### 3. Sync Contracts

**File:** `packages/contracts/sync.py`

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class SourceFileEvent:
    path: str
    event_type: str
    modified_at: str | None = None
    entry_id: str | None = None

@dataclass(frozen=True)
class SyncJobAccepted:
    job_id: str
    status: Literal["accepted"]

@dataclass(frozen=True)
class SyncExecutionResult:
    requested_files: int
    downloaded_files: tuple[str, ...]
    updated_wiki_files: tuple[str, ...]
    skipped_files: tuple[str, ...]
    indexed_files: tuple[str, ...]
```

**Why:**  
The synchronous `SyncReport` object is fine internally, but A1 will need async job acceptance and worker-owned execution results.

### 4. Model Gateway Interface

**File:** `packages/wiki_core/ai/model_gateway.py`

```python
from typing import Protocol, Sequence, Any

class ModelGateway(Protocol):
    def embed_texts_sync(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def embed_texts_async(self, texts: Sequence[str]) -> list[list[float]]: ...
    async def complete_text(
        self,
        *,
        system_prompt: str,
        user_prompt: str | list[dict[str, Any]],
        temperature: float = 0.1,
        requires_vision: bool = False,
    ) -> str: ...
    def complete_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> dict: ...
```

**Why:**  
This should be named around model capabilities, not vendor names. The implementation target is Azure AI Foundry, even if a temporary legacy adapter still exists during migration.

### 5. Vector Store Interface

**File:** `packages/wiki_core/retrieval/vector_store.py`

```python
from typing import Protocol, Iterable, Any

class VectorStore(Protocol):
    def is_ready(self) -> bool: ...
    def rebuild(self, rows: list[dict[str, Any]]) -> None: ...
    def upsert(self, rows: list[dict[str, Any]]) -> None: ...
    def delete_by_paths(self, relative_paths: Iterable[str]) -> None: ...
    def search(
        self,
        embedding: list[float],
        *,
        top_k: int,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...
```

**Why:**  
The `filters` argument must exist from day one so future ACL metadata filtering does not require another interface break.

### 6. Page Store Interface

**File:** `packages/wiki_core/content/page_store.py`

```python
from typing import Protocol, Iterable, Any
from pathlib import Path
from packages.wiki_core.content.markdown import WikiPage

class PageStore(Protocol):
    def iter_wiki_pages(self) -> list[Path]: ...
    def load_wiki_page(self, path: Path) -> WikiPage: ...
    def write_page(self, relative_path: str, frontmatter: dict[str, Any], body: str) -> None: ...
    def read_index_summary(self, max_chars: int) -> str: ...
    def upsert_index_entry(self, entry: str) -> bool: ...
    def append_overview_note(self, note: str) -> bool: ...
    def append_log_entry(self, title: str, bullets: list[str]) -> bool: ...
```

**Why:**  
`auto_ingest.py` currently mixes ingest logic with direct filesystem wiki mutations. This interface isolates the content layer cleanly.

### 7. Source Sync Interface

**File:** `packages/wiki_core/ingest/source_sync.py`

```python
from typing import Protocol, Any
from pathlib import Path
from packages.contracts.sync import SourceFileEvent

class SourceSyncAdapter(Protocol):
    def parse_webhook_payload(self, payload: Any) -> list[SourceFileEvent]: ...
    def is_in_scope(self, event: SourceFileEvent) -> bool: ...
    def download_file(self, path: str) -> Path: ...
    def list_files_recursive(self, root_path: str) -> list[SourceFileEvent]: ...
```

**Why:**  
Egnyte is just one source adapter. This keeps the ingest plane extensible.

### 8. Teams Query Client Interface

**File:** `teams_bot/services/query_client.py`

```python
from typing import Protocol
from packages.contracts.query import QueryRequest, QueryResponse

class QueryClient(Protocol):
    async def query(self, request: QueryRequest) -> QueryResponse: ...
```

**Why:**  
The Teams bot should depend on a query client abstraction, not on backend imports.

### 9. Teams Ingest Admin Client Interface

**File:** `teams_bot/services/ingest_admin_client.py`

```python
from typing import Protocol
from packages.contracts.sync import SyncJobAccepted

class IngestAdminClient(Protocol):
    async def request_manual_sync(self, requested_by_user_id: str | None) -> SyncJobAccepted: ...
    async def get_sync_status(self, job_id: str) -> dict: ...
```

**Why:**  
This replaces the bot’s current direct `sync_runner` dependency.

## A0 Refactor Rules

These should be treated as hard acceptance rules.

- `wiki_core` must have zero imports from `teams_bot`
- `teams_bot` must not import backend settings or vector/Egnyte logic
- `extract_text()` must no longer live only under `scripts/`
- `VectorStore.search()` must accept filters
- `QueryResponse` must support structured citations and diagnostics
- `rag_backend/*` remains as temporary wrappers for one release
- the Azure AI Foundry path should be reflected in the interface names, even if the current implementation still temporarily routes through existing logic

## Step A1: Split Query Service From Ingest Worker

### A1 Objectives

- separate read/query traffic from ingest/update traffic
- remove Egnyte webhook handling from the bot app
- stop running background ingest in the Teams process
- make query service independently deployable
- make ingest worker independently scalable
- ensure the query path is read-only

## Proposed Deployables In A1

| Deployable | New file | Responsibility |
|---|---|---|
| Teams bot app | `apps/teams_bot_app/app.py` | Own only Teams/Bot Framework endpoints and bot lifecycle |
| Query API | `apps/wiki_query_api/app.py` | Own `POST /query`, `GET /healthz`, and `GET /readyz` |
| Ingest API | `apps/ingest_api/app.py` | Own `POST /webhooks/egnyte` and `POST /admin/sync` |
| Ingest worker | `workers/egnyte_ingest_worker/worker.py` | Consume jobs and execute ingest/index updates |

## Current File Changes Required For A1

| Current file | A1 change |
|---|---|
| `app.py` | Remove `AutoIngestService`, background sync scheduling, and `/api/webhooks/egnyte`. Keep bot plumbing only. |
| `teams_bot/config.py` | Remove `backend=BackendSettings.from_env()`. Replace with service URLs such as `QUERY_API_URL` and `INGEST_API_URL`. |
| `teams_bot/bot.py` | Replace `sync_runner` with `ingest_admin_client`. `/sync` should call the ingest service, not run ingest locally. |
| `teams_bot/services/wiki_query.py` | Replace with `teams_bot/services/query_client.py`. Keep HTTP-first query access. |
| `rag_backend/query.py` or `wiki_core/retrieval/query_service.py` | Remove query-time index build behavior. Missing index should return a controlled readiness failure, not mutate state. |
| `rag_backend/auto_ingest.py` or `wiki_core/ingest/ingest_service.py` | Make ingest worker-owned. No bot-process assumptions. |

## A1 Runtime Policy

- Teams bot app should no longer need Egnyte credentials
- Teams bot app should no longer need vector store credentials
- Teams bot app should no longer need backend model credentials
- Query API should have read-only access to:
  - wiki content
  - search index
  - model inference
- Ingest worker should have write access to:
  - `raw/`
  - `wiki/`
  - search index updates
  - source-sync credentials
- `/sync` in Teams should become asynchronous
- query must not build or repair the index in production

## A1 Configuration Direction

### Teams bot config should move toward:

```python
@dataclass(frozen=True)
class TeamsBotSettings:
    app_id: str
    app_password: str
    port: int
    query_api_url: str
    ingest_api_url: str
    request_timeout_seconds: float
```

### Query API config should own:

```python
@dataclass(frozen=True)
class QueryApiSettings:
    vault_root: Path
    rag_top_k: int
    search_index_name: str
    ai_foundry_endpoint: str
    ai_foundry_chat_deployment: str
    ai_foundry_embedding_deployment: str
```

### Ingest worker config should own:

```python
@dataclass(frozen=True)
class IngestWorkerSettings:
    vault_root: Path
    egnyte_domain: str
    egnyte_sync_root: str
    egnyte_token: str
    ai_foundry_endpoint: str
    ai_foundry_chat_deployment: str
    search_index_name: str
```

## Strong Recommendation: Add Queueing In A1

I recommend introducing **Azure Service Bus in A1**, not later.

Without a queue:
- `ingest_api` is still awkward for long-running work
- `/sync` remains operationally clumsy
- webhook bursts are harder to absorb
- retries and idempotency become fragile

With Service Bus:
- `POST /webhooks/egnyte` becomes fast and reliable
- `/sync` can return `job accepted`
- worker scaling is straightforward
- later orchestration and approval flows become easier to support

## Suggested A1 Migration Order

1. Create `packages/contracts`
2. Create `packages/shared/documents`
3. Extract `packages/wiki_core`
4. Leave `rag_backend/*` as wrappers
5. Create `apps/wiki_query_api/app.py`
6. Point Teams to the query API using the existing HTTP query pattern
7. Remove backend settings from the Teams app
8. Create `apps/ingest_api/app.py`
9. Create `workers/egnyte_ingest_worker/worker.py`
10. Move Egnyte webhook handling off the bot app
11. Change Teams `/sync` to call the ingest admin API
12. Remove query-time index build logic from the query path

## Recommended PR Breakdown

### PR 1
Contracts and shared document extraction

- add `packages/contracts/*`
- add `packages/shared/documents/extract_text.py`
- move DTOs out of `teams_bot/services/wiki_query.py`
- update imports only
- no behavior change intended

### PR 2
`wiki_core` extraction with compatibility shims

- add `packages/wiki_core/*`
- move markdown, query, index, ingest, and model logic into `wiki_core`
- keep `rag_backend/*` as wrappers
- keep existing CLI entrypoints working

### PR 3
Remote query API

- add `apps/wiki_query_api/app.py`
- replace local callable assumptions with query client usage
- make HTTP the normal production path
- simplify Teams config
- remove query-time index build behavior

### PR 4
Ingest API and worker split

- add `apps/ingest_api/app.py`
- add `workers/egnyte_ingest_worker/worker.py`
- move Egnyte webhook route off the bot app
- switch `/sync` to async job submission

## Acceptance Criteria

### A0
- no `teams_bot` imports anywhere inside `wiki_core`
- `QueryAttachment`, `QueryRequest`, and `QueryResponse` live in `packages/contracts/query.py`
- `extract_text()` lives in `packages/shared/documents/extract_text.py`
- current `python app.py` and `python -m rag_backend.*` commands still work through wrappers

### A1
- Teams bot can start without Egnyte credentials
- Teams bot can start without search-index credentials
- Teams bot can start without backend model credentials
- query API is independently deployable
- bot app no longer exposes the Egnyte webhook
- `/sync` no longer runs ingest inside the bot process
- query is read-only and does not rebuild the index
- service identities can be separated cleanly between:
  - bot app
  - query API
  - ingest worker

## Main Design Risks To Review

| Risk | Why it matters |
|---|---|
| Response contract too thin | If `QueryResponse` only returns answer text, later orchestration and provenance will be weak |
| Vector interface too LanceDB-shaped | This could make Azure AI Search migration harder |
| Azure AI Foundry abstraction too vendor-specific | This could reintroduce model coupling under a different name |
| Keeping sync fully synchronous too long | This will make ingest splitting awkward and operationally fragile |
| Not separating config early | Teams app may stay over-privileged longer than necessary |

## Specific Questions For External Review

1. Is the proposed `wiki_core` boundary the right one, or should content/retrieval/ingest be split even further now?
2. Is `QueryResponse` rich enough for future orchestration, or should structured retrieved chunks also be part of the public contract?
3. Is adding Azure Service Bus in A1 the right move, or should queueing wait until after the query API stabilizes?
4. Is the proposed `VectorStore` interface sufficient for an Azure AI Search adapter with future ACL filtering?
5. Is `ModelGateway` the right abstraction for Azure AI Foundry, or should the interface separate chat, embedding, and vision more explicitly?
6. Is it worth keeping `rag_backend/*` wrappers for one release, or is that unnecessary complexity?
7. Should the ingest worker own all wiki file writes immediately, or should a separate `page_store` service boundary be introduced later?

## Recommended Bottom Line

Phase 1 should be treated as a **service-boundary extraction phase**, not as an MCP implementation phase.

If A0 and A1 are done cleanly:

- Azure AI Search can be introduced without another major refactor
- Azure AI Foundry can become the standard model backend cleanly
- a Wiki MCP server can be added as a thin facade later
- an Orchestrator service can be added later without re-cutting the core
- Teams remains a thin client
- Claude Desktop can stay read-only without special-case logic
- SharePoint actions can remain safely outside the direct client path until workflow governance is ready

## End State After Phase 1

After A0 and A1, the expected architecture should be:

- Teams bot app as a thin channel client
- query service as a standalone read-only backend
- ingest API plus worker as a separate write/update plane
- shared contracts for all cross-service communication
- `wiki_core` as the reusable core
- future MCP and orchestration work made incremental rather than disruptive

If useful, the next planning artifact should be a **PR 1 implementation checklist** with exact import rewrites and shim details for the current repository.
