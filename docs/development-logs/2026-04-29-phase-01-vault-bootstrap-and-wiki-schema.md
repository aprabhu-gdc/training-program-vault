# 2026-04-29 Phase 01: Vault Bootstrap and Wiki Schema

## Summary

This phase established the repository as an LLM-maintained knowledge vault rather than a generic note folder. The goal was to create a durable structure where raw sources could remain immutable while the wiki layer accumulated synthesized knowledge over time.

## Changes Implemented

- Created `AGENTS.md` as the canonical operating contract for the vault
- Established the `raw/` and `wiki/` layer split
- Bootstrapped `wiki/index.md`, `wiki/log.md`, and `wiki/overview.md`
- Created the first maintained source page from the seed LLM wiki concept
- Added starter concept and synthesis pages to make the vault navigable

## Why These Changes Were Implemented

- The project needed a durable structure before any large ingest could happen
- The wiki had to be queryable and maintainable, not just a directory of files
- The schema in `AGENTS.md` created clear rules for ingestion, citations, metadata, linking, and maintenance
- The starter pages ensured future ingest work had a navigation spine instead of growing ad hoc

## Key Decisions

- Treat raw sources as immutable
- Store synthesis only in the `wiki/` layer
- Require YAML frontmatter on maintained wiki pages
- Keep `wiki/index.md` as the first-stop navigation page
- Keep `wiki/log.md` append-only for operational traceability

## Result

The repository became a structured vault with a clear ingestion and maintenance model, ready for Graydaze training-material ingestion.
