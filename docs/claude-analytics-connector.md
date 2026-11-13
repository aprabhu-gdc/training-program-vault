# Claude analytics connector — setup runbook

The **Graydaze Training Analytics** connector is a small remote MCP server
(`apps/analytics_mcp/`) that Claude calls from the PM Training Vault project.
It replaces the Teams bot's analytics writes so the existing dashboard keeps
working after the move to Claude.

```
PM asks in the Claude project
  -> Claude answers from SharePoint (Microsoft 365 connector, read-only)
  -> Claude calls record_question / submit_feedback -> this connector
       -> TrainingBotQueryEvents / TrainingBotFeedback (same lists, same columns)
            -> Power BI report (unchanged) and/or the Claude dashboard artifact
Admin opens the dashboard -> get_analytics (Analytics.Admin role only)
```

## What it records (and never records)

| Tool | Who | Writes / returns |
|---|---|---|
| `record_question(page_path?, page_title?)` | anyone assigned to the app | QueryEvents row: concept label + title, user (from sign-in), UTC timestamp, request id. No arguments = `Unknown`. |
| `submit_feedback(request_id, rating, comment?)` | the person who asked | Feedback row with the same columns as the bot wrote |
| `get_analytics(start_date?, end_date?, user_id?)` | `Analytics.Admin` role | Same figures as the Power BI model: Questions Asked, Unknown Questions, questions by concept, Feedback/Helpful/Inaccurate counts, Helpful %, ratings, daily series, users, feedback table |

- **Never recorded:** question text or answer text. No tool accepts them. `page_title` is validated as a page title (≤14 words, no `?`, no quotes) and `page_path` must be a `wiki/<category>/<file>.md` path, so a misbehaving client cannot write question text into the lists.
- **Identity** comes only from the person's Entra ID sign-in (`oid` + display name), never from tool arguments. `UserId` is now the Entra object id (the bot wrote the Teams user id), so a person's old and new rows appear as two users in the dashboard until you map them.
- Max 30 records per person per minute.

## 1. Entra ID app registration (admin)

Create **one** app registration, used both for sign-in and for writing to the lists.

1. **App registrations → New registration**: name `Graydaze Training Analytics Connector`, single tenant. No redirect URI yet; you add it after deploying.
2. **Expose an API**: set the Application ID URI to `api://<client-id>`. Add the scope `analytics.use` (who can consent: admins only).
3. **Manifest**: set `"requestedAccessTokenVersion": 2` (under `api` in the new manifest editor).
4. **App roles → Create app role**: display name `Analytics Admin`, value `Analytics.Admin`, allowed member types *Users/Groups*.
5. **Certificates & secrets**: new client secret, 6–12 month expiry. Put a reminder in the calendar to rotate it.
6. **API permissions → Microsoft Graph → Application permissions**:
   - Preferred: `Lists.SelectedOperations.Selected` (access to individual lists only).
   - Fallback: `Sites.Selected` (access to the whole site you grant).
   - **Do not** add `Sites.ReadWrite.All`.
   - **Grant admin consent.** Also grant admin consent for the `analytics.use` scope.
7. **Enterprise applications → (this app) → Properties**: set **Assignment required = Yes**.
   **Users and groups**: add `PM Training Readers` with the default role, and `Vault Maintainers` with `Analytics Admin`.
   Only assigned people can sign in. Only `Analytics Admin` can read the report.

### Grant the app access to the two lists

These calls use [Graph Explorer](https://developer.microsoft.com/graph/graph-explorer), signed in as a SharePoint admin.

1. Find the site id: `GET https://graph.microsoft.com/v1.0/sites/graydazeb.sharepoint.com` (or `.../sites/{hostname}:{site-path}` if the lists live on a sub-site — use the site from the bot's `SHAREPOINT_SITE_*` settings).
2. Find the list ids: `GET https://graph.microsoft.com/v1.0/sites/{site-id}/lists?$select=id,displayName`. Note the ids of `TrainingBotQueryEvents` and `TrainingBotFeedback`.
3. Grant write on each list (list-level, preferred):
   ```http
   POST https://graph.microsoft.com/v1.0/sites/{site-id}/lists/{list-id}/permissions
   { "roles": ["write"], "grantedTo": { "application": { "id": "<client-id>" } } }
   ```
   If list-level grants are not available in your tenant, use `Sites.Selected` and grant the site instead:
   ```http
   POST https://graph.microsoft.com/v1.0/sites/{site-id}/permissions
   { "roles": ["write"], "grantedToIdentities": [ { "application": { "id": "<client-id>", "displayName": "Graydaze Training Analytics Connector" } } ] }
   ```
   ⚠️ A site-level grant lets the app write anywhere on that site, including the vault library. Prefer the list-level grant.

## 2. Container image

The workflow `.github/workflows/analytics-connector.yml` tests, builds and pushes
`ghcr.io/<owner>/training-analytics-connector:<sha>` on every push to `main`
that touches the connector. You can also run it from the Actions tab
(**Run workflow** on any branch) to build an image before merging.

Container Apps needs a token to pull the private image. Create a **classic**
GitHub PAT with only `read:packages`, and set an expiry.

## 3. Deploy the infrastructure

```bash
az group create --name rg-training-analytics --location eastus2

python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> jwtSigningKey; store it in your password manager

az deployment group create \
  --resource-group rg-training-analytics \
  --template-file infra/analytics-connector/main.bicep \
  --parameters namePrefix=gdcanalytics \
               image=ghcr.io/aprabhu-gdc/training-analytics-connector:<sha> \
               registryUsername=<github-user> registryPassword=<read:packages PAT> \
               tenantId=<tenant-id> connectorClientId=<client-id> connectorClientSecret=<secret> \
               jwtSigningKey=<generated> sharepointSiteId=<site-id> \
               queryListId=<TrainingBotQueryEvents id> feedbackListId=<TrainingBotFeedback id> \
               businessHoursTimeZone=America/New_York
```

Pass the secrets interactively or from a parameters file kept **outside** the
SharePoint-synced folder. Never commit them.

The deployment outputs `connectorUrl` and `oauthRedirectUri`.

1. In the app registration, add `oauthRedirectUri` (`https://…/auth/callback`) under **Authentication → Web → Redirect URIs**.
2. Check `https://<app>/healthz` returns `{"status":"ok"}`. Check `connectorUrl` returns **401** without a token; that's expected.

Scaling: 0–1 replicas. One replica stays warm during business hours (`businessHoursStart`/`End`, cron in `businessHoursTimeZone`), and the app scales to zero otherwise. The first call after a cold start takes a few seconds. Estimated cost is about $1–5/month plus pennies for Table Storage.

### Automated deploys (optional)

So that merges to `main` roll out automatically:

1. Create an Entra app (or user-assigned identity) for GitHub with a **federated credential** for `repo:aprabhu-gdc/training-program-vault:ref:refs/heads/main`.
2. Give it **Contributor** on the resource group only.
3. Add the repository secrets `CONNECTOR_AZURE_CLIENT_ID`, `CONNECTOR_AZURE_TENANT_ID`, `CONNECTOR_AZURE_SUBSCRIPTION_ID`.
4. Add the repository variables `CONNECTOR_RESOURCE_GROUP` and `CONNECTOR_APP_NAME` (`<namePrefix>-connector`).

## 4. Add the connector in Claude (Owner)

1. **Admin settings → Connectors → Add custom connector**: name `Graydaze Training Analytics`, URL = `connectorUrl` (ends in `/mcp`). Leave the OAuth client ID/secret blank; the connector registers Claude automatically and only accepts Claude's callback URLs.
2. Each person: **Settings → Connectors → Graydaze Training Analytics → Connect**, then sign in with their work account and approve the consent screen. Connect it yourself first.
3. Paste `docs/claude-project-instructions.md` into the project's instructions. In project chats, make sure both Microsoft 365 and Graydaze Training Analytics are enabled in the tools menu.

## 5. Test

| As | Do | Expect |
|---|---|---|
| Pilot PM | Ask "How deep should we fill a slab joint with semi-rigid filler if the saw cut is deeper than 2 inches?" | Cited answer; a new `TrainingBotQueryEvents` row with Concept `ACI Joint Filler Depth`, the PM's name, **no question text** |
| Pilot PM | "That was helpful" | A `TrainingBotFeedback` row, same RequestId, Rating `helpful` |
| Pilot PM | Ask something the wiki doesn't cover | Row with Concept `Unknown`, IsUnknown = Yes |
| Pilot PM | Ask Claude to run `get_analytics` | Refused, "limited to training admins" |
| Admin | Ask Claude for "training analytics for this month" | Totals, concepts and feedback |
| Anyone not assigned | Try to connect | Microsoft sign-in refuses (assignment required) |

Then refresh the Power BI report. The new rows appear with no model changes.

## Operations

- **Rotate the client secret** before it expires: create a new one in Entra, then `az containerapp secret set -n <app> -g <rg> --secrets connector-client-secret=<new>`, then restart the revision.
- **Rotate `jwtSigningKey`** only if it leaks. Rotating it signs everyone out, and they must reconnect.
- **Logs**: Log Analytics workspace `<namePrefix>-logs`, kept 30 days. Logs show request ids and errors. Question text never reaches the connector, so it can't be logged.
- **Turn it off**: remove the connector in Claude admin settings, or `az containerapp update -n <app> -g <rg> --max-replicas 0`. Delete the resource group to remove everything.
- **Local development**: `pip install -r apps/analytics_mcp/requirements.txt`, set `ANALYTICS_MCP_BASE_URL=http://localhost:8000` and `ANALYTICS_MCP_DEV_NO_AUTH=true` (refused on any non-localhost URL), then `uvicorn apps.analytics_mcp.app:create_app --factory --port 8000`. This still writes to the real SharePoint lists configured by `SHAREPOINT_*`.
