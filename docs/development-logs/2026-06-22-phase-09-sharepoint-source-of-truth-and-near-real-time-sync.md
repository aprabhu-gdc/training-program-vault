# 2026-06-22 Phase 09: SharePoint Source of Truth and Near-Real-Time Sync

## Summary

This phase completes the migration off Egnyte and onto SharePoint as the production source of truth for both the raw training corpus and the maintained wiki layer. It also turns on near-real-time freshness by adding a Microsoft Graph change-notification (webhook) path, narrows the LLM runtime to a single provider stack (Azure OpenAI / Microsoft AI Foundry), and removes the local `wiki/` markdown tree from git so the on-disk cache stops fighting the SharePoint source of truth.

The repository was returned to after several weeks away. A re-grounding conversation determined that the prior multi-provider abstraction, the in-flight Egnyte→SharePoint shim layer, and the tracked wiki cache were all carrying complexity that no longer matched the project's actual goals: a single-tenant whole-company Teams chatbot answering questions against SharePoint-resident training documents.

## Changes Implemented

### SharePoint as authoritative source

- Replaced the Egnyte source-sync path with a Microsoft Graph-backed `SharePointSourceSyncAdapter` (`packages/wiki_core/ingest/sharepoint_adapter.py`) implementing the existing `SourceSyncAdapter` protocol: site/list/drive resolution, recursive listing, content download, ensure-folder, and managed-page upload.
- Renamed the queue worker to `workers/source_sync_worker` and pointed `apps/ingest_api` and `AutoIngestService` at the SharePoint adapter throughout.
- Removed the Egnyte adapter, the `workers/egnyte_ingest_worker/` directory, and `rag_backend/egnyte_client.py` outright — no compatibility shim phase.
- Switched the ingest contract from `Literal`-narrowed event types to plain strings so the SharePoint adapter can emit `manual-sync` / `webhook` events without forcing a contract change for every new source.

### Near-real-time sync via Graph change notifications

- Added `parse_webhook_payload`, `create_subscription`, `renew_subscription`, and `delete_subscription` to the SharePoint adapter. `parse_webhook_payload` validates the `clientState` shared secret on every notification and resolves each `resourceData.id` to a drive-relative path via a follow-up Graph call so queued jobs carry the full path.
- Added a `POST /api/webhooks/sharepoint` route to `apps/ingest_api/app.py` that handles the Graph subscription validation handshake (echoing `validationToken` as plaintext within the 10-second window) and otherwise queues one Service Bus job per affected file with `job_type="webhook"`.
- Extended `workers/source_sync_worker/worker.py` to dispatch `webhook` jobs by constructing a `SourceFileEvent` from the queued payload and calling `AutoIngestService.sync_events([event])`. Manual full-sync behavior is preserved.
- Added `SHAREPOINT_WEBHOOK_NOTIFICATION_URL` and `SHAREPOINT_WEBHOOK_CLIENT_STATE` to `CoreSettings`, `.env.example`, and the README.

### Single-provider LLM stack

- Removed all Anthropic and Google runtime code from `rag_backend/llm.py` (chat, vision, embedding, and JSON-completion branches), shrinking the file by ~280 lines.
- Removed Anthropic/Google entries from `KNOWN_LLM_PROVIDERS`, `IMPLEMENTED_CHAT_PROVIDERS`, `IMPLEMENTED_EMBEDDING_PROVIDERS`, the provider alias map, the `llm_anthropic_*` / `llm_google_*` settings fields, and their validation branches in `packages/wiki_core/settings.py`.
- Removed the corresponding `LLM_ANTHROPIC_*` / `LLM_GOOGLE_*` entries from `.env.example` and `README.md`; Azure OpenAI is now annotated as the supported path for Microsoft AI Foundry project endpoints.

### Reliability and quality fixes carried in this phase

- Hardened the Service Bus consumer with `AutoLockRenewer` and a `treat_completion_lock_loss_as_processed` knob so full SharePoint syncs that exceed the message lock TTL don't get redelivered.
- Capped wiki-page chunking at 6000 characters in `packages/wiki_core/content/markdown.py` so pathological pages don't produce oversized embeddings.
- Fixed a duplicate-YAML-frontmatter bug in `FilePageStore.write_managed_page`: the LLM was occasionally including a frontmatter block at the top of its body, and the writer was prepending a second one. The writer now calls `split_frontmatter` on the LLM body and uses only the body portion before composing.

### Repository hygiene

- Stopped tracking the local `wiki/` markdown tree (46 files: `wiki/sources/`, `wiki/concepts/`, `wiki/entities/`, `wiki/queries/`, `wiki/syntheses/`, `wiki/index.md`, `wiki/log.md`, `wiki/overview.md`). It is now treated as a regenerable local cache; the LanceDB index is the bot's actual read path.
- Added `.gitignore` patterns for the OneDrive sync lock filename pattern, host-specific `wiki/index-*.md` files, and the local `source-sync-state.json` / `sync-job-state.json` runtime files.

## Why These Changes Were Implemented

- **SharePoint is where the docs already live.** Operators edit training material in SharePoint; mirroring through Egnyte added a hop without adding value. Cutting Egnyte removes a system from the architecture diagram.
- **Near-real-time was a product requirement, not a nice-to-have.** Whole-company usage means a user who just edited a SharePoint document expects the bot to know about it the same hour, not the next day. Manual `/sync` was acceptable while only a few engineers were the audience.
- **Multi-provider abstraction was speculative complexity.** The project picked Microsoft AI Foundry / Azure OpenAI. Anthropic and Google paths were carrying maintenance cost (config branches, validation, alternative payload shapes) for hypothetical future use.
- **The wiki markdown tree was a debug aid masquerading as a knowledge base.** Retrieval is from LanceDB. Tracking 46 markdown files in git meant every ingest run was either a noisy diff or a merge conflict. The cache stays on disk; git stops pretending to own it.

## Key Decisions

- **Subscribe at the drive root, filter at the adapter.** Microsoft Graph drive-item subscriptions don't reliably scope to a subfolder, so the adapter subscribes to the entire SharePoint document library and uses `is_in_scope` to discard notifications outside `SHAREPOINT_RAW_ROOT_PATH`.
- **Resolve item paths at webhook receive time, not at worker time.** The webhook route does a follow-up Graph call to convert each `resourceData.id` into a path before queueing. This keeps queue payloads self-contained and lets workers process webhook jobs without their own Graph credentials.
- **Validate `clientState` inside the adapter, not the route.** The adapter owns the contract with Graph and is the right boundary for the shared-secret check.
- **Delete Egnyte outright; no shim release.** The previous in-flight diff used compatibility shims (e.g., `EgnyteSourceSyncAdapter = SharePointSourceSyncAdapter`) to soften the transition. Since the project hasn't shipped externally, the shim added confusion without buying compatibility for any real caller.
- **Wiki detrack uses `git rm --cached`, not deletion.** Files stay on disk for human inspection of what the LLM is generating; only the git index forgets them.
- **Subscription renewal is left to operations for now.** The adapter exposes `renew_subscription`, but no background scheduler invokes it yet — drive-item subscriptions can live for ~3 days, so a startup hook plus an APScheduler / Azure WebJob / cron loop is the next concrete piece needed to keep webhooks alive in production.

## Verification

- Import smoke tests pass: `packages.wiki_core.ingest.ingest_service`, `apps.ingest_api.app`, `workers.source_sync_worker.worker`, `packages.wiki_core.ingest.sharepoint_adapter`, `rag_backend.llm`.
- Repository-wide grep for `egnyte`, `Egnyte`, `anthropic`, `Anthropic`, `gemini` returns zero matches across `packages/`, `apps/`, `workers/`, `teams_bot/`, `rag_backend/`, `.env.example`, and `README.md`.

## Result

The runtime now reads and writes a single source of truth (SharePoint via Graph), refreshes within seconds of a SharePoint edit (when a public webhook endpoint is configured), targets a single LLM stack (Azure OpenAI / Microsoft AI Foundry), and no longer drags a 300-file regeneratable markdown cache through git. Several speculative abstractions and one parallel ingest pipeline were retired in the process, which should make the next round of operational work — durable bot storage, subscription renewal scheduling, and Teams manifest publishing — meaningfully easier to plan against.

## Follow-ups Not in This Phase

- Stand up a public HTTPS endpoint for `SHAREPOINT_WEBHOOK_NOTIFICATION_URL` (Graph cannot deliver to localhost).
- Add a subscription create-on-startup hook and a half-life renewer (cron, Azure Scheduled WebJob, or in-process APScheduler).
- Production-readiness items already tracked in the README "Before publishing" list: durable bot storage in place of `MemoryStorage`, real Teams manifest IDs/icons, and Bot/Entra registration.
