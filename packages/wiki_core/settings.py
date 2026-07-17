"""Shared configuration for wiki core services."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_DATA_ROOT = Path(os.getenv("LOCALAPPDATA", str(REPO_ROOT))) / "GraydazeTrainingVault"

KNOWN_LLM_PROVIDERS = {"openai", "azure-openai"}
IMPLEMENTED_CHAT_PROVIDERS = {"openai", "azure-openai"}
IMPLEMENTED_EMBEDDING_PROVIDERS = {"openai", "azure-openai"}


load_dotenv()


def _read_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _resolve_path(value: str, *, base: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _normalize_provider(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "azure": "azure-openai",
        "azureopenai": "azure-openai",
        "azure-openai": "azure-openai",
        "openai": "openai",
        "openai-compatible": "openai",
        "openaicompatible": "openai",
    }
    return aliases.get(normalized, normalized)


def _detect_legacy_default_provider() -> str:
    if _read_env("AZURE_OPENAI_ENDPOINT"):
        return "azure-openai"
    if _read_env("OPENAI_API_KEY") or _read_env("OPENAI_CHAT_MODEL"):
        return "openai"
    return ""


@dataclass(frozen=True)
class CoreSettings:
    repo_root: Path
    wiki_root: Path
    raw_sources_root: Path
    vector_db_path: Path
    vector_table_name: str
    vector_manifest_path: Path
    source_sync_state_path: Path
    sync_job_state_path: Path
    sync_progress_path: Path
    rag_top_k: int
    rag_index_summary_chars: int
    max_source_chars: int
    llm_provider: str
    llm_chat_provider: str
    llm_chat_model: str
    llm_vision_provider: str
    llm_vision_model: str
    llm_embedding_provider: str
    llm_embedding_model: str
    llm_openai_api_key: str
    llm_openai_base_url: str
    llm_azure_openai_endpoint: str
    llm_azure_openai_api_key: str
    llm_azure_openai_api_version: str
    sharepoint_tenant_id: str
    sharepoint_client_id: str
    sharepoint_client_secret: str
    sharepoint_site_id: str
    sharepoint_site_hostname: str
    sharepoint_site_path: str
    sharepoint_list_id: str
    sharepoint_drive_id: str
    sharepoint_drive_name: str
    sharepoint_raw_root_path: str
    sharepoint_wiki_root_path: str
    sharepoint_request_timeout_seconds: float
    sharepoint_webhook_notification_url: str
    sharepoint_webhook_client_state: str
    # Analytics (SharePoint-list sinks read by the Power BI dashboard). Defaulted so
    # existing construction sites (tests, callers) keep working unchanged.
    analytics_enabled: bool = True
    analytics_query_list_name: str = "TrainingBotQueryEvents"
    analytics_feedback_list_name: str = "TrainingBotFeedback"
    # Ingest LLM (wiki-page generation). Defaulted: falls back to the chat
    # provider/model, so setting LLM_INGEST_MODEL alone upgrades ingest without
    # touching the interactive answering path.
    llm_ingest_provider: str = ""
    llm_ingest_model: str = ""

    @classmethod
    def from_env(cls) -> "CoreSettings":
        repo_root = _resolve_path(_read_env("VAULT_ROOT", default=str(REPO_ROOT)), base=REPO_ROOT)
        local_data_root = _resolve_path(
            _read_env("LOCAL_DATA_ROOT", default=str(DEFAULT_LOCAL_DATA_ROOT)),
            base=REPO_ROOT,
        )
        vector_db_path = _resolve_path(
            _read_env("VECTOR_DB_PATH", default=str(local_data_root / "lancedb")),
            base=local_data_root,
        )
        vector_manifest_path = _resolve_path(
            _read_env("VECTOR_MANIFEST_PATH", default=str(local_data_root / "index-manifest.json")),
            base=local_data_root,
        )
        source_sync_state_path = _resolve_path(
            _read_env("SOURCE_SYNC_STATE_PATH", default=str(local_data_root / "source-sync-state.json")),
            base=local_data_root,
        )
        sync_job_state_path = _resolve_path(
            _read_env("SYNC_JOB_STATE_PATH", default=str(local_data_root / "sync-job-state.json")),
            base=local_data_root,
        )
        sync_progress_path = _resolve_path(
            _read_env("SYNC_PROGRESS_PATH", default=str(local_data_root / "sync-progress.json")),
            base=local_data_root,
        )

        return cls(
            repo_root=repo_root,
            wiki_root=repo_root / "wiki",
            raw_sources_root=repo_root / "raw" / "sources",
            vector_db_path=vector_db_path,
            vector_table_name=_read_env("VECTOR_TABLE_NAME", default="training-vault-wiki"),
            vector_manifest_path=vector_manifest_path,
            source_sync_state_path=source_sync_state_path,
            sync_job_state_path=sync_job_state_path,
            sync_progress_path=sync_progress_path,
            rag_top_k=int(_read_env("RAG_TOP_K", default="6")),
            rag_index_summary_chars=int(_read_env("RAG_INDEX_SUMMARY_CHARS", default="5000")),
            max_source_chars=int(_read_env("AUTO_INGEST_MAX_SOURCE_CHARS", default="18000")),
            llm_provider=_read_env("LLM_PROVIDER", default=_detect_legacy_default_provider()),
            llm_chat_provider=_read_env("LLM_CHAT_PROVIDER"),
            llm_chat_model=_read_env(
                "LLM_CHAT_MODEL",
                "OPENAI_CHAT_MODEL",
                "AZURE_OPENAI_CHAT_DEPLOYMENT",
            ),
            llm_vision_provider=_read_env("LLM_VISION_PROVIDER"),
            llm_vision_model=_read_env(
                "LLM_VISION_MODEL",
                "OPENAI_VISION_MODEL",
                "AZURE_OPENAI_VISION_DEPLOYMENT",
            ),
            llm_embedding_provider=_read_env("LLM_EMBEDDING_PROVIDER"),
            llm_embedding_model=_read_env(
                "LLM_EMBEDDING_MODEL",
                "OPENAI_EMBEDDING_MODEL",
                "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
            ),
            llm_openai_api_key=_read_env("LLM_OPENAI_API_KEY", "OPENAI_API_KEY"),
            llm_openai_base_url=_read_env("LLM_OPENAI_BASE_URL", "OPENAI_BASE_URL"),
            llm_azure_openai_endpoint=_read_env("LLM_AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_ENDPOINT"),
            llm_azure_openai_api_key=_read_env("LLM_AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_KEY"),
            llm_azure_openai_api_version=_read_env(
                "LLM_AZURE_OPENAI_API_VERSION",
                "AZURE_OPENAI_API_VERSION",
                default="2024-02-01",
            ),
            sharepoint_tenant_id=_read_env("SHAREPOINT_TENANT_ID", "AZURE_TENANT_ID"),
            sharepoint_client_id=_read_env("SHAREPOINT_CLIENT_ID", "AZURE_CLIENT_ID"),
            sharepoint_client_secret=_read_env("SHAREPOINT_CLIENT_SECRET", "AZURE_CLIENT_SECRET"),
            sharepoint_site_id=_read_env("SHAREPOINT_SITE_ID"),
            sharepoint_site_hostname=_read_env("SHAREPOINT_SITE_HOSTNAME", "SHAREPOINT_HOSTNAME"),
            sharepoint_site_path=_read_env("SHAREPOINT_SITE_PATH"),
            sharepoint_list_id=_read_env("SHAREPOINT_LIST_ID"),
            sharepoint_drive_id=_read_env("SHAREPOINT_DRIVE_ID"),
            sharepoint_drive_name=_read_env("SHAREPOINT_DRIVE_NAME", "SHAREPOINT_LIBRARY_NAME"),
            sharepoint_raw_root_path=_read_env("SHAREPOINT_RAW_ROOT_PATH", default="raw/sources"),
            sharepoint_wiki_root_path=_read_env("SHAREPOINT_WIKI_ROOT_PATH", default="wiki"),
            sharepoint_request_timeout_seconds=float(
                _read_env("SHAREPOINT_REQUEST_TIMEOUT_SECONDS", default="60")
            ),
            sharepoint_webhook_notification_url=_read_env("SHAREPOINT_WEBHOOK_NOTIFICATION_URL"),
            sharepoint_webhook_client_state=_read_env("SHAREPOINT_WEBHOOK_CLIENT_STATE"),
            analytics_enabled=_read_env("ANALYTICS_ENABLED", default="true").strip().lower()
            not in {"false", "0", "no", "off"},
            analytics_query_list_name=_read_env(
                "ANALYTICS_QUERY_LIST_NAME", default="TrainingBotQueryEvents"
            ),
            analytics_feedback_list_name=_read_env(
                "ANALYTICS_FEEDBACK_LIST_NAME", default="TrainingBotFeedback"
            ),
            llm_ingest_provider=_read_env("LLM_INGEST_PROVIDER"),
            llm_ingest_model=_read_env("LLM_INGEST_MODEL"),
        )

    @property
    def default_llm_provider(self) -> str:
        return _normalize_provider(self.llm_provider)

    @property
    def chat_provider(self) -> str:
        return _normalize_provider(self.llm_chat_provider or self.llm_provider)

    @property
    def vision_provider(self) -> str:
        return _normalize_provider(self.llm_vision_provider or self.llm_chat_provider or self.llm_provider)

    @property
    def embedding_provider(self) -> str:
        return _normalize_provider(self.llm_embedding_provider or self.llm_provider or self.llm_chat_provider)

    @property
    def ingest_provider(self) -> str:
        return _normalize_provider(self.llm_ingest_provider or self.llm_chat_provider or self.llm_provider)

    @property
    def resolved_chat_model(self) -> str:
        return self.llm_chat_model.strip()

    @property
    def resolved_ingest_model(self) -> str:
        return (self.llm_ingest_model or self.llm_chat_model).strip()

    @property
    def resolved_vision_model(self) -> str:
        return (self.llm_vision_model or self.llm_chat_model).strip()

    @property
    def resolved_embedding_model(self) -> str:
        return self.llm_embedding_model.strip()

    @property
    def index_path(self) -> Path:
        return self.wiki_root / "index.md"

    @property
    def overview_path(self) -> Path:
        return self.wiki_root / "overview.md"

    @property
    def log_path(self) -> Path:
        return self.wiki_root / "log.md"

    @property
    def normalized_sharepoint_site_path(self) -> str:
        path = self.sharepoint_site_path.strip()
        if not path:
            return ""
        return "/" + path.strip("/")

    @property
    def normalized_sharepoint_raw_root_path(self) -> str:
        return self.sharepoint_raw_root_path.strip("/")

    @property
    def normalized_sharepoint_wiki_root_path(self) -> str:
        return self.sharepoint_wiki_root_path.strip("/")

    def ensure_data_dirs(self) -> None:
        self.vector_db_path.mkdir(parents=True, exist_ok=True)
        self.vector_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.source_sync_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.sync_job_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.sync_progress_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_llm(self) -> None:
        if not self.chat_provider:
            raise ValueError("Set LLM_PROVIDER or LLM_CHAT_PROVIDER so the chat model provider is defined.")
        if not self.resolved_chat_model:
            raise ValueError("LLM_CHAT_MODEL is required.")
        if not self.embedding_provider:
            raise ValueError("Set LLM_PROVIDER or LLM_EMBEDDING_PROVIDER so the embedding provider is defined.")
        if not self.resolved_embedding_model:
            raise ValueError("LLM_EMBEDDING_MODEL is required.")

        for capability, provider in (
            ("chat", self.chat_provider),
            ("vision", self.vision_provider or self.chat_provider),
            ("embedding", self.embedding_provider),
            ("ingest", self.ingest_provider),
        ):
            self._validate_provider(capability=capability, provider=provider)

    def _validate_provider(self, *, capability: str, provider: str) -> None:
        if provider not in KNOWN_LLM_PROVIDERS:
            supported = ", ".join(sorted(KNOWN_LLM_PROVIDERS))
            raise ValueError(f"Unsupported {capability} provider '{provider}'. Expected one of: {supported}.")

        implemented = IMPLEMENTED_EMBEDDING_PROVIDERS if capability == "embedding" else IMPLEMENTED_CHAT_PROVIDERS
        if provider not in implemented:
            implemented_text = ", ".join(sorted(implemented))
            raise ValueError(
                f"Configured {capability} provider '{provider}' is not implemented yet. "
                f"The environment contract is provider-agnostic, but the current runtime adapters support: {implemented_text}."
            )

        if provider == "openai":
            if not self.llm_openai_api_key:
                raise ValueError("LLM_OPENAI_API_KEY is required when any configured LLM workload uses provider 'openai'.")
            return

        if provider == "azure-openai":
            missing = [
                name
                for name, value in (
                    ("LLM_AZURE_OPENAI_ENDPOINT", self.llm_azure_openai_endpoint),
                    ("LLM_AZURE_OPENAI_API_KEY", self.llm_azure_openai_api_key),
                    ("LLM_AZURE_OPENAI_API_VERSION", self.llm_azure_openai_api_version),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "Azure OpenAI is selected for an LLM workload but required settings are missing: " + ", ".join(missing)
                )
            return

    def validate_source_sync(self) -> None:
        missing = [
            name
            for name, value in (
                ("SHAREPOINT_RAW_ROOT_PATH", self.normalized_sharepoint_raw_root_path),
                ("SHAREPOINT_WIKI_ROOT_PATH", self.normalized_sharepoint_wiki_root_path),
            )
            if not value
        ]
        if missing:
            raise ValueError("SharePoint sync requires the following settings: " + ", ".join(missing))

        if not self.sharepoint_site_id and (not self.sharepoint_site_hostname or not self.normalized_sharepoint_site_path):
            raise ValueError(
                "Set SHAREPOINT_SITE_ID or both SHAREPOINT_SITE_HOSTNAME and SHAREPOINT_SITE_PATH for SharePoint sync."
            )

        if not self.sharepoint_list_id and not self.sharepoint_drive_id and not self.sharepoint_drive_name:
            raise ValueError(
                "Set one of SHAREPOINT_LIST_ID, SHAREPOINT_DRIVE_ID, or SHAREPOINT_DRIVE_NAME for SharePoint sync."
            )

        auth_missing = [
            name
            for name, value in (
                ("SHAREPOINT_TENANT_ID", self.sharepoint_tenant_id),
                ("SHAREPOINT_CLIENT_ID", self.sharepoint_client_id),
                ("SHAREPOINT_CLIENT_SECRET", self.sharepoint_client_secret),
            )
            if not value
        ]
        if auth_missing:
            raise ValueError(
                "SharePoint app-only auth requires the following settings: " + ", ".join(auth_missing)
            )
