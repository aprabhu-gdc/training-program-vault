#!/usr/bin/env bash
# Azure App Service (Linux) startup command for the Stage 1 Teams pilot.
#
# Set the App Service "Startup Command" to:  bash startup.sh
#
# Topology (single App Service instance):
#   - ingest API   -> private, binds INGEST_API_PORT (8010); reached by the bot
#                     via INGEST_ADMIN_HTTP_URL=http://localhost:8010
#   - sync worker  -> background process consuming the Service Bus queue
#   - Teams bot    -> the only PUBLIC process; binds $PORT (App Service routes
#                     inbound HTTPS here, i.e. /api/messages)
#
# Required App Service settings (Configuration > Application settings):
#   PORT=8000              WEBSITES_PORT=8000
#   INGEST_API_PORT=8010   (MUST be set; ingest config otherwise falls back to
#                           PORT and would collide with the bot)
#   INGEST_ADMIN_HTTP_URL=http://localhost:8010
#   WIKI_QUERY_CALLABLE=rag_backend.query:query_vault
#   MicrosoftAppId / MicrosoftAppPassword / MicrosoftAppType=SingleTenant /
#   MicrosoftAppTenantId
#   ...plus all existing LLM / SharePoint / Service Bus settings.
# Enable "Always On" so the worker keeps running.
set -euo pipefail

log() { echo "[startup] $*"; }

# Background: private ingest API (manual /sync endpoint).
log "starting ingest API on port ${INGEST_API_PORT:-8010}"
python -m apps.ingest_api.app &

# Background: Service Bus sync worker.
log "starting source sync worker"
python -m workers.source_sync_worker.worker &

# Foreground (PID 1): the public Teams bot on $PORT.
log "starting Teams bot on port ${PORT:-3978}"
exec python app.py
