# 2026-05-01 Phase 07: Provider-Agnostic LLM Configuration

## Summary

This phase replaced the previously provider-specific environment-variable scheme with a provider-agnostic `LLM_*` contract. The goal was to let the project express chat, vision, and embedding routing independently without locking the repository’s configuration model to OpenAI naming.

## Changes Implemented

- Replaced provider-specific model routing keys in `.env.example` with generic routing keys:
  - `LLM_PROVIDER`
  - `LLM_CHAT_PROVIDER`
  - `LLM_CHAT_MODEL`
  - `LLM_VISION_PROVIDER`
  - `LLM_VISION_MODEL`
  - `LLM_EMBEDDING_PROVIDER`
  - `LLM_EMBEDDING_MODEL`
- Added provider-specific credential blocks under neutral names:
  - `LLM_OPENAI_*`
  - `LLM_AZURE_OPENAI_*`
  - `LLM_ANTHROPIC_*`
  - `LLM_GOOGLE_*`
- Refactored `rag_backend/config.py` to parse and validate the new generic contract
- Updated `rag_backend/llm.py` to use provider/model routing properties instead of hard-coded OpenAI env names
- Updated backend callers to use `validate_llm()` rather than the older `validate_openai()` method name
- Updated `README.md` so the model-configuration section reflects the new env contract and the currently implemented provider boundary

## Why These Changes Were Implemented

- The previous env naming implied that OpenAI-specific keys were the canonical configuration path, which made future experimentation with Anthropic, Google, or mixed-provider setups look unnatural
- The project needed a stable config contract that would not require another repository-wide rename if the chosen provider changes later
- Chat, vision, and embedding workloads may reasonably diverge over time, so the configuration needed to support separate routing keys for those workloads

## Key Decisions

- Separate the configuration contract from the current implementation surface
- Keep the current runtime adapters limited to the already implemented providers rather than pretending Anthropic or Google support exists today
- Preserve legacy provider-specific keys as fallbacks inside config parsing for compatibility with existing local setups while steering the docs and `.env.example` toward the new `LLM_*` scheme

## Current Runtime Boundary

The configuration scheme is now provider-agnostic, but the runtime implementation currently supports only:

- `openai`
- `azure-openai`

`anthropic` and `google` can now be represented in configuration cleanly, but they are not yet implemented in `rag_backend/llm.py`.

## Result

The repository’s environment contract is now future-proofed for provider experimentation and mixed workload routing without locking the project’s configuration vocabulary to one vendor.
