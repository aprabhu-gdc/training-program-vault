---
title: Log
type: log
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 1
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
---

# Log

## [2026-04-29] setup | initialize llm wiki vault

- Created `AGENTS.md` as the canonical schema for future interactions.
- Established the `raw/` and `wiki/` layer conventions described in the schema.
- Seeded `wiki/overview.md`, `wiki/index.md`, and `wiki/log.md`.

## [2026-04-29] ingest | LLM Wiki

- Raw source: [[raw/sources/2026-04-29-llm-wiki-idea|LLM Wiki]].
- Created `wiki/sources/llm-wiki-idea.md`.
- Created concept pages for `LLM Wiki`, `Ingest`, `Query`, `Lint`, and `Indexing and Logging`.
- Created `wiki/syntheses/second-brain-operating-model.md`.
- Updated `wiki/index.md` and `wiki/overview.md` to reflect the seed knowledge graph.

## [2026-04-29] maintenance | clarify folder ingest behavior

- Updated `AGENTS.md` to allow folder drops under `raw/sources/`.
- Defined the rule that folder ingest starts with inventory, then uses intentional batch grouping instead of blindly creating one wiki page per raw file.
- Updated `wiki/concepts/ingest.md` to reflect the same convention.

## [2026-04-29] query | explain folder inventory step

- Clarified that folder inventory is a lightweight structural pass before deep ingest.
- Expanded `wiki/concepts/ingest.md` with a checklist for deciding ingest scope and grouping.

## [2026-04-29] query | inventory Training Program CRD folder

- Inspected `raw/sources/Training Program CRD/` structurally without deep content reads.
- Counted 171 files across 31 subdirectories with the largest groups in `Resources/` and `Training Schudule CRD/`.
- Identified duplicate, recovered, and personalized checklist patterns that make full-folder ingest too noisy for a first pass.
- Created `wiki/queries/training-program-crd-folder-inventory.md` with staged ingest recommendations.
- Updated `wiki/index.md`.

## [2026-04-29] ingest | Training Program CRD staged batches 1-4

- Ingested the planned core curriculum batch, the `PM 101 Test/` module, selected canonical files from `Training Schudule CRD/`, and the first `Resources/` subfolders: `PM Basics/`, `Materials and Equipment/`, `Immersion Training/`, and `Pre Job Check Lists/`.
- Added a local extraction utility at `scripts/extract_text.py` to read `.docx`, `.pdf`, `.pptx`, `.xlsx`, and `.xlsm` sources without modifying the raw files.
- Created maintained source pages for the training modules, schedules, PM references, mission-support resources, materials/equipment references, and checklist sets.
- Created new concept pages for `Graydaze Training Program`, `Graydaze Project Manager Role`, `Estimate to Complete`, `Mission Support`, `Field Execution Basics`, and `Graydaze Operating Principles`.
- Created `wiki/entities/graydaze-contracting.md` and `wiki/syntheses/graydaze-training-program-synthesis.md`.
- Updated `wiki/index.md`, `wiki/overview.md`, and `wiki/syntheses/second-brain-operating-model.md` to reflect the first major domain branch.
- Preserved known tensions, including older versus newer ETC update cadence and duplicate packaging of some training assets across folders.

## [2026-04-29] maintenance | normalize internal systems

- Added entity pages for `GC Pay`, `Epicor`, `Ramp`, `QuickBooks`, `Salesforce`, and `Graydaze App`.
- Linked the new system pages into `Mission Support`, the Graydaze synthesis, and affected source pages.
- Updated `wiki/index.md` and `wiki/overview.md` to surface the normalized systems.

## [2026-04-29] ingest | Info Resources selective batch

- Ingested a selective batch from `raw/sources/Training Program CRD/Resources/Info/`.
- Created `wiki/sources/info-resources.md` from the durable process and operating-guidance documents in that folder.
- Deferred more situational handoff artifacts such as vacation/sabbatical turnover forms from deeper synthesis.

## [2026-04-29] lint | graydaze branch cleanup

- Corrected source metadata so concept and entity pages cite maintained source pages instead of derived concept pages where possible.
- Folded `Info Resources` insights into `Graydaze Project Manager Role`, `Field Execution Basics`, `Graydaze Operating Principles`, and `Graydaze Training Program Synthesis`.
- Noted that `Quizlets/` appears primarily duplicative and reinforcement-oriented relative to the higher-signal manuals and process docs already ingested.

## [2026-04-29] ingest | next deferred resource options

- Ingested a selective batch from `Resources/Dont know what that is, Heres Who to know/` as `wiki/sources/org-and-reference-resources.md`.
- Ingested a selective batch from `Resources/Templates/` as `wiki/sources/template-resources.md`.
- Ingested `Resources/Sales Est/Value Proposistion.pptx` as `wiki/sources/sales-est-resources.md`.
- Added `wiki/entities/cx-ex-fx-teams.md` to capture named support-team structure exposed by the org/reference material.
- Updated the PM role, mission-support, operating-principles, and Graydaze synthesis pages to incorporate the new batches.

## [2026-04-29] maintenance | teams bot packaging and deployment readiness

- Added a production-oriented `aiohttp` + Bot Framework Teams bot scaffold in `app.py` and `teams_bot/`.
- Added `teams_app/manifest.json` and `teams_app/README.md` so the bot can be packaged as a Teams app.
- Added support for either a local imported wiki query function or an HTTP-backed wiki query service, depending on where the existing backend runs.
- Confirmed Azure CLI access in the Graydaze tenant, but did not register or publish a live bot because no public HTTPS endpoint, bot app registration values, or Teams icon assets were available in the workspace.
