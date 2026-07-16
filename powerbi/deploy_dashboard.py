"""Deploy the TrainingBotAnalytics semantic model + report to a Power BI workspace.

Uses Microsoft's fabric-cicd library, which publishes the SemanticModel before
the Report and rewrites the report's definition.pbir dataset reference from the
local byPath to a byConnection pointing at the deployed model.

Prerequisites:
- `az login` as a user with Power BI Pro and Contributor (or higher) on the
  target workspace
- pip install -r powerbi/requirements.txt

Run: python powerbi/deploy_dashboard.py --workspace "<Workspace Name>"

After the first deploy, bind data source credentials once in the Service
(workspace -> TrainingBotAnalytics semantic model -> Settings -> Data source
credentials -> Edit credentials -> OAuth2), then run set_refresh_schedule.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", required=True, help="Target Power BI workspace name")
    parser.add_argument(
        "--directory",
        default=str(SCRIPT_DIR / "TrainingBotAnalytics"),
        help="Folder containing the .SemanticModel and .Report item folders",
    )
    parser.add_argument("--skip-validate", action="store_true", help="Skip the offline validation pass")
    args = parser.parse_args()

    if not args.skip_validate:
        sys.path.insert(0, str(SCRIPT_DIR))
        import validate_report

        if validate_report.main([]) != 0:
            print("Aborting deploy: offline validation failed.", file=sys.stderr)
            return 1

    from azure.identity import AzureCliCredential
    from fabric_cicd import FabricWorkspace, publish_all_items

    workspace = FabricWorkspace(
        workspace_name=args.workspace,
        repository_directory=args.directory,
        item_type_in_scope=["SemanticModel", "Report"],
        token_credential=AzureCliCredential(),
    )
    publish_all_items(workspace)

    print(
        "\nDeployed. Next steps:\n"
        f"1. One-time: in the Power BI Service, open workspace '{args.workspace}' -> "
        "TrainingBotAnalytics (semantic model) -> Settings -> Data source credentials -> "
        "Edit credentials -> OAuth2 -> sign in.\n"
        f'2. python powerbi/set_refresh_schedule.py --workspace "{args.workspace}" --refresh-now'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
