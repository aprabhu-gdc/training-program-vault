---
title: Lint
type: concept
status: active
created: 2026-04-29
updated: 2026-04-29
source_count: 1
sources:
  - raw/sources/2026-04-29-llm-wiki-idea.md
---

# Current Synthesis

Lint is the maintenance workflow for keeping the wiki healthy as it grows. The agent checks for contradictions, stale claims, weak sourcing, missing pages for recurring ideas, orphan pages, and missing cross-references. Lint helps the wiki remain coherent instead of gradually decaying.

## Typical Checks

- contradictions between source pages and synthesis pages
- claims that newer sources weaken or supersede
- important recurring concepts that still lack their own page
- pages with too few inbound or outbound links
- missing source support for strong claims
- obvious research gaps worth filling next

## Notes For This Vault

- Prefer small corrective edits when safe.
- Surface higher-risk structural issues to the user.
- Record the lint pass in `wiki/log.md`.

## Related

- [[wiki/concepts/llm-wiki|LLM Wiki]]
- [[wiki/concepts/ingest|Ingest]]
- [[wiki/concepts/query|Query]]

## Open Questions

- How often should this vault run a deliberate lint pass once real content starts accumulating?

## Sources

- [[wiki/sources/llm-wiki-idea|LLM Wiki Idea]]
- [[raw/sources/2026-04-29-llm-wiki-idea|Raw source]]
