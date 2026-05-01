# 2026-05-01 Phase 06: Multimodal Teams Chat Input

## Summary

This phase extended the Teams bot from text-only querying to multimodal message handling. Users can now attach supported documents or images directly in chat, and the bot will preprocess that input before running the normal retrieval flow against the wiki.

## Changes Implemented

- Updated `teams_app/manifest.json` to set `supportsFiles` to `true`
- Extended the Teams bot request model to carry structured attachment payloads
- Added attachment download and preprocessing in `teams_bot/bot.py`
- Reused `scripts/extract_text.py` for supported document formats
- Passed document text as user context into the RAG pipeline
- Passed image attachments as `image_url` content to the chat model when available
- Updated retrieval query construction so attachment text helps retrieval instead of only influencing final generation
- Explicitly prevented attachment-only context from being treated as a citeable wiki source

## Why These Changes Were Implemented

- Teams users often ask questions in the context of a document or screenshot rather than a pure text prompt
- Supporting inline files reduces workflow friction and makes the bot more useful during real PM work
- Document text can materially improve retrieval quality when the user’s question is short or underspecified
- Image support enables workflows like interpreting screenshots or visual training artifacts while still grounding the answer in the wiki

## Key Decisions

- Keep retrieval grounded in `wiki/` even when attachment content is present
- Treat attachments as user-supplied context, not as new knowledge or citeable sources
- Do not assume a specific chat model such as GPT-4o in configuration defaults
- Allow separate optional vision-model settings while falling back to the configured chat model when appropriate

## Result

The Teams bot now supports richer user inputs without bypassing the maintained wiki retrieval pipeline, making it more practical for day-to-day training and operational assistance.
