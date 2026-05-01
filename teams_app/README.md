# Teams App Package

This folder contains the Microsoft Teams app manifest for the `Graydaze PM Training Vault` bot.

## Before packaging

Replace these placeholders in `manifest.json`:

- `__TEAMS_APP_ID__`
  - A unique app package GUID for the Teams app manifest itself
- `__BOT_APP_ID__`
  - The Microsoft Entra app ID used by the Azure Bot registration
- `__BOT_HOSTNAME__`
  - The public HTTPS hostname serving `/api/messages`

## Required package contents

Zip these three files together when publishing to Teams:

- `manifest.json`
- `color.png` (192x192)
- `outline.png` (32x32)

The current repository does not include production icon assets yet, so add them before packaging.

## Publishing paths

- Personal/test install: upload the app package in Teams Developer Portal or Teams client if sideloading is allowed
- Org-wide availability: a Teams admin publishes the package to the tenant app catalog and can pin/allow it organization-wide
