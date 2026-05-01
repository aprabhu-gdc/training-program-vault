# 2026-05-01 Phase 05: Vector Store Hardening and Runtime Portability

## Summary

After the initial RAG implementation, runtime validation exposed environment-specific problems around vector-store installation and filesystem behavior. This phase hardened the backend so it would run on the current Windows/Egnyte setup instead of only looking correct in code review.

## Changes Implemented

- Replaced ChromaDB with LanceDB
- Moved default vector DB and sync-state paths to local app data rather than the repository UNC path
- Added manifest-based incremental upsert tracking for wiki pages
- Verified LanceDB behavior on a local filesystem path and corrected the `list_tables()` handling
- Preserved the embedded/local vector DB requirement while removing the native-build blocker encountered with ChromaDB

## Why These Changes Were Implemented

- ChromaDB failed to install because `chroma-hnswlib` required local MSVC build tooling
- Embedded databases are more stable on a normal local filesystem path than on the Egnyte UNC workspace path
- The backend needed to be actually runnable in the target environment, not merely architecturally valid in theory

## Key Decisions

- Prefer a working embedded database over preserving the original Chroma implementation
- Keep the external interface of the backend unchanged where possible so bot integration remained stable
- Use local app data defaults for runtime state while leaving environment overrides available

## Result

The vector backend became portable and verifiably runnable in the current environment, which materially reduced deployment risk.
