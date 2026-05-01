---
title: Training Program CRD Folder Inventory
type: query
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 171
sources:
  - raw/sources/Training Program CRD/
---

# Question

What is inside `raw/sources/Training Program CRD/`, and how should it be grouped for ingest?

# Answer

## Observed Structure

- The folder contains 171 files across 31 subdirectories.
- The top level contains 12 folders and 4 standalone files.
- File type mix is dominated by office documents: 87 `.docx`, 39 `.pdf`, 26 `.xlsx`, 8 `.pptx`, 5 `.doc`, and a small number of `.xls`, `.xlsm`, `.msg`, image, and video files.
- The largest top-level groups are `Resources/` with 72 files and `Training Schudule CRD/` with 56 files.
- Other coherent-looking groups include `ETC Training/` with 14 files, `PM 101 Test/` with 12 files, and `PM 101 CRD/` with 6 files.
- Single-file or near-single-file topic folders include `Epicor Training/`, `Helpful Travel info/`, `Job Site Glossary/`, `Paint Basics Training CRD/`, `RAMP/`, and `Repaint Project flowchart/`.
- `GC Pay Training/` currently appears empty.

## Notable Noise And Duplicate Patterns

- Observation: `Training Schudule CRD/` is heavy on blank schedules, weekly checklist templates, and person-specific checklist copies.
- Observation: `Resources/` is heterogeneous. It mixes PM basics, materials and equipment notes, templates, quizlets, pre-job checklists, immersion training, and miscellaneous reference files.
- Observation: there are clear duplicate or near-duplicate filenames across folders, including `PM 101 CRD Revised 10-23-24.pdf`, `Vertical joints PM's check List.pdf`, `Job Start Up Checklist.pdf`, and `Phase-2-Project-Manager-General-Manager-Process.pdf`.
- Observation: there are draft or generated variants such as `AutoRecovered` spreadsheets, `Copy of` files, and repeated blank checklist files.
- Observation: there are a few non-document assets such as `MOV_6782.mov`, `graydaze.jpg`, and `Graydaze_color_transparent - Copy.png`.
- Inference: this is a training corpus with several real curriculum modules plus a substantial amount of supporting operational material and duplicate packaging.

## Recommended Ingest Grouping

- Inference: do not ingest the entire folder as a single unit.
- Inference: the best first pass is staged module ingestion.
- Inference: Batch 1 should focus on the most coherent core curriculum groups: `PM 101 CRD/`, `ETC Training/`, `Paint Basics Training CRD/`, `Epicor Training/`, `Helpful Travel info/`, `RAMP/`, `Repaint Project flowchart/`, `Job Site Glossary/`, `Customer 101- CRD.docx`, and the Graydaze PM roles documents.
- Inference: `PM 101 Test/` is coherent enough to ingest as its own module after the core PM 101 materials.
- Inference: `Training Schudule CRD/` should be ingested selectively, favoring canonical blank schedules and generic checklists while deferring personalized weekly checklist copies and recovered variants.
- Inference: `Resources/` should be treated as a later curated batch by subfolder, not as one source set.
- Inference: likely first `Resources/` candidates are `PM Basics/`, `Materials and Equipment/`, `Immersion Training/`, and `Pre Job Check Lists/`.
- Inference: likely deferred items are blank templates, quizlet exports, duplicate PDFs already present elsewhere, person-specific weekly checklists, recovered spreadsheet variants, and the standalone video unless specifically needed.

## Implications

- This folder has enough structure to support multi-batch ingest without manual reorganization of raw files.
- The real source units appear to be training modules and resource clusters, not individual files and not the entire folder as one page.
- A staged ingest will reduce noise and avoid polluting the wiki with duplicate checklists and support artifacts.
- The next useful step is to choose the first batch and ingest it deeply.

## Sources

- Structural inventory of `raw/sources/Training Program CRD/`.
- Representative repeated files include `raw/sources/Training Program CRD/PM 101 CRD/PM 101 CRD Revised 10-23-24.pdf` and `raw/sources/Training Program CRD/Training Schudule CRD/-PM/Resources/PM 101 CRD Revised 10-23-24.pdf`.
- Representative noisy variants include `raw/sources/Training Program CRD/Training Schudule CRD/-ESTIMATING/ESTIMATING Training Schedule- BLANK(AutoRecovered).xlsx` and `raw/sources/Training Program CRD/Resources/Templates/Copy of WEEKLY RATE COST TOOL.xlsx`.
