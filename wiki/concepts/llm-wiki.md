---
title: LLM Wiki
type: concept
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 1
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
---

# Current Synthesis

An LLM Wiki is a persistent, interlinked markdown knowledge base maintained by an LLM. It sits between raw source material and future answers. The central advantage is accumulation: synthesis, cross-links, and contradictions are stored once and then kept current instead of being recomputed from raw text on every query.

## Distinguishing Properties

- The wiki is a compiled artifact, not just a retrieval layer.
- The agent updates the wiki incrementally as new sources and questions arrive.
- Useful query outputs can become new durable pages instead of disappearing into chat history.
- Cross-references and tensions are part of the maintained state of the system.

## Related

- [[wiki/concepts/ingest|Ingest]]
- [[wiki/concepts/query|Query]]
- [[wiki/concepts/lint|Lint]]
- [[wiki/concepts/indexing-and-logging|Indexing and Logging]]
- [[wiki/syntheses/second-brain-operating-model|Second Brain Operating Model]]

## Open Questions

- How much page structure will this specific vault need as it grows into real domains?
- At what scale should search move beyond `wiki/index.md`?

## Sources

- [[wiki/sources/llm-wiki-idea|LLM Wiki Idea]]
- [[raw/sources/2026-04-29-llm-wiki-idea|Raw source]]
