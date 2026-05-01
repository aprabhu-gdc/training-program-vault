---
title: Indexing and Logging
type: concept
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 1
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
---

# Current Synthesis

`wiki/index.md` and `wiki/log.md` solve different problems. The index is content-oriented and helps the agent find relevant pages quickly. The log is chronological and helps both the user and the agent understand what changed recently. Together they provide navigation and memory without requiring heavier retrieval infrastructure at small to medium scale.

## Distinction

- `wiki/index.md` is a catalog of durable pages grouped by category with one-line descriptions.
- `wiki/log.md` is an append-only timeline of ingests, queries, lint passes, and maintenance operations.

## Notes For This Vault

- The agent should read `wiki/index.md` first during most queries.
- The agent should append to `wiki/log.md` after any durable operation.
- If the vault grows beyond what the index handles comfortably, add search tooling without replacing the index's role as human-readable navigation.

## Related

- [[wiki/concepts/ingest|Ingest]]
- [[wiki/concepts/query|Query]]
- [[wiki/concepts/lint|Lint]]
- [[wiki/overview|Overview]]

## Open Questions

- When will this vault outgrow index-first navigation and need dedicated search support?

## Sources

- [[wiki/sources/llm-wiki-idea|LLM Wiki Idea]]
- [[raw/sources/2026-04-29-llm-wiki-idea|Raw source]]
