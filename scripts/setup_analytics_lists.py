"""Provision the SharePoint lists that receive training-bot analytics events.

Creates two lists on the configured SharePoint site (idempotent — existing
lists are left untouched):

- TrainingBotQueryEvents: one row per (answered query x matched concept)
- TrainingBotFeedback: one row per thumbs up/down submission

The lists are read by the Power BI dashboard (see docs/analytics-dashboard.md).
By design they never contain question or answer text.

Uses the same app-only Graph credentials as SharePoint ingest
(SHAREPOINT_TENANT_ID / SHAREPOINT_CLIENT_ID / SHAREPOINT_CLIENT_SECRET and the
site settings). List creation requires write access to the site (e.g.
Sites.ReadWrite.All, or the manage role under Sites.Selected); a 403 here means
the app registration needs that permission granted — as a fallback, create the
lists manually with the exact column names printed by --dry-run.

Run:

    python -m scripts.setup_analytics_lists [--dry-run]
        [--query-list NAME] [--feedback-list NAME]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.wiki_core.analytics.sharepoint_lists import (  # noqa: E402
    FEEDBACK_COLUMNS,
    QUERY_EVENT_COLUMNS,
    SharePointListClient,
)
from packages.wiki_core.settings import CoreSettings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    settings = CoreSettings.from_env()
    parser.add_argument(
        "--query-list",
        default=settings.analytics_query_list_name,
        help="Display name for the query-events list",
    )
    parser.add_argument(
        "--feedback-list",
        default=settings.analytics_feedback_list_name,
        help="Display name for the feedback list",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the lists and columns that would be created without calling Graph",
    )
    args = parser.parse_args()

    plan = (
        (args.query_list, QUERY_EVENT_COLUMNS),
        (args.feedback_list, FEEDBACK_COLUMNS),
    )

    if args.dry_run:
        for list_name, columns in plan:
            print(f"List: {list_name}")
            for column in columns:
                kind = next(key for key in column if key != "name")
                print(f"  - {column['name']} ({kind})")
        return 0

    client = SharePointListClient(settings)
    for list_name, columns in plan:
        try:
            created = client.ensure_list(list_name, columns)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 403:
                print(
                    f"ERROR: Graph returned 403 creating {list_name!r}. The app registration "
                    "lacks permission to create lists on this site (needs Sites.ReadWrite.All "
                    "or the manage role under Sites.Selected). Either grant it, or create the "
                    "list manually with the columns shown by --dry-run.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"ERROR: Graph returned {status} creating {list_name!r}: "
                    f"{exc.response.text[:500]}",
                    file=sys.stderr,
                )
            return 1
        print(f"{'Created' if created else 'Already exists'}: {list_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
