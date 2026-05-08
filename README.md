# Graydaze PM Training Vault

Graydaze PM Training Vault is a private wiki-backed Teams assistant for Graydaze training content. It combines a maintained markdown knowledge base under `wiki/`, a local retrieval pipeline, and a Microsoft Teams bot so junior PMs can ask operational questions without reading the full training corpus manually.

## Repository Purpose

This repository now separates the runtime into thin service boundaries while keeping one-release compatibility shims for the older `rag_backend/` imports.

The main layers are:

- `wiki/`: the maintained knowledge base that the bot queries
- `packages/`: shared contracts, document extraction, retrieval core, and ingest core
- `apps/`: standalone HTTP apps for the Teams bot wrapper, wiki query API, and ingest API
- `workers/`: background ingest workers that consume queued jobs
- `teams_bot/` and `app.py`: the thin Microsoft Teams bot runtime and HTTP entrypoint
- `rag_backend/`: compatibility wrappers over the extracted core modules

The bot retrieves only from `wiki/`. The `raw/` folder is used for source capture and ingest workflows, not for end-user retrieval.

## Current Features

- Local embedded vector index for `wiki/` content using LanceDB
- Markdown-aware chunking by `##` headings
- YAML frontmatter parsing and metadata propagation into index rows
- Provider-agnostic LLM configuration with runtime support for OpenAI, Azure OpenAI, Anthropic, and Google
- Standalone `apps/wiki_query_api` query service
- Standalone `apps/ingest_api` ingest service with Azure Service Bus job queueing
- Background `workers/egnyte_ingest_worker` worker for manual sync and webhook-triggered ingest
- Teams bot with typing indicators, welcome message, and feedback buttons
- Teams manual `/sync` command that queues an Egnyte refresh job
- Egnyte webhook endpoint for queued background ingest and reindexing
- Attachment-aware Teams chat input for supported documents and images

## Architecture

### Wiki Layer

- `AGENTS.md` defines the vault operating contract
- `wiki/index.md` is the primary navigation map
- `wiki/log.md` tracks durable maintenance and ingest activity
- `wiki/sources/`, `wiki/concepts/`, `wiki/entities/`, `wiki/syntheses/`, and `wiki/queries/` hold maintained knowledge pages

### Retrieval Layer

- `packages/wiki_core/retrieval/` contains the extracted indexing and query services
- `packages/wiki_core/content/` contains markdown parsing and page store helpers
- `packages/wiki_core/ai/` contains the current model gateway and legacy provider adapter
- `apps/wiki_query_api/app.py` exposes the extracted query service over HTTP
- `rag_backend/` remains as a compatibility layer over the extracted modules for one release

### Sync and Ingest Layer

- `packages/wiki_core/ingest/` contains the extracted Egnyte adapter and ingest orchestration service
- `packages/shared/documents/extract_text.py` extracts text from Office and PDF formats used during ingest and Teams attachment preprocessing
- `packages/shared/messaging/service_bus.py` wraps Azure Service Bus send/receive helpers
- `apps/ingest_api/app.py` accepts manual sync and Egnyte webhook requests, then queues jobs
- `workers/egnyte_ingest_worker/worker.py` performs the actual ingest work from queued jobs
- `scripts/extract_text.py` remains as a CLI wrapper over the shared extraction library

### Teams Layer

- `app.py` exposes `/api/messages` and `/healthz`
- `teams_bot/bot.py` handles chat, `/sync`, feedback, and attachment preprocessing
- `teams_bot/services/wiki_query.py` adapts Teams requests to a local callable or remote HTTP query API
- `teams_bot/services/ingest_admin_client.py` submits `/sync` requests to the remote ingest API
- `teams_app/manifest.json` contains the Teams app package manifest

## Key Design Decisions

### Why LanceDB Instead of ChromaDB

The original backend request allowed either ChromaDB or LanceDB. The implementation uses LanceDB because it installs and runs cleanly in the current Windows environment, while ChromaDB required a native `hnswlib` build that failed without local MSVC build tools.

### Why Vector State Lives Outside the Repo by Default

The repository lives on an Egnyte UNC path. Embedded local databases are more reliable on a normal local filesystem path, so the default vector DB and sync state paths point at local app data unless explicitly overridden.

### Why Queries Only Use `wiki/`

The bot is designed to answer from the maintained knowledge layer, not directly from raw source files. This keeps answers grounded in curated pages and aligned with the vault contract in `AGENTS.md`.

## Supported Attachment Inputs in Teams

The bot can preprocess these attachments in chat:

- Images exposed to the bot through a downloadable URL
- `.pdf`
- `.docx`
- `.pptx`
- `.xlsx`
- `.xlsm`
- `.txt`
- `.md`
- `.csv`
- `.json`

Document attachments are converted into text and passed as user context to the RAG query. Image attachments are passed to the configured vision-capable chat model alongside the retrieved wiki context.

Attachment content is treated as user-supplied context, not as a wiki source. Only retrieved wiki content should be cited as `[Source: Title]`.

## Model Configuration

The environment contract is provider-agnostic. You can configure one default provider or route chat, vision, and embeddings separately.

### Generic Routing Keys

Required:

- `LLM_PROVIDER` or workload-specific provider keys
- `LLM_CHAT_MODEL`
- `LLM_EMBEDDING_MODEL`

Optional:

- `LLM_CHAT_PROVIDER`
- `LLM_VISION_PROVIDER`
- `LLM_VISION_MODEL`
- `LLM_EMBEDDING_PROVIDER`

Routing behavior:

- `LLM_CHAT_PROVIDER` falls back to `LLM_PROVIDER`
- `LLM_VISION_PROVIDER` falls back to `LLM_CHAT_PROVIDER`, then `LLM_PROVIDER`
- `LLM_EMBEDDING_PROVIDER` falls back to `LLM_PROVIDER`
- `LLM_VISION_MODEL` falls back to `LLM_CHAT_MODEL`

### Provider Credential Blocks

- OpenAI-compatible:
  - `LLM_OPENAI_API_KEY`
  - `LLM_OPENAI_BASE_URL`
- Azure OpenAI:
  - `LLM_AZURE_OPENAI_ENDPOINT`
  - `LLM_AZURE_OPENAI_API_KEY`
  - `LLM_AZURE_OPENAI_API_VERSION`
- Anthropic:
  - `LLM_ANTHROPIC_API_KEY`
  - `LLM_ANTHROPIC_BASE_URL`
- Google:
  - `LLM_GOOGLE_API_KEY`
  - `LLM_GOOGLE_BASE_URL`

### Current Runtime Support

The configuration scheme is provider-agnostic, and the current implemented runtime adapters support these chat/vision providers today:

- `openai`
- `azure-openai`
- `anthropic`
- `google`

The current implemented embedding providers are:

- `openai`
- `azure-openai`
- `google`

Anthropic is not implemented for embeddings in the current runtime.

### Vision Behavior

If `LLM_VISION_MODEL` is not set, image requests fall back to `LLM_CHAT_MODEL`. In that case, the selected chat model must support image input if you want Teams image attachments to work.

## Environment Variables

See `.env.example` for the full list. The most important settings are:

- Bot runtime:
  - `MicrosoftAppId`
  - `MicrosoftAppPassword`
  - `PORT`
  - `WIKI_QUERY_CALLABLE`
- Query routing:
  - `QUERY_API_PORT`
  - `WIKI_QUERY_HTTP_URL`
  - `INGEST_API_PORT`
  - `INGEST_ADMIN_HTTP_URL`
  - `WIKI_QUERY_TIMEOUT_SECONDS`
- Retrieval and vector state:
  - `VAULT_ROOT`
  - `LOCAL_DATA_ROOT`
  - `VECTOR_DB_PATH`
  - `VECTOR_TABLE_NAME`
  - `VECTOR_MANIFEST_PATH`
  - `RAG_TOP_K`
  - `RAG_INDEX_SUMMARY_CHARS`
- LLM and embedding config:
  - `LLM_PROVIDER`
  - `LLM_CHAT_PROVIDER`
  - `LLM_CHAT_MODEL`
  - `LLM_VISION_PROVIDER`
  - `LLM_VISION_MODEL`
  - `LLM_EMBEDDING_PROVIDER`
  - `LLM_EMBEDDING_MODEL`
  - `LLM_OPENAI_*`
  - `LLM_AZURE_OPENAI_*`
  - `LLM_ANTHROPIC_*`
  - `LLM_GOOGLE_*`
- Egnyte sync:
  - `EGNYTE_DOMAIN`
  - `EGNYTE_API_TOKEN`
  - `EGNYTE_SYNC_ROOT`
  - `EGNYTE_TRAINING_FOLDER_NAME`
  - `EGNYTE_REQUEST_TIMEOUT_SECONDS`
  - `EGNYTE_SYNC_STATE_PATH`
- Queueing:
  - `SERVICE_BUS_CONNECTION_STRING`
  - `SERVICE_BUS_NAMESPACE`
  - `INGEST_QUEUE_NAME`

## Setup

### 1. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 2. Configure environment

Create a `.env` file based on `.env.example` and populate the required bot, model, Egnyte, and queue settings.

For a local split run, set:

- `WIKI_QUERY_HTTP_URL=http://localhost:8000/query`
- `INGEST_ADMIN_HTTP_URL=http://localhost:8010`
- `QUERY_API_PORT=8000`
- `INGEST_API_PORT=8010`

### 3. Build the initial wiki index

```bash
python -m rag_backend.indexer --mode build
```

### 4. Run the standalone query API

```bash
python -m apps.wiki_query_api.app
```

### 5. Run the ingest API

```bash
python -m apps.ingest_api.app
```

### 6. Run the ingest worker

```bash
python -m workers.egnyte_ingest_worker.worker
```

### 7. Run the bot locally

```bash
python app.py
```

The app exposes:

- `POST /api/messages`
- `GET /healthz`

The standalone query API exposes:

- `POST /query`
- `GET /healthz`
- `GET /readyz`

The ingest API exposes:

- `POST /admin/sync`
- `POST /api/webhooks/egnyte`
- `GET /healthz`

## Operations

### Rebuild the whole index

```bash
python -m rag_backend.indexer --mode build
```

### Upsert only modified wiki files

```bash
python -m rag_backend.indexer --mode upsert
```

### Run the queued ingest worker

```bash
python -m workers.egnyte_ingest_worker.worker
```

### Replay an Egnyte webhook payload locally with the legacy compatibility shim

```bash
python -m rag_backend.auto_ingest --payload path/to/payload.json
```

### Manual Egnyte sync with the legacy compatibility shim

```bash
python -m rag_backend.auto_ingest --manual
```

### Teams manual sync

Message the bot with:

```text
/sync
```

## Teams App Packaging

See `teams_app/README.md` for packaging details. Before publishing, you still need:

- Real `manifest.json` IDs and hostname values
- `color.png`
- `outline.png`
- A public HTTPS endpoint for the bot
- A real Bot/Entra registration

## Development Logs

Backfilled engineering reports live under `docs/development-logs/`. They are named with `YYYY-MM-DD-phase-topic.md` so they sort naturally and remain easy to scan.

## Repository Notes

- `raw/` is intentionally git-ignored and should remain untracked
- `.obsidian/` and session transcript files are intentionally excluded from git
- The bot currently uses `MemoryStorage`; replace it with shared durable storage for multi-instance production
