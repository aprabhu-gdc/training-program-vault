# TrainingBotAnalytics — code-authored Power BI dashboard

A fully text-based Power BI project (PBIP): the semantic model is TMDL under
`TrainingBotAnalytics.SemanticModel/`, the report is PBIR JSON under
`TrainingBotAnalytics.Report/`. No `.pbix` binary — everything is reviewable
and diffable, and deployment is CLI-driven.

**Data**: the two SharePoint analytics lists the Teams bot writes to
(`TrainingBotQueryEvents`, `TrainingBotFeedback` — see
`docs/analytics-dashboard.md` for schemas and what is never collected).
Timestamps are UTC throughout.

**Model**: star schema — two import-mode fact tables, `DimUser` and `DimDate`
calculated dimensions (so the one User slicer and one Date slicer filter every
visual), and a `_Measures` table (`Questions Asked`, `Unknown Questions`,
`Known Concept Rows`, `Feedback Count`, `Helpful Count`, `Inaccurate Count`,
`Helpful %`). The bar chart uses `Known Concept Rows`, which returns BLANK for
`Unknown`, so the Unknown bucket drops out without a visual filter.

## Configuration coupling

- **SharePoint site URL** lives in ONE place:
  `TrainingBotAnalytics.SemanticModel/definition/expressions.tmdl`
  (M parameter `SharePointSiteUrl`).
- **List names** are hardcoded in the two `List = Source{[Title = "..."]}` lines
  of `definition/tables/QueryEvents.tmdl` and `Feedback.tmdl`. If you override
  `ANALYTICS_QUERY_LIST_NAME` / `ANALYTICS_FEEDBACK_LIST_NAME` for the bot,
  update those two lines to match.

## Deploy runbook

```
pip install -r powerbi/requirements.txt
python powerbi/validate_report.py            # offline schema + hygiene check
# Optional visual check: open powerbi/TrainingBotAnalytics/TrainingBotAnalytics.pbip
#   in Power BI Desktop; Refresh prompts an org-account OAuth sign-in.
az login                                     # as yourself (Pro + workspace Contributor)
python powerbi/deploy_dashboard.py --workspace "<Workspace Name>"
# ONE-TIME MANUAL STEP (platform limitation — API-set OAuth tokens last ~1h):
#   Power BI Service -> workspace -> TrainingBotAnalytics (semantic model)
#   -> Settings -> Data source credentials -> Edit credentials -> OAuth2 -> sign in
python powerbi/set_refresh_schedule.py --workspace "<Workspace Name>" --refresh-now
```

`deploy_dashboard.py` uses Microsoft's `fabric-cicd`: it publishes the semantic
model first, then the report, rewriting `definition.pbir` from the local
`byPath` reference to a `byConnection` against the deployed model.

## Access

The lists (and therefore this dashboard) contain user-attributed activity.
Share the workspace/app only with the intended audience — see "Data handling:
scoping who can see the analytics" in `docs/analytics-dashboard.md`. The
account whose credentials power the scheduled refresh needs Read on both lists
(remember this if list permission inheritance is ever broken).
