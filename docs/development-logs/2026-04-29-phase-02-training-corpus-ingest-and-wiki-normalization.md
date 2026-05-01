# 2026-04-29 Phase 02: Training Corpus Ingest and Wiki Normalization

## Summary

This phase converted the Graydaze training folder from a raw source drop into a maintained wiki branch. The work focused on staged ingestion, selective extraction of high-signal content, and normalization of recurring systems and concepts so the knowledge base would remain coherent over time.

## Changes Implemented

- Inventoried `raw/sources/Training Program CRD/` before deep ingest
- Added folder-ingest behavior to `AGENTS.md`
- Created `wiki/queries/training-program-crd-folder-inventory.md`
- Ingested multiple Graydaze PM training source groups into `wiki/sources/`
- Created and updated concept pages covering PM role, ETC, mission support, field execution, operating principles, and the training program
- Created and normalized entity pages such as `GC Pay`, `Epicor`, `Ramp`, `QuickBooks`, `Salesforce`, and `Graydaze App`
- Added `scripts/extract_text.py` to support Office and PDF extraction without modifying raw files
- Updated synthesis, overview, index, and log pages to reflect the growing training branch

## Why These Changes Were Implemented

- The raw corpus contained duplicates, templates, mixed formats, and uneven signal quality
- An inventory-first workflow reduced noise and avoided brittle one-file-per-page ingest behavior
- Recurring systems and concepts needed dedicated entity/concept pages so future sources could accumulate into the same nodes instead of fragmenting knowledge
- Extraction support for Office and PDF formats was necessary because many training materials were not plain markdown

## Key Decisions

- Ingest the corpus in staged batches rather than one monolithic pass
- Prefer durable source pages and a smaller number of high-value concepts/entities over exhaustive page creation
- Preserve tensions explicitly rather than silently harmonizing contradictory source guidance
- Keep ingestion aligned to the wiki schema rather than creating an alternate reporting structure

## Result

The repository gained a substantial Graydaze PM training knowledge base under `wiki/`, including normalized concepts and entities that future RAG and Teams workflows could use.
