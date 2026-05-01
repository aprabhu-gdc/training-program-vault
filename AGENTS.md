# LLM Wiki Agent Schema

## Role
This vault is a persistent, LLM-maintained second brain.

The agent's job is to compile knowledge into a durable wiki that improves over time. Raw sources are the source of truth. The wiki is the maintained synthesis layer. This file is the operating contract that governs how the agent works inside the vault.

## Instruction Precedence
1. Direct user instructions in the current conversation.
2. This `AGENTS.md` schema.
3. Existing wiki conventions already present in the vault.

If the user explicitly asks to break a convention for a good reason, do it and document the deviation.

## Core Model
There are three layers:

1. Raw sources
`raw/sources/` contains immutable source material. Articles, notes, transcripts, PDFs converted to markdown, images, and other assets live here or under `raw/assets/`. The agent reads from this layer but does not rewrite source content.

2. Wiki
`wiki/` contains the maintained markdown knowledge base. This is where summaries, concept pages, entity pages, syntheses, and durable query outputs live. The agent owns this layer and is expected to create and update files here.

3. Schema
`AGENTS.md` defines folder structure, page conventions, and workflows so the agent behaves like a disciplined wiki maintainer rather than a generic chatbot.

## Mission
The wiki should become more useful after every ingest and every durable question.

The key behavior is accumulation:
- do not rediscover the same knowledge from scratch every time
- preserve synthesis in pages
- maintain cross-links between related pages
- surface contradictions instead of hiding them
- keep the wiki coherent as new material arrives

## Vault Structure
Canonical layout:

- `AGENTS.md` - schema and operating rules
- `raw/sources/` - immutable source files
- `raw/assets/` - local images and attachments referenced by sources
- `wiki/overview.md` - top-level orientation and current working thesis
- `wiki/index.md` - content-oriented catalog of wiki pages
- `wiki/log.md` - append-only chronological record of operations
- `wiki/sources/` - one maintained page per ingested source
- `wiki/concepts/` - topic and concept pages
- `wiki/entities/` - named people, companies, tools, places, projects, etc.
- `wiki/syntheses/` - higher-level analyses, theses, and operating models
- `wiki/queries/` - durable answers, comparisons, memos, and generated artifacts worth keeping

If a folder does not exist yet, create it when the first file for that category is needed.

## Naming Conventions
- Use lowercase kebab-case filenames.
- Prefer stable names. Rename only when it materially improves clarity.
- Use one durable concept or entity per file.
- Prefer updating an existing page over creating a near-duplicate.
- When a new source is captured from chat, use `raw/sources/YYYY-MM-DD-short-title.md`.
- When the user drops in an existing file, keep the original raw filename unless there is a clear reason not to.

## Linking Conventions
- Use Obsidian wikilinks for internal references.
- Prefer explicit vault-relative links when there is any ambiguity, for example `[[wiki/concepts/ingest|Ingest]]`.
- Every maintained page should link to adjacent concepts, relevant sources, or both.
- Orphan pages are a lint problem unless they are intentionally isolated scratch material.

## Metadata Conventions
All maintained wiki pages should start with YAML frontmatter.

Required fields for wiki pages:
- `title`
- `type` - one of `overview`, `index`, `log`, `source`, `concept`, `entity`, `synthesis`, `query`
- `status` - usually `active`, `seed`, `draft`, or `superseded`
- `created`
- `updated`
- `source_count`
- `sources` - list of raw source file paths and, when useful, source pages

Raw source files may use lighter metadata such as:
- `title`
- `source_type`
- `captured`
- `status: immutable`

## Content Standards
- Summaries should be dense, specific, and factual.
- Avoid generic filler and motivational phrasing.
- Distinguish observation from inference.
- Preserve uncertainty explicitly.
- If sources conflict, do not flatten the disagreement. Record the tension and cite both sides.
- When a page becomes too broad, split it only if the split produces cleaner long-term navigation.

## Citation Rules
- Any nontrivial claim should be traceable to a source.
- Source-backed pages must include a `## Sources` section.
- Prefer citing the maintained source page and linking the raw source beneath it.
- If a statement is an inference, label it as an inference.
- Never fabricate citations.

## Index Rules
`wiki/index.md` is the navigation file the agent should read first when answering questions from the wiki.

Format:
- Organize by section such as `Schema`, `Overview`, `Sources`, `Concepts`, `Entities`, `Syntheses`, and `Queries`.
- Each entry should be one line in the form `- [[path|Title]] - one-line description`.
- Keep section contents alphabetized when practical.

Update `wiki/index.md` when:
- a new durable page is created
- a page is renamed
- a page's purpose changes materially
- a page is superseded

## Log Rules
`wiki/log.md` is the chronological record of what happened and when.

Format each entry like this:

`## [YYYY-MM-DD] operation | title`

Then add short bullets such as:
- raw source involved
- created pages
- updated pages
- notable tensions or follow-ups

Rules:
- append only
- do not rewrite historical substance except for typo or link fixes
- keep entries terse and parseable
- use operation labels such as `setup`, `ingest`, `query`, `lint`, `rename`, `merge`, or `review`

## Workflow Classification
Every interaction should be treated as one of these modes unless the user says otherwise:

1. Ingest
The user provided a new source or asked the agent to process one.

2. Query
The user asked a question, requested a comparison, or asked for synthesis.

3. Lint
The user asked for a health check, audit, cleanup, or review of the wiki.

4. Maintenance
The user asked for structural edits such as renaming, reorganizing, or changing conventions.

## Ingest Workflow
Use this whenever a new source arrives.

1. Capture the source in `raw/sources/` if it only exists in chat.
2. Read the source and identify its main claims, concepts, entities, and relationships.
3. Search the existing wiki for pages that should be updated instead of duplicated.
4. Create or update `wiki/sources/<slug>.md` with:
   - summary
   - key takeaways
   - notable claims or evidence
   - connections to existing pages
   - open questions or tensions
5. Update affected pages in `wiki/concepts/`, `wiki/entities/`, and `wiki/syntheses/`.
6. Update `wiki/overview.md` if the source changes the top-level picture.
7. Update `wiki/index.md` for any durable page additions or purpose changes.
8. Append an `ingest` entry to `wiki/log.md`.
9. Respond with what changed, what was created, what was updated, and any unresolved questions.

Default ingest behavior:
- prefer one-source-at-a-time, high-attention ingestion unless the user asks for batching
- make the smallest set of correct page updates that preserves long-term usefulness
- do not create pages for every noun; create pages where ongoing accumulation value is likely

## Query Workflow
Use this whenever the user asks a question.

1. Read `wiki/index.md` first.
2. Read only the relevant wiki pages needed to answer.
3. If the wiki is insufficient, read the minimum necessary raw sources.
4. Answer with citations to maintained source pages and, when useful, raw sources.
5. If the answer is durable and likely to matter later, write it back into `wiki/queries/` or `wiki/syntheses/`.
6. If a durable page was created or updated, also update `wiki/index.md` and `wiki/log.md`.

Default query behavior:
- prefer answering from the wiki before going back to raw documents
- turn reusable answers into durable pages
- keep ephemeral answers ephemeral unless they clearly deserve preservation

## Lint Workflow
Use this for periodic health checks.

Check for:
- contradictions between pages
- stale claims that newer sources weaken or supersede
- orphan pages with no meaningful inbound links
- repeated concepts that should have their own page
- pages with weak sourcing
- missing cross-references
- obvious source gaps worth researching

Then:
- make small corrective edits when safe
- surface higher-risk issues to the user
- append a `lint` entry to `wiki/log.md`

## Maintenance Workflow
Use this for renames, mergers, folder changes, or convention changes.

1. Identify all affected links and index entries.
2. Make the smallest structural change that resolves the issue.
3. Update `wiki/index.md`, `wiki/log.md`, and any affected pages.
4. If the change alters conventions, update this `AGENTS.md` file.

## Durable Output Rules
Write query results back into the wiki when they are any of the following:
- a comparison likely to be referenced later
- a new synthesis or thesis update
- a decision memo
- a reusable process or checklist
- a generated artifact the user is likely to revisit

Do not file routine small talk or one-off answers that have no future reuse value.

## Contradiction Handling
When new information conflicts with old information:
- do not silently overwrite the old claim
- update the affected synthesis page
- add a `## Tensions`, `## Contradictions`, or `## Open Questions` section where appropriate
- cite both the prior and new evidence
- note the conflict in the source page if the conflict is source-specific

## Source Handling Rules
- Raw sources are immutable after capture.
- The agent may fix obvious metadata mistakes only if the user asks.
- If a source references images or attachments, inspect relevant files under `raw/assets/` when needed for understanding.
- If a raw source is non-markdown or binary, create a maintained source page in `wiki/sources/` that records what was inspected and what remains unknown.

## Folder Ingest Rules
- The user may add entire folders under `raw/sources/` for ingestion.
- Treat a folder drop as a batch ingest candidate, not as an instruction to create one wiki page per file automatically.
- First inventory the folder contents, identify file types and likely source units, and propose or apply the smallest sensible ingest grouping.
- Prefer preserving the raw folder structure when it carries meaning such as project, source, date, or topic.
- If a folder contains many low-signal files, duplicates, exports, or mixed formats, summarize the inventory and ask whether to ingest all files, a subset, or a representative sample.
- For small coherent folders, the agent may ingest the whole folder in one pass if that is clearly useful.
- Record folder-level ingest decisions in `wiki/log.md`, including which files or subfolders were included, skipped, or deferred.

## Review Mode
If the user asks for a review, prioritize findings:
- bugs or logical errors in the wiki
- unsupported claims
- missing links or stale conclusions
- testing or verification gaps in generated artifacts

List findings first with file references when possible. Keep summaries secondary.

## Page Templates

### Source Page Template
```md
---
title: <Title>
type: source
status: active
created: YYYY-MM-DD
updated: YYYY-MM-DD
source_count: 1
sources:
  - raw/sources/<file>.md
---

# Summary

## Key Takeaways

## Connections

## Open Questions

## Sources
- [[raw/sources/<file>]]
```

### Concept Page Template
```md
---
title: <Title>
type: concept
status: active
created: YYYY-MM-DD
updated: YYYY-MM-DD
source_count: <n>
sources:
  - raw/sources/<file>.md
---

# Current Synthesis

## Related

## Open Questions

## Sources
- [[wiki/sources/<source-page>|<Source Title>]]
```

### Query Page Template
```md
---
title: <Title>
type: query
status: active
created: YYYY-MM-DD
updated: YYYY-MM-DD
source_count: <n>
sources:
  - raw/sources/<file>.md
---

# Question

# Answer

## Implications

## Sources
```

## Bootstrapping Rule
If the vault is empty or nearly empty, initialize it with:
- `AGENTS.md`
- `wiki/overview.md`
- `wiki/index.md`
- `wiki/log.md`
- the first captured source page and the minimum concept pages needed to make the wiki navigable

## Interaction Contract
From this point onward:
- treat pasted documents as ingest candidates by default
- treat questions as wiki-first queries
- keep `wiki/index.md` and `wiki/log.md` current after durable changes
- explain what changed after each nontrivial operation
- prefer small, coherent edits over sprawling rewrites

This schema is expected to evolve as the vault matures. When conventions change, update this file and record the change in `wiki/log.md`.
