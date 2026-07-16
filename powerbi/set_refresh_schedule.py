"""Set the refresh schedule for the deployed TrainingBotAnalytics semantic model.

Resolves the workspace and dataset by name via the Power BI REST API using the
Azure CLI login (`az login` first), PATCHes a daily refresh schedule, and with
--refresh-now triggers an immediate refresh and polls it to completion.

Credentials note: the FIRST refresh requires a one-time manual step in the
Service (semantic model Settings -> Data source credentials -> Edit -> OAuth2 ->
sign in). OAuth tokens pushed via API expire after ~1 hour, so only the UI bind
stores a durable refresh token for SharePoint Online.

Run: python powerbi/set_refresh_schedule.py --workspace "<Workspace Name>" [--refresh-now]
"""

from __future__ import annotations

import argparse
import sys
import time

import requests

BASE = "https://api.powerbi.com/v1.0/myorg"
CREDENTIAL_FIX = (
    "Fix: in the Power BI Service open the workspace -> TrainingBotAnalytics (semantic "
    "model) -> Settings -> Data source credentials -> Edit credentials -> OAuth2 -> sign in, "
    "then re-run this script."
)


def _token() -> str:
    from azure.identity import AzureCliCredential

    return AzureCliCredential().get_token("https://analysis.windows.net/powerbi/api/.default").token


def _get(session: requests.Session, url: str) -> dict:
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--dataset", default="TrainingBotAnalytics")
    parser.add_argument("--time", default="07:00", help="Daily refresh time (UTC unless --timezone set)")
    parser.add_argument("--timezone", default="UTC", help="Windows time zone id, e.g. 'Eastern Standard Time'")
    parser.add_argument("--refresh-now", action="store_true", help="Trigger an immediate refresh and poll it")
    parser.add_argument("--poll-timeout", type=int, default=900, help="Seconds to wait for --refresh-now")
    args = parser.parse_args()

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {_token()}"

    # The personal workspace is not a "group" in the REST API; its datasets live
    # directly under /myorg. Shared workspaces resolve via /groups by name.
    if args.workspace.strip().lower() == "my workspace":
        scope = BASE
    else:
        groups = _get(session, f"{BASE}/groups?$filter=name eq '{args.workspace}'").get("value", [])
        if len(groups) != 1:
            print(f"ERROR: found {len(groups)} workspaces named {args.workspace!r}", file=sys.stderr)
            return 1
        scope = f"{BASE}/groups/{groups[0]['id']}"

    datasets = _get(session, f"{scope}/datasets").get("value", [])
    matches = [d for d in datasets if d.get("name") == args.dataset]
    if len(matches) != 1:
        print(f"ERROR: found {len(matches)} datasets named {args.dataset!r} in {args.workspace!r}", file=sys.stderr)
        return 1
    dataset_id = matches[0]["id"]

    schedule = {
        "value": {
            "enabled": True,
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            "times": [args.time],
            "localTimeZoneId": args.timezone,
            "notifyOption": "MailOnFailure",
        }
    }
    response = session.patch(
        f"{scope}/datasets/{dataset_id}/refreshSchedule", json=schedule, timeout=60
    )
    if response.status_code >= 400:
        print(f"ERROR setting schedule ({response.status_code}): {response.text[:500]}", file=sys.stderr)
        if response.status_code == 400:
            print(CREDENTIAL_FIX, file=sys.stderr)
        return 1
    print(f"Refresh schedule set: daily at {args.time} ({args.timezone}).")

    if not args.refresh_now:
        return 0

    response = session.post(
        f"{scope}/datasets/{dataset_id}/refreshes",
        json={"notifyOption": "NoNotification"},
        timeout=60,
    )
    if response.status_code >= 400:
        print(f"ERROR triggering refresh ({response.status_code}): {response.text[:500]}", file=sys.stderr)
        print(CREDENTIAL_FIX, file=sys.stderr)
        return 1
    print("Refresh triggered; polling...")

    deadline = time.monotonic() + args.poll_timeout
    while time.monotonic() < deadline:
        time.sleep(15)
        latest = _get(session, f"{scope}/datasets/{dataset_id}/refreshes?$top=1").get("value", [])
        status = latest[0].get("status") if latest else "Unknown"
        print(f"  status: {status}")
        if status == "Completed":
            print("Refresh completed successfully.")
            return 0
        if status in {"Failed", "Disabled"}:
            print(f"Refresh {status}: {latest[0].get('serviceExceptionJson', '')[:1000]}", file=sys.stderr)
            print(CREDENTIAL_FIX, file=sys.stderr)
            return 1
    print("Timed out waiting for refresh; check the Service refresh history.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
