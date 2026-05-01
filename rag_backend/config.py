"""Shared configuration for the local RAG and Egnyte sync backend."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_DATA_ROOT = Path(os.getenv("LOCALAPPDATA", str(REPO_ROOT))) / "GraydazeTrainingVault"


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


@dataclass(frozen=True)
class BackendSettings:
    """Runtime settings for retrieval, indexing, and Egnyte sync."""

    repo_root: Path
    wiki_root: Path
    raw_sources_root: Path
    vector_db_path: Path
    vector_table_name: str
    vector_manifest_path: Path
    egnyte_state_path: Path
    rag_top_k: int
    rag_index_summary_chars: int
    max_source_chars: int
    openai_api_key: str
    openai_base_url: str
    openai_chat_model: str
    openai_vision_model: str
    openai_embedding_model: str
    azure_openai_endpoint: str
    azure_openai_api_key: str
    azure_openai_api_version: str
    azure_openai_chat_deployment: str
    azure_openai_vision_deployment: str
    azure_openai_embedding_deployment: str
    egnyte_domain: str
    egnyte_api_token: str
    egnyte_sync_root: str
    egnyte_training_folder_name: str
    egnyte_request_timeout_seconds: float

    @classmethod
    def from_env(cls) -> "BackendSettings":
        repo_root = _resolve_path(_read_env("VAULT_ROOT", default=str(REPO_ROOT)), base=REPO_ROOT)
        local_data_root = _resolve_path(
            _read_env("LOCAL_DATA_ROOT", default=str(DEFAULT_LOCAL_DATA_ROOT)),
            base=REPO_ROOT,
        )
        vector_db_path = _resolve_path(
            _read_env(
                "VECTOR_DB_PATH",
                default=str(local_data_root / "lancedb"),
            ),
            base=local_data_root,
        )
        vector_manifest_path = _resolve_path(
            _read_env(
                "VECTOR_MANIFEST_PATH",
                default=str(local_data_root / "index-manifest.json"),
            ),
            base=local_data_root,
        )
        egnyte_state_path = _resolve_path(
            _read_env(
                "EGNYTE_SYNC_STATE_PATH",
                default=str(local_data_root / "egnyte-sync-state.json"),
            ),
            base=local_data_root,
        )

        return cls(
            repo_root=repo_root,
            wiki_root=repo_root / "wiki",
            raw_sources_root=repo_root / "raw" / "sources",
            vector_db_path=vector_db_path,
            vector_table_name=_read_env(
                "VECTOR_TABLE_NAME",
                default="training-vault-wiki",
            ),
            vector_manifest_path=vector_manifest_path,
            egnyte_state_path=egnyte_state_path,
            rag_top_k=int(_read_env("RAG_TOP_K", default="6")),
            rag_index_summary_chars=int(_read_env("RAG_INDEX_SUMMARY_CHARS", default="5000")),
            max_source_chars=int(_read_env("AUTO_INGEST_MAX_SOURCE_CHARS", default="18000")),
            openai_api_key=_read_env("OPENAI_API_KEY"),
            openai_base_url=_read_env("OPENAI_BASE_URL"),
            openai_chat_model=_read_env("OPENAI_CHAT_MODEL"),
            openai_vision_model=_read_env("OPENAI_VISION_MODEL"),
            openai_embedding_model=_read_env(
                "OPENAI_EMBEDDING_MODEL",
                default="text-embedding-3-large",
            ),
            azure_openai_endpoint=_read_env("AZURE_OPENAI_ENDPOINT"),
            azure_openai_api_key=_read_env("AZURE_OPENAI_API_KEY"),
            azure_openai_api_version=_read_env(
                "AZURE_OPENAI_API_VERSION",
                default="2024-02-01",
            ),
            azure_openai_chat_deployment=_read_env("AZURE_OPENAI_CHAT_DEPLOYMENT"),
            azure_openai_vision_deployment=_read_env("AZURE_OPENAI_VISION_DEPLOYMENT"),
            azure_openai_embedding_deployment=_read_env("AZURE_OPENAI_EMBEDDING_DEPLOYMENT"),
            egnyte_domain=_read_env("EGNYTE_DOMAIN"),
            egnyte_api_token=_read_env("EGNYTE_API_TOKEN"),
            egnyte_sync_root=_read_env("EGNYTE_SYNC_ROOT"),
            egnyte_training_folder_name=_read_env(
                "EGNYTE_TRAINING_FOLDER_NAME",
                default="Training Program CRD",
            ),
            egnyte_request_timeout_seconds=float(
                _read_env("EGNYTE_REQUEST_TIMEOUT_SECONDS", default="60")
            ),
        )

    @property
    def uses_azure_openai(self) -> bool:
        return bool(self.azure_openai_endpoint)

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
    def training_folder_marker(self) -> str:
        return f"/{self.egnyte_training_folder_name.strip('/')}/"

    def ensure_data_dirs(self) -> None:
        self.vector_db_path.mkdir(parents=True, exist_ok=True)
        self.vector_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.egnyte_state_path.parent.mkdir(parents=True, exist_ok=True)

    def validate_openai(self) -> None:
        if self.uses_azure_openai:
            missing = [
                name
                for name, value in (
                    ("AZURE_OPENAI_ENDPOINT", self.azure_openai_endpoint),
                    ("AZURE_OPENAI_API_KEY", self.azure_openai_api_key),
                    ("AZURE_OPENAI_CHAT_DEPLOYMENT", self.azure_openai_chat_deployment),
                    (
                        "AZURE_OPENAI_EMBEDDING_DEPLOYMENT",
                        self.azure_openai_embedding_deployment,
                    ),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    "Azure OpenAI is configured but required settings are missing: "
                    + ", ".join(missing)
                )
            return

        if not self.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY is required unless Azure OpenAI settings are configured."
            )

        if not self.openai_chat_model:
            raise ValueError(
                "OPENAI_CHAT_MODEL is required unless Azure OpenAI settings are configured."
            )

    def validate_egnyte(self) -> None:
        missing = [
            name
            for name, value in (
                ("EGNYTE_DOMAIN", self.egnyte_domain),
                ("EGNYTE_API_TOKEN", self.egnyte_api_token),
                ("EGNYTE_SYNC_ROOT", self.egnyte_sync_root),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Egnyte sync requires the following settings: " + ", ".join(missing)
            )
