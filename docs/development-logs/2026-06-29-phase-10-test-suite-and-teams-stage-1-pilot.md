# 2026-06-29 Phase 10: Backend Test Suite and Teams Stage 1 Pilot Deployment

## Summary

Two threads of work after Phase 09. First, a repeatable automated test suite was
added to validate the Phase 09 surgery (which had only been import-smoke-tested):
111 offline tests plus one live end-to-end smoke, all green. Second, the project
moved from "code exists" to "running in Azure" for a Stage 1 Teams pilot: the bot
was re-wired for single-tenant auth, packaging/ops scripts were added, and the
runtime was deployed to an Azure App Service in resource group `rg-Notify-6121`.
The bot, ingest API, and Service Bus worker are running and `/healthz` returns
200. The only remaining blocker for a live Teams conversation is the bot's Entra
app registration, which requires an admin.

## Changes Implemented

### Test suite (committed)

- pytest scaffolding: `pytest.ini`, `requirements-dev.txt`, `tests/conftest.py`
  (deterministic `CoreSettings` factory; offline by default, live auto-skips).
- `tests/unit/`: markdown chunking + the 6000-char cap, single-provider settings
  (Anthropic/Google removed), `rag_backend.llm` surface, string-based sync event
  types, SharePoint adapter pure helpers.
- `tests/seams/`: webhook parsing + the `clientState` security boundary, ingest
  API routes + the Graph `validationToken` handshake, worker job dispatch,
  Service Bus lock-loss handling, the managed-page frontmatter-dedup fix, query
  service + query API, and bot config (single-tenant).
- `tests/e2e/test_live_index_and_query.py`: builds a real index from `wiki/` and
  asserts a grounded, cited answer via Azure OpenAI (marked `live`).
- Result: `pytest -m "not live"` = 111 passed; live smoke passed in ~2.5 min.

### Teams Stage 1 pilot enablement (committed)

- Single-tenant auth: `app.py` now uses `CloudAdapter` +
  `ConfigurationBotFrameworkAuthentication` (from `botbuilder.integration.aiohttp`);
  `teams_bot/config.py` adds `MicrosoftAppType` / `MicrosoftAppTenantId`.
  `requirements.txt` adds `botbuilder-integration-aiohttp`.
- `startup.sh`: App Service startup running the ingest API (private `:8010`) and
  the sync worker in the background and the bot (public, `$PORT`) in the foreground.
- `scripts/build_teams_package.py`: substitutes the manifest placeholders, zips
  with icons into `dist/teams_app.zip`, and generates placeholder icons.
- `scripts/push_appsettings.py`: pushes `.env` to App Service (curated overrides,
  excludes bot creds + local paths) via `az`, or exports portal "Advanced edit"
  JSON when CLI writes are blocked.
- `teams_app/color.png` + `outline.png`: committed placeholder icons.
- Docs: README pilot-deploy section, `teams_app/README.md`, `.env.example`.
- Committed to `main` as `ae8fdc6` and pushed to GitHub.

### Azure App Service deployment (provisioned + verified)

- Resource group `rg-Notify-6121` (eastus2) already held: Azure AI Foundry account
  + project (`pm-training-program-resource`), Service Bus namespace + queue
  (`training-vault-ingest`), App Service Plan (B1 Linux), and the Web App
  `graydaze-pm-training-vault` (Linux, Python 3.13, Always On).
- Configured: startup command `bash startup.sh`; system-assigned managed identity
  granted **Azure Service Bus Data Sender + Receiver** on the namespace (the
  runtime authenticates to the queue via `DefaultAzureCredential`); 37 application
  settings applied (LLM = Azure OpenAI `gpt-5.4-nano` chat/vision +
  `text-embedding-3-large`; data paths under `/home/data`; `MicrosoftAppType`
  temporarily `MultiTenant`).
- Deployed via GitHub Actions (OIDC) wired through Deployment Center; clean Oryx
  build; all three processes started and `/healthz` returns 200.

## Why These Changes Were Implemented

- **Test before Teams.** Phase 09 was only import-tested; standing up an
  internet-facing bot on an unverified backend would mean debugging two unknowns
  at once. The suite makes the backend trustworthy and the changes regression-safe.
- **Pilot first.** Get a working bot to a few users fast (sideload, MemoryStorage,
  manual `/sync`), defer production hardening to Stage 2.
- **Single-tenant.** Correct security posture for an internal whole-company bot;
  the bot is usable only inside the GRAYDAZE tenant.

## Key Decisions

- **One App Service, bot-only public surface.** Bot is the only public process;
  the ingest API stays on `localhost`, so nothing but the bot is internet-exposed
  this stage. RAG query runs in-process in the bot.
- **Dedicated bot identity.** A new single-tenant app registration, separate from
  the SharePoint-sync app, so the bot identity carries no Graph/SharePoint access.
- **GitHub Actions OIDC for deploy.** SCM basic-auth publishing is disabled in the
  tenant, so OIDC (not a publish profile) is the deploy path; the GitHub repo was
  already the source of truth.
- **Worked around tenant policy.** Conditional Access requires MFA for ARM writes,
  which the headless CLI session couldn't satisfy — so writes (startup command,
  identity, role grants, app settings) were done in the portal/Kudu where the
  browser session is MFA-compliant; reads were done via CLI.
- **Webhook deferred.** Stage 1 uses manual `/sync`; the public webhook +
  subscription renewer remain Stage 2.

## Verification

- `pytest -m "not live"` → 111 passed; `pytest -m live` → passed.
- App Service: GitHub Actions deploy succeeded (clean Oryx build); container log
  shows all three startup processes; `GET /healthz` → 200.
- Azure config confirmed via read-only `az`: startup command, managed identity
  principal, and both Service Bus role assignments.

## Status and Follow-ups

- **Blocked:** the single-tenant bot Entra app registration (admin task; runbook
  handed off). No Teams conversation until its client ID / tenant ID / secret are
  returned and wired into the Azure Bot resource + App Service settings.
- **In progress:** first SharePoint sync to build the LanceDB index (via the
  in-container ingest API) so queries return answers and the full backend is
  validated on App Service.
- **Reliability fix (found while monitoring the first sync):** the worker crashed
  on a transient `AMQPLinkError: Link detached unexpectedly` raised from
  `receive_messages` (outside the per-message try/except), and nothing restarted
  it, leaving jobs stranded in the queue. Fixed by (a) a `_poll_once` wrapper in
  `workers/source_sync_worker/worker.py` that logs and swallows transient
  Service Bus/AMQP errors so the loop keeps running, and (b) supervising the
  background ingest API + worker in `startup.sh` with restart loops so a hard
  crash relaunches them.
- **Operational note:** cold start is ~150s; the first boot exceeded the default
  230s container-start limit and auto-retried. `WEBSITES_CONTAINER_START_TIME_LIMIT`
  should be raised to avoid restart flapping.
- **Stage 2 (deferred):** durable bot storage (Azure Blob), public webhook +
  subscription renewer, org-wide Teams catalog publish + real brand icons,
  Key Vault references for secrets, and (optionally) managed-identity bot auth.
