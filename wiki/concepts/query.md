---
title: Query
type: concept
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 1
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
---

# Current Synthesis

Query is the workflow for answering from the wiki first. The agent should read `wiki/index.md`, locate relevant pages, and synthesize an answer from the maintained knowledge base before returning to raw sources. When a query produces reusable insight, that output should be written back into the wiki as a durable page.

## Durable Query Outputs

- comparisons
- analyses
- connection notes
- decision memos
- slide decks, canvases, or other generated artifacts when requested

## Notes For This Vault

- Markdown pages are the default durable output format unless the user asks for something else.
- Query answers should cite maintained source pages and raw sources when needed.
- If the answer changes the vault's top-level understanding, update a synthesis page rather than leaving the insight only in chat.

## Related

- [[wiki/concepts/llm-wiki|LLM Wiki]]
- [[wiki/concepts/ingest|Ingest]]
- [[wiki/concepts/lint|Lint]]
- [[wiki/syntheses/second-brain-operating-model|Second Brain Operating Model]]

## Open Questions

- Which kinds of questions should always be promoted into durable pages for this vault?

## Sources

- [[wiki/sources/llm-wiki-idea|LLM Wiki Idea]]
- [[raw/sources/2026-04-29-llm-wiki-idea|Raw source]]
