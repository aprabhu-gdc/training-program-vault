# 2026-05-01 Phase 08: Anthropic and Google Runtime Support

## Summary

This phase extended the provider-agnostic LLM configuration work into actual runtime support. The repository previously had a neutral `LLM_*` configuration contract, but the implementation still only executed OpenAI and Azure OpenAI requests. This phase added Anthropic and Google execution paths so the neutral configuration model became materially useful.

## Changes Implemented

- Extended `rag_backend/llm.py` to support Anthropic message generation via the Messages API
- Added Anthropic vision support using image content blocks
- Extended `rag_backend/llm.py` to support Google content generation via Gemini REST endpoints
- Added Google embedding support via Gemini embedding endpoints
- Fixed provider-specific client routing so chat, vision, and embeddings use their own configured provider paths
- Matched Google multimodal payload field names to the published Gemini REST schema
- Hardened structured JSON generation for ingest flows when using Anthropic or Google
- Updated `rag_backend/config.py` validation to reflect the new provider support matrix
- Updated `.env.example` notes so users can see which providers are currently implemented for chat/vision and embeddings
- Updated `README.md` to reflect the new runtime support boundary accurately

## Why These Changes Were Implemented

- A provider-agnostic configuration contract is only partially useful if the runtime still hard-stops at one or two vendors
- The stated project goal is to compare providers and models in practice, not just to reserve config names for them
- Anthropic and Google are common alternatives for chat and multimodal evaluation, so adding them materially improves the value of the project’s neutral configuration scheme

## Key Decisions

- Use direct HTTP calls for Anthropic and Google instead of adding more SDK dependencies
- Keep OpenAI and Azure OpenAI on the existing SDK path because those adapters were already working
- Support Google for both chat/vision and embeddings
- Support Anthropic for chat/vision but not embeddings, because the current backend does not implement an Anthropic embedding path

## Result

The repository can now switch among multiple providers using the same `LLM_*` environment contract, with a clearer distinction between what is configurable in theory and what is implemented in practice.
