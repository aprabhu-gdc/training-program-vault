# 2026-04-29 Phase 03: Teams Bot Scaffold and Packaging

## Summary

This phase introduced the first production-oriented Teams bot scaffold so the vault could be queried from Microsoft Teams. The initial focus was bot runtime structure, stable request handling, welcome UX, feedback collection, and app package readiness rather than implementing retrieval logic directly inside the bot.

## Changes Implemented

- Added `app.py` as the `aiohttp` HTTP entrypoint
- Added the `teams_bot/` package for bot logic, configuration, and query-service adaptation
- Added welcome messaging and typing indicators
- Added Adaptive Card feedback buttons and logging
- Added `teams_app/manifest.json`
- Added `teams_app/README.md` with packaging instructions
- Added configuration support for local callable or HTTP-backed query integration

## Why These Changes Were Implemented

- The user needed a Teams-native interface for junior PMs
- The bot had to stay decoupled from the future backend implementation so the retrieval stack could evolve independently
- Teams users needed immediate feedback and a simple initial experience because query latency was expected to be several seconds
- Feedback collection created an operational loop for answer quality improvement

## Key Decisions

- Use `aiohttp` and Bot Framework SDK rather than a heavier framework
- Keep `/api/messages` as the canonical bot endpoint
- Use a query-adapter service instead of hard-coding retrieval logic into the bot
- Package a Teams manifest early even though live deployment inputs were not yet available

## Result

The repository gained a functioning Teams bot scaffold and the packaging structure required for future publish/install work.
