# Training Bot Analytics → Power BI Dashboard

The Teams training bot records **which wiki concepts PMs ask about** and **the
feedback they submit** into two SharePoint lists on the existing team site.
Power BI reads those lists directly — no extra Azure resources.

## What is (and is not) collected

Collected per answered question: the matched concept title(s), the asker's Teams
display name and Teams user id, a timestamp, and an opaque request id.
Collected per feedback submission: the rating (👍 helpful / 👎 inaccurate), the
optional free-text comment, and the concepts the answer was about.

**Never collected: the text of the user's question or the bot's answer.** The
analytics code has no field for them (`teams_bot/services/analytics.py`), and
questions that match no wiki concept are recorded only as `Unknown`. Users are
notified via the bot's welcome message and a footer on every answer card.

## The two lists

### `TrainingBotQueryEvents` — one row per (answered question × matched concept)

| Column | Type | Notes |
| --- | --- | --- |
| Title | text | Same as Concept (SharePoint's built-in column) |
| Timestamp | dateTime | UTC, ISO 8601 |
| RequestId | text | Groups rows from the same question (max 3 concepts per question) |
| UserId | text | Teams user id (stable, opaque) |
| UserName | text | Teams display name — use for the user slicer |
| Concept | text | Wiki concept title, or `Unknown` |
| IsUnknown | boolean | True when no wiki concept matched |

### `TrainingBotFeedback` — one row per feedback submission

| Column | Type | Notes |
| --- | --- | --- |
| Title | text | Same as Rating |
| Timestamp | dateTime | UTC |
| RequestId | text | Joins back to `TrainingBotQueryEvents.RequestId` |
| UserId / UserName | text | As above |
| Rating | text | `helpful` or `inaccurate` |
| Comment | multiline text | Optional free text typed on the answer card |
| Concepts | text | `"; "`-joined concept titles of the answered question |

## Provisioning the lists

```
python -m scripts.setup_analytics_lists --dry-run   # show what would be created
python -m scripts.setup_analytics_lists             # create (idempotent)
```

Uses the same app-only Graph credentials as SharePoint ingest (`SHAREPOINT_*`
env vars). A **403** means the app registration cannot create lists (needs
`Sites.ReadWrite.All`, or the manage role under `Sites.Selected`); as a
fallback, create the lists manually with exactly the column names above —
list-item *writes* generally work with the permissions the app already uses to
upload wiki files.

Config knobs (App Service settings / `.env`):

- `ANALYTICS_ENABLED` — set `false` to turn the pipeline off (default `true`)
- `ANALYTICS_QUERY_LIST_NAME` / `ANALYTICS_FEEDBACK_LIST_NAME` — list names
  (defaults above; point at `...-Test` lists to verify without polluting prod)

The pipeline is fail-soft: if SharePoint is unreachable or misconfigured, the
bot logs one warning and keeps answering normally; events are dropped, never
queued.

## Building the Power BI report

1. In Power BI Desktop: **Get Data → Online Services → SharePoint Online List**
   (2.0 implementation), site URL = the team site root (not the list URL).
   Sign in with an org account that can read the site.
2. Select `TrainingBotQueryEvents` and `TrainingBotFeedback`.
3. In Power Query, expand the record columns so `Timestamp`, `RequestId`,
   `UserId`, `UserName`, `Concept`, `IsUnknown`, `Rating`, `Comment`,
   `Concepts` are top-level columns; set `Timestamp` to Date/Time (it arrives
   as UTC) and `IsUnknown` to True/False. Remove the other SharePoint system
   columns.
4. Model: no relationship between the two tables is required for the core
   visuals; if you want feedback filtered by concept slicers, relate
   `TrainingBotQueryEvents[RequestId]` (many) ↔ `TrainingBotFeedback[RequestId]`
   (many) or slice each visual independently.

Suggested measures:

```dax
Questions Asked = DISTINCTCOUNT ( TrainingBotQueryEvents[RequestId] )
Unknown Questions = CALCULATE ( [Questions Asked], TrainingBotQueryEvents[IsUnknown] = TRUE() )
Helpful % =
DIVIDE (
    CALCULATE ( COUNTROWS ( TrainingBotFeedback ), TrainingBotFeedback[Rating] = "helpful" ),
    COUNTROWS ( TrainingBotFeedback )
)
```

Suggested layout:

- **Bar chart** — count of rows by `Concept`, visual-level filter
  `IsUnknown = False`: the "what are PMs asking about" headline.
- **Cards** — `Questions Asked`, `Unknown Questions`, `Helpful %`.
- **Donut** — feedback rows by `Rating`.
- **Table** — `Timestamp`, `UserName`, `Rating`, `Concepts`, `Comment` from
  `TrainingBotFeedback` so dashboard users can read the free-text feedback.
- **Slicers** (sync across pages): `UserName`, and `Timestamp` with the date
  hierarchy (or a relative-date slicer).

Publish to the Power BI Service and set **scheduled refresh** — SharePoint
Online lists refresh with OAuth credentials, no gateway needed. A daily or
hourly refresh is plenty at this volume.

## Adding new training material (ingest)

There is no upload button in the dashboard because none is needed: ingest is
event-driven, with a scheduled sweep as a safety net.

1. Upload the new file to the **`raw/sources`** folder of the *Training
   Program Vault* library on the team site. **Only `.docx`, `.pdf`, `.pptx`,
   `.xlsx`, and `.xlsm` files are ingested** — anything else (videos, images,
   `.txt`/`.md`) is skipped silently. A Microsoft Graph webhook (delivered via
   the bot's public `/api/webhooks/sharepoint` endpoint and kept alive by an
   hourly subscription renewer) queues an ingest job, and the worker
   regenerates the relevant wiki pages and refreshes the bot's vector index —
   typically within a few minutes. Requires `SHAREPOINT_WEBHOOK_NOTIFICATION_URL`
   and `SHAREPOINT_WEBHOOK_CLIENT_STATE` app settings.
2. Even if a notification is missed, the worker runs a **reconciliation sync
   every 6 hours** (`INGEST_RECONCILE_HOURS`, `0` disables) that picks up any
   new or changed files.
3. On-demand / bulk refresh: type **`/sync`** to the bot in Teams to queue a
   full SharePoint re-sync (or run
   `python -m packages.wiki_core.ingest.ingest_service --manual`).

Tip: pin a link to the `raw/sources` folder on the dashboard page so
dashboard users can jump straight to the upload location.

## Data handling: scoping who can see the analytics

These lists contain user-attributed activity (names + topics + feedback text).
By default they inherit the team site's permissions, so **every site member
could browse them**. To scope access to dashboard users only:

1. **Create a security group** in Entra (e.g. `Training Dashboard Users`)
   containing the people who need the dashboard. The group is the ongoing
   control: adding/removing a member grants/revokes access everywhere it's
   used. A dynamic membership rule (e.g. by job title or department) makes
   this fully automatic.
2. **Break inheritance on each list** and grant that group (plus the account
   Power BI uses for scheduled refresh) Read access. In the SharePoint UI:
   list ⚙️ **Settings → Permissions for this list → Stop Inheriting
   Permissions**, remove the site members entry, then **Grant Permissions →**
   the security group with *Read*. Or as a one-time PnP PowerShell script:

   ```powershell
   Connect-PnPOnline -Url https://<tenant>.sharepoint.com/sites/<site> -Interactive
   foreach ($list in 'TrainingBotQueryEvents', 'TrainingBotFeedback') {
       Set-PnPList -Identity $list -BreakRoleInheritance -CopyRoleAssignments:$false
       # Entra group claims format: c:0t.c|tenant|<group object id>
       Set-PnPListPermission -Identity $list -User 'c:0t.c|tenant|<group-object-id>' -AddRole 'Read'
   }
   ```

3. **Scope the Power BI side too — this is the one that actually gates
   dashboard viewers.** Once the report is published, viewers read data from
   the *dataset*, not from SharePoint, so SharePoint permissions no longer
   apply to them. Share the workspace/app with the same security group and no
   one else, and don't grant dataset Build permission beyond report authors.

The bot's own writes are unaffected by any of this: it writes app-only via
`Sites.Selected`, which bypasses list-level user permissions.

Apply your normal retention practices to both lists. Keeping question/answer
text out of the store is enforced in code; keep it that way when extending
the pipeline.
