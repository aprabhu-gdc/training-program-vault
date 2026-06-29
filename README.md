# Graydaze PM Training Vault

Graydaze PM Training Vault is a private wiki-backed Teams assistant for Graydaze training content. It combines a maintained markdown knowledge base under `wiki/`, a local retrieval pipeline, and a Microsoft Teams bot so junior PMs can ask operational questions without reading the full training corpus manually.

## Repository Purpose

This repository separates the runtime into thin service boundaries while keeping one-release compatibility shims for the older `rag_backend/` imports.

The main layers are:

- `wiki/`: the maintained knowledge base that the bot queries
- `packages/`: shared contracts, document extraction, retrieval core, and ingest core
- `apps/`: standalone HTTP apps for the Teams bot wrapper, wiki query API, and ingest API
- `workers/`: background sync workers that consume queued jobs
- `teams_bot/` and `app.py`: the thin Microsoft Teams bot runtime and HTTP entrypoint
- `rag_backend/`: compatibility wrappers over the extracted core modules

The bot retrieves only from `wiki/`. The `raw/` folder is used for source capture and ingest workflows, not for end-user retrieval.

## Current Features

- Local embedded vector index for `wiki/` content using LanceDB
- Markdown-aware chunking by `##` headings
- YAML frontmatter parsing and metadata propagation into index rows
- Provider-agnostic LLM configuration with runtime support for OpenAI and Azure OpenAI (Microsoft AI Foundry)
- Standalone `apps/wiki_query_api` query service
- Standalone `apps/ingest_api` ingest service with Azure Service Bus job queueing
- Background `workers/source_sync_worker` worker for manual SharePoint-backed ingest
- Teams bot with typing indicators, welcome message, and feedback buttons
- Teams manual `/sync` command that queues a SharePoint refresh job
- Attachment-aware Teams chat input for supported documents and images

## Architecture

### SharePoint Source of Truth

- The production source of truth is the remote SharePoint Online document library.
- `raw/sources/` in SharePoint is the authoritative raw source layer.
- `wiki/` in SharePoint is the authoritative maintained wiki layer.
- A local SharePoint-synced folder is optional for development and inspection only. It is not a required production dependency.

### Runtime Storage Model

- The ingest/query runtime uses a local materialized working copy of the vault.
- The runtime reads remote SharePoint source files through Microsoft Graph during sync.
- Updated `wiki/` files are written locally first, then pushed back to SharePoint.
- Vector DB files, manifests, and sync state should live outside any synced SharePoint workspace.

### Wiki Layer

- `AGENTS.md` defines the vault operating contract.
- `wiki/index.md` is the primary navigation map.
- `wiki/log.md` tracks durable maintenance and ingest activity.
- `wiki/sources/`, `wiki/concepts/`, `wiki/entities/`, `wiki/syntheses/`, and `wiki/queries/` hold maintained knowledge pages.

### Retrieval Layer

- `packages/wiki_core/retrieval/` contains the extracted indexing and query services.
- `packages/wiki_core/content/` contains markdown parsing and page store helpers.
- `packages/wiki_core/ai/` contains the current model gateway and legacy provider adapter.
- `apps/wiki_query_api/app.py` exposes the extracted query service over HTTP.
- `rag_backend/` remains as a compatibility layer over the extracted modules for one release.

### Sync and Ingest Layer

- `packages/wiki_core/ingest/` contains the extracted SharePoint source adapter and ingest orchestration service.
- `packages/shared/documents/extract_text.py` extracts text from Office and PDF formats used during ingest and Teams attachment preprocessing.
- `packages/shared/messaging/service_bus.py` wraps Azure Service Bus send/receive helpers.
- `apps/ingest_api/app.py` accepts manual sync requests and queues jobs.
- `workers/source_sync_worker/worker.py` performs the actual ingest work from queued jobs.
- `scripts/extract_text.py` remains as a CLI wrapper over the shared extraction library.

### Teams Layer

- `app.py` exposes `/api/messages` and `/healthz`.
- `teams_bot/bot.py` handles chat, `/sync`, feedback, and attachment preprocessing.
- `teams_bot/services/wiki_query.py` adapts Teams requests to a local callable or remote HTTP query API.
- `teams_bot/services/ingest_admin_client.py` submits `/sync` requests to the remote ingest API.
- `teams_app/manifest.json` contains the Teams app package manifest.

## Key Design Decisions

### Why Microsoft Graph For SharePoint Sync

The ingest path now treats remote SharePoint Online as the authoritative upstream and uses Microsoft Graph for site, drive, folder, and file operations. This avoids any production dependency on one person’s local sync client or workstation.

### Why LanceDB Still Lives Outside The Synced Vault

Embedded local databases and sync state are more reliable on a normal local filesystem path than inside a synced SharePoint workspace. The default vector DB and sync state paths therefore point at local app data unless explicitly overridden.

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

### Provider Credential Blocks

- OpenAI-compatible:
  - `LLM_OPENAI_API_KEY`
  - `LLM_OPENAI_BASE_URL`
- Azure OpenAI (Microsoft AI Foundry project endpoints work here):
  - `LLM_AZURE_OPENAI_ENDPOINT`
  - `LLM_AZURE_OPENAI_API_KEY`
  - `LLM_AZURE_OPENAI_API_VERSION`

## Environment Variables

See `.env.example` for the full list. The most important settings are:

- Bot runtime:
  - `MicrosoftAppId`
  - `MicrosoftAppPassword`
  - `MicrosoftAppType` (`MultiTenant` for the Emulator; `SingleTenant` for an internal Teams bot)
  - `MicrosoftAppTenantId` (required when `MicrosoftAppType=SingleTenant`)
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
  - `SOURCE_SYNC_STATE_PATH`
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
- SharePoint source sync:
  - `SHAREPOINT_TENANT_ID`
  - `SHAREPOINT_CLIENT_ID`
  - `SHAREPOINT_CLIENT_SECRET`
  - `SHAREPOINT_SITE_ID` or `SHAREPOINT_SITE_HOSTNAME` + `SHAREPOINT_SITE_PATH`
  - `SHAREPOINT_LIST_ID`, `SHAREPOINT_DRIVE_ID`, or `SHAREPOINT_DRIVE_NAME`
  - `SHAREPOINT_RAW_ROOT_PATH`
  - `SHAREPOINT_WIKI_ROOT_PATH`
  - `SHAREPOINT_REQUEST_TIMEOUT_SECONDS`
  - `SHAREPOINT_WEBHOOK_NOTIFICATION_URL` (public HTTPS endpoint Graph posts to)
  - `SHAREPOINT_WEBHOOK_CLIENT_STATE` (shared secret echoed in every notification)
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

Create a `.env` file based on `.env.example` and populate the required bot, model, SharePoint, and queue settings.

The ingest runtime expects app-only Microsoft Graph access to the target SharePoint site and document library.

The most reliable Phase 1 SharePoint configuration is:

- `SHAREPOINT_SITE_ID` from Graph or SharePoint site metadata
- `SHAREPOINT_LIST_ID` from the document library settings URL

When `SHAREPOINT_LIST_ID` is set, the runtime resolves the drive internally and you do not need Graph Explorer to discover `SHAREPOINT_DRIVE_ID` manually.

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

### 6. Run the source sync worker

```bash
python -m workers.source_sync_worker.worker
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

### Run the queued source sync worker

```bash
python -m workers.source_sync_worker.worker
```

### Manual SharePoint sync with the compatibility shim

```bash
python -m rag_backend.auto_ingest --manual
```

### Teams manual sync

Message the bot with:

```text
/sync
```

## Teams App Packaging

See `teams_app/README.md` for packaging details. Build the installable package with:

```bash
python -m scripts.build_teams_package --bot-app-id <ENTRA_APP_CLIENT_ID> --host <app>.azurewebsites.net
```

Placeholder icons are committed and auto-generated by the script; replace them
with real brand art before a wider rollout. You still need a real Bot/Entra
registration and a public HTTPS endpoint for `/api/messages` (see the pilot
deployment below).

## Stage 1 Pilot Deployment (Azure App Service)

The pilot runs as a single Azure App Service (Linux) instance with the bot as the
only public process. Set the App Service **Startup Command** to `bash startup.sh`;
it launches the ingest API (private, `localhost:8010`), the Service Bus sync
worker (background), and the Teams bot (public, on `$PORT`). RAG queries run
in-process via `WIKI_QUERY_CALLABLE`.

Required App Service application settings (in addition to the LLM / SharePoint /
Service Bus settings):

- `PORT=8000` and `WEBSITES_PORT=8000`
- `INGEST_API_PORT=8010` (must be explicit — ingest config otherwise falls back to
  `PORT` and would collide with the bot)
- `INGEST_ADMIN_HTTP_URL=http://localhost:8010`
- `WIKI_QUERY_CALLABLE=rag_backend.query:query_vault`
- `MicrosoftAppId`, `MicrosoftAppPassword`, `MicrosoftAppType=SingleTenant`, `MicrosoftAppTenantId`
- Enable **Always On** so the worker stays running.

Provisioning runbook (Entra app → Azure Bot resource → App Service → sideload) and
the values to capture are tracked in the project plan. Near-real-time SharePoint
sync (public webhook + subscription renewer), durable bot storage, and org-wide
catalog publishing are deferred to Stage 2; the pilot uses manual `/sync`.

## Repository Notes

- `raw/` is intentionally git-ignored and should remain untracked
- `.obsidian/` and session transcript files are intentionally excluded from git
- The bot currently uses `MemoryStorage`; replace it with shared durable storage for multi-instance production
