# 2026-05-01 Phase 04: Local RAG Backend and Egnyte Sync

## Summary

This phase implemented the actual retrieval backend and the automated Egnyte synchronization pipeline. The objective was to make the Teams bot self-sufficient: retrieve only from `wiki/`, maintain a local embedded vector index, and refresh the wiki and vector store when Egnyte training files change.

## Changes Implemented

- Added `rag_backend/indexer.py` for wiki chunking and vector indexing
- Added `rag_backend/query.py` for retrieval and answer generation
- Added `rag_backend/markdown.py` to parse frontmatter and split markdown by `##` headings
- Added `rag_backend/llm.py` to wrap OpenAI and Azure OpenAI calls
- Added `rag_backend/config.py` for backend settings
- Added `rag_backend/egnyte_client.py` for Egnyte listing and file downloads
- Added `rag_backend/auto_ingest.py` to orchestrate raw-file download, LLM-based synthesis, wiki updates, log updates, and vector upserts
- Updated `app.py` with `POST /api/webhooks/egnyte`
- Updated the Teams bot `/sync` command to trigger manual ingest and reindex
- Updated `.env.example` and runtime config to default the bot query callable to `rag_backend.query:query_vault`

## Why These Changes Were Implemented

- The existing Teams bot scaffold lacked the actual retrieval system needed to answer user questions
- The project requirement was explicit that retrieval must query only the maintained wiki layer, not raw sources
- Egnyte was the operational source of new/changed training files, so the sync pipeline needed to update the wiki and reindex automatically
- The retrieval path needed a local embedded database, not an external vector service

## Key Decisions

- Use markdown-aware chunking on `##` sections to preserve section semantics
- Attach `title`, `type`, and `sources` metadata to vector rows from page frontmatter
- Summarize `wiki/index.md` into the system prompt so the answer model sees the vault map
- Require strict `[Source: Title]` citations for wiki-grounded claims
- Keep auto-ingest aligned to `AGENTS.md` instead of inventing a separate ingest contract

## Implementation Adjustment

The first vector-store implementation used ChromaDB, which matched the allowed architecture. That was later replaced with LanceDB because ChromaDB required a native `hnswlib` build that failed in the current Windows/Python environment without Visual C++ build tools.

## Result

The repository gained a functioning local RAG backend and an Egnyte-driven refresh workflow, making the Teams bot capable of answering from the maintained wiki instead of being only a UI scaffold.
