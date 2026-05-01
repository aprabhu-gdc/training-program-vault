---
title: LLM Wiki Idea
type: source
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 1
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
---

# Summary

This source proposes an LLM-maintained wiki as a persistent layer between raw documents and future questions. Instead of re-deriving answers from raw text every time, the agent incrementally compiles knowledge into linked markdown pages and keeps them current as new sources arrive.

## Key Takeaways

- The main distinction from standard RAG is persistence: synthesis, links, and contradictions are stored in the wiki instead of being recomputed from scratch on every query.
- The system has three layers: immutable raw sources, an editable wiki maintained by the LLM, and a schema file that enforces conventions and workflows.
- The recurring operations are ingest, query, and lint.
- `index.md` and `log.md` are core navigation artifacts, not optional bookkeeping.
- Obsidian works well as the browsing environment because the wiki is just markdown files with links and graph structure.

## Local Implications

- This vault should prefer wiki-first answers over raw-source-first answers.
- Each new source should produce a maintained source page and any required concept or synthesis updates.
- Durable answers should be written back into the wiki when they are likely to matter later.
- Contradictions should be recorded explicitly rather than silently overwritten.

## Connections

- [[wiki/concepts/llm-wiki|LLM Wiki]]
- [[wiki/concepts/ingest|Ingest]]
- [[wiki/concepts/query|Query]]
- [[wiki/concepts/lint|Lint]]
- [[wiki/concepts/indexing-and-logging|Indexing and Logging]]
- [[wiki/syntheses/second-brain-operating-model|Second Brain Operating Model]]

## Open Questions

- Which personal or professional domains should be ingested first so the vault becomes useful quickly?
- When the wiki grows, should we add search tooling such as `qmd`, or keep relying on `wiki/index.md` longer?
- Do we want richer metadata for Dataview-backed dashboards later, or keep page frontmatter minimal?

## Sources

- [[raw/sources/2026-04-29-llm-wiki-idea|LLM Wiki raw source]]
