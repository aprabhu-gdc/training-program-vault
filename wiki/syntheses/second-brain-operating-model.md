---
title: Second Brain Operating Model
type: synthesis
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 2
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
  - wiki/syntheses/graydaze-training-program-synthesis.md
---

# Thesis

This vault will operate as a wiki-first second brain. The user curates sources and directs attention. The agent compiles source material into a persistent markdown graph, maintains cross-references, and keeps the knowledge base current as new sources and questions arrive.

## Roles

- User responsibilities: choose what to ingest, decide what matters, ask questions, and steer emphasis.
- Agent responsibilities: summarize, cross-link, update affected pages, track tensions, maintain the index, and append to the log.

## Local Implementation Decisions

- Raw sources live in `raw/sources/`.
- Local attachments belong in `raw/assets/` when used.
- The maintained wiki lives in `wiki/`.
- The canonical navigation files are `wiki/index.md` and `wiki/log.md`.
- The schema file is `AGENTS.md` at the vault root.
- Durable answers should usually be written into `wiki/queries/` or `wiki/syntheses/`.
- Markdown is the default output format unless the user asks for another artifact type.

## Why This Should Compound

- Cross-links are preserved instead of re-created ad hoc.
- Contradictions can be surfaced in the relevant pages instead of lost in chat history.
- Query outputs can become new inputs to future thinking.
- The maintenance burden stays low because the agent, not the user, performs the bookkeeping.

## Near-Term Next Steps

- Continue expanding the Graydaze training branch through deferred resource subfolders and later source drops.
- Let a few branches of the vault emerge naturally before adding more structure.
- Revisit metadata and search tooling only when the current setup becomes limiting.

## Related

- [[wiki/overview|Overview]]
- [[wiki/concepts/llm-wiki|LLM Wiki]]
- [[wiki/concepts/ingest|Ingest]]
- [[wiki/concepts/query|Query]]
- [[wiki/concepts/indexing-and-logging|Indexing and Logging]]

## Sources

- [[wiki/sources/llm-wiki-idea|LLM Wiki Idea]]
- [[wiki/syntheses/graydaze-training-program-synthesis|Graydaze Training Program Synthesis]]
- [[raw/sources/2026-04-29-llm-wiki-idea|Raw source]]
