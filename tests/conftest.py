"""Shared test fixtures.

Design notes:
- `packages/wiki_core/settings.py` runs `load_dotenv()` at import time, so the
  real `.env` lands in `os.environ`. Offline tests therefore avoid
  `CoreSettings.from_env()` and instead build settings directly via
  `make_core_settings(...)` so they are deterministic and never touch real
  credentials or remote services.
- aiohttp apps are driven with `aiohttp.test_utils` under pytest-asyncio rather
  than pytest-aiohttp, to avoid event-loop plugin conflicts.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the repo root is importable (no src/ layout, no installed package).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from packages.wiki_core.settings import CoreSettings  # noqa: E402


def make_core_settings(base: Path, **overrides):
    """Build a fully-populated, deterministic CoreSettings rooted at `base`.

    Defaults supply a valid dummy OpenAI LLM config and a complete dummy
    SharePoint config (with site_id + drive_id pre-resolved so adapter helpers
    never hit the network). Override any field via keyword.
    """
    base = Path(base)
    defaults = dict(
        repo_root=base,
        wiki_root=base / "wiki",
        raw_sources_root=base / "raw" / "sources",
        vector_db_path=base / "lancedb",
        vector_table_name="test-vault-wiki",
        vector_manifest_path=base / "index-manifest.json",
        source_sync_state_path=base / "source-sync-state.json",
        sync_job_state_path=base / "sync-job-state.json",
        rag_top_k=6,
        rag_index_summary_chars=5000,
        max_source_chars=18000,
        llm_provider="openai",
        llm_chat_provider="",
        llm_chat_model="gpt-4o-test",
        llm_vision_provider="",
        llm_vision_model="",
        llm_embedding_provider="",
        llm_embedding_model="text-embedding-3-small-test",
        llm_openai_api_key="sk-test-key",
        llm_openai_base_url="",
        llm_azure_openai_endpoint="",
        llm_azure_openai_api_key="",
        llm_azure_openai_api_version="2024-02-01",
        sharepoint_tenant_id="tenant-test",
        sharepoint_client_id="client-test",
        sharepoint_client_secret="secret-test",
        sharepoint_site_id="site-test-id",
        sharepoint_site_hostname="",
        sharepoint_site_path="",
        sharepoint_list_id="",
        sharepoint_drive_id="drive-test-id",
        sharepoint_drive_name="Training Program Vault",
        sharepoint_raw_root_path="raw/sources",
        sharepoint_wiki_root_path="wiki",
        sharepoint_request_timeout_seconds=60.0,
        sharepoint_webhook_notification_url="",
        sharepoint_webhook_client_state="",
    )
    defaults.update(overrides)
    settings = CoreSettings(**defaults)
    settings.wiki_root.mkdir(parents=True, exist_ok=True)
    return settings


@pytest.fixture
def core_settings(tmp_path):
    """A deterministic, offline CoreSettings rooted at a tmp dir."""
    return make_core_settings(tmp_path)


@pytest.fixture
def wiki_dir(tmp_path):
    """A tmp repo root with a wiki/ tree; returns (repo_root, wiki_root)."""
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    return tmp_path, wiki


@pytest.fixture
def live_settings():
    """Real env-backed settings for live e2e. Skips if LLM creds are absent."""
    settings = CoreSettings.from_env()
    try:
        settings.validate_llm()
    except ValueError as exc:
        pytest.skip(f"Live LLM credentials not configured: {exc}")
    return settings
