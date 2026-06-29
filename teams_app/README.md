# Teams App Package

This folder contains the Microsoft Teams app manifest for the `Graydaze PM Training Vault` bot.

## Building the package

Use the build script instead of editing `manifest.json` by hand. It substitutes
the placeholders (`__TEAMS_APP_ID__`, `__BOT_APP_ID__`, `__BOT_HOSTNAME__`),
validates the result, and zips `manifest.json` + the two icons into
`dist/teams_app.zip`:

```bash
python -m scripts.build_teams_package \
    --bot-app-id <ENTRA_APP_CLIENT_ID> \
    --host <app>.azurewebsites.net
```

- `--teams-app-id` is optional; it defaults to a stable GUID derived from the bot
  id, so repeat builds produce the same package id. Pass your own to override.
- Values may also be supplied via `BOT_APP_ID` / `BOT_HOSTNAME` / `TEAMS_APP_ID`.

## Icons

`color.png` (192x192) and `outline.png` (32x32) are committed as **placeholder**
assets (Graydaze accent color). The build script regenerates them if missing.
Replace both with real brand art before a wider rollout — overwrite the two files
and rebuild.

## Publishing paths

- Pilot / test install (Stage 1): upload `dist/teams_app.zip` via the Teams client
  "Upload a custom app" option or the Teams Developer Portal. A tenant admin may
  need to allow custom app uploads for the pilot users.
- Org-wide availability (Stage 2): a Teams admin publishes the package to the
  tenant app catalog and can pin/allow it organization-wide.
