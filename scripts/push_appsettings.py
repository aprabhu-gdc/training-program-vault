"""Push local .env values to an Azure App Service as application settings.

Reads `.env`, applies the Stage 1 pilot overrides (ports, Linux data paths,
in-process query, MultiTenant placeholder), excludes deferred bot credentials and
local-only path vars, and applies the result in a single
`az webapp config appsettings set` call.

Security: values are read locally and sent only to Azure. Nothing is printed, and
`--output none` stops the Azure CLI from echoing the settings (with values) back.

Requires the Azure CLI. Run `az login` first, or run inside Azure Cloud Shell
(upload your .env there). Apply with:

    python -m scripts.push_appsettings --resource-group <RG> --name graydaze-pm-training-vault

Use --dry-run to print only the setting KEY names (never values) that would be set.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

REPO_ROOT = Path(__file__).resolve().parents[1]

# Keys whose value must differ on App Service from local development.
OVERRIDES = {
    "PORT": "8000",
    "WEBSITES_PORT": "8000",
    "INGEST_API_PORT": "8010",
    "INGEST_ADMIN_HTTP_URL": "http://localhost:8010",
    "WIKI_QUERY_CALLABLE": "rag_backend.query:query_vault",
    "LOCAL_DATA_ROOT": "/home/data",
    "VAULT_ROOT": "/home/data/vault",
    # Deferred until the bot app registration exists; MultiTenant boots with blank creds.
    "MicrosoftAppType": "MultiTenant",
    # Make Oryx run `pip install -r requirements.txt` on git/zip deploy.
    "SCM_DO_BUILD_DURING_DEPLOYMENT": "true",
    # The three processes each import lancedb/pyarrow/openai/botbuilder, so cold
    # start is ~150s — above the 230s default but too close for comfort. Give it
    # headroom so platform restarts don't time out and flap.
    "WEBSITES_CONTAINER_START_TIME_LIMIT": "1800",
}

# Never push these from .env: bot creds (not created yet) and local/Windows-only
# paths that must fall back to the computed defaults under LOCAL_DATA_ROOT.
EXCLUDE = {
    "MicrosoftAppId",
    "MicrosoftAppPassword",
    "MicrosoftAppTenantId",
    "VECTOR_DB_PATH",
    "VECTOR_MANIFEST_PATH",
    "SOURCE_SYNC_STATE_PATH",
    "SYNC_JOB_STATE_PATH",
    "WIKI_QUERY_HTTP_URL",  # pilot uses the in-process callable
}


def build_settings(env_path: Path) -> dict[str, str]:
    raw = dotenv_values(env_path)
    merged: dict[str, str] = {}
    for key, value in raw.items():
        if key in EXCLUDE or value is None or value == "":
            continue
        merged[key] = value
    merged.update(OVERRIDES)  # overrides win over .env
    return dict(sorted(merged.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Push .env to an Azure App Service.")
    parser.add_argument("--resource-group", help="Required unless --out is used.")
    parser.add_argument("--name", help="Web App name. Required unless --out is used.")
    parser.add_argument("--env-file", default=str(REPO_ROOT / ".env"))
    parser.add_argument("--dry-run", action="store_true", help="Print KEY names only; do not call Azure.")
    parser.add_argument(
        "--out",
        help="Write portal 'Advanced edit' JSON (array of name/value/slotSetting) to this "
        "file instead of calling Azure. Use when CLI writes are blocked (e.g. MFA).",
    )
    args = parser.parse_args()

    env_path = Path(args.env_file)
    if not env_path.exists():
        print(f"error: {env_path} not found", file=sys.stderr)
        return 1

    settings = build_settings(env_path)
    if not settings:
        print("error: no settings to push", file=sys.stderr)
        return 1

    print(f"{len(settings)} settings to apply (names only):")
    for key in settings:
        print(f"  - {key}")

    if args.dry_run:
        return 0

    if args.out:
        import json

        portal = [{"name": k, "value": v, "slotSetting": False} for k, v in settings.items()]
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(portal, indent=2), encoding="utf-8")
        print(f"Wrote portal Advanced-edit JSON ({len(settings)} settings) to: {out_path}")
        print("Paste its contents into: Web App > Environment variables > Advanced edit > OK > Save.")
        return 0

    if not args.resource_group or not args.name:
        print("error: --resource-group and --name are required unless --out is used.", file=sys.stderr)
        return 1

    az = shutil.which("az")
    if not az:
        print("error: Azure CLI ('az') not found. Run `az login` or use Cloud Shell.", file=sys.stderr)
        return 1

    cmd = [
        az, "webapp", "config", "appsettings", "set",
        "--resource-group", args.resource_group,
        "--name", args.name,
        "--output", "none",  # suppress echo of values
        "--settings", *[f"{k}={v}" for k, v in settings.items()],
    ]
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print(f"Applied {len(settings)} settings to {args.name}.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
