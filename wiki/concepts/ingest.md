---
title: Ingest
type: concept
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 1
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
---

# Current Synthesis

Ingest is the workflow that turns a raw source into maintained wiki knowledge. The agent reads the source, creates or updates a source summary page, revises affected concept or entity pages, updates top-level synthesis when needed, and records the operation in the index and log.

## Minimum Durable Outputs

- A captured source under `raw/sources/` if the source only existed in chat.
- A maintained source page under `wiki/sources/`.
- Updates to any affected concept, entity, or synthesis pages.
- An updated `wiki/index.md` if durable pages were created or materially changed.
- A new `wiki/log.md` entry.

## Notes For This Vault

- Default to one-source-at-a-time ingestion unless the user requests batching.
- Prefer small, high-signal updates over creating lots of thin pages.
- Create new concept or entity pages when they are likely to accumulate future information, not merely because the term appeared once.
- Entire folders under `raw/sources/` are allowed. Treat them as batch-ingest candidates: inventory first, then ingest the whole folder or a deliberate subset depending on coherence and volume.

## Folder Inventory Checklist

Inventorying a folder means doing a quick structural pass before deep ingestion. The goal is to identify the real source units and avoid treating every file as equally important.

- count files and subfolders
- identify file types and formats
- note meaningful structure such as project, date, source, or topic
- separate likely source units from attachments, exports, and support files
- flag duplicates, generated files, and low-signal material
- estimate whether the folder is coherent enough to ingest in one pass or should be split
- choose an ingest scope: all files, a subset, or a representative sample

Example: a folder with one transcript, one slide deck, and a few images may be one coherent ingest unit. A folder with hundreds of mixed exports may need grouping or sampling first.

## Related

- [[wiki/concepts/llm-wiki|LLM Wiki]]
- [[wiki/concepts/query|Query]]
- [[wiki/concepts/lint|Lint]]
- [[wiki/concepts/indexing-and-logging|Indexing and Logging]]

## Open Questions

- Which recurring page patterns will emerge once the vault covers real subject matter?
- When should batch ingest become the default instead of one-at-a-time ingest?

## Sources

- [[wiki/sources/llm-wiki-idea|LLM Wiki Idea]]
- [[raw/sources/2026-04-29-llm-wiki-idea|Raw source]]
