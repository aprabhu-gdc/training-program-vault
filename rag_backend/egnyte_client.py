"""Minimal Egnyte API client for downloading source files into `raw/sources/`."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from rag_backend.config import BackendSettings


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EgnyteFileEvent:
    """Normalized webhook file event."""

    path: str
    event_type: str
    modified_at: str | None = None
    entry_id: str | None = None


class EgnyteClient:
    """Download changed Egnyte files into the local raw source tree."""

    def __init__(self, settings: BackendSettings | None = None) -> None:
        self._settings = settings or BackendSettings.from_env()
        self._settings.validate_egnyte()
        self._base_url = f"https://{self._settings.egnyte_domain}"
        self._timeout = httpx.Timeout(self._settings.egnyte_request_timeout_seconds)

    @staticmethod
    def parse_webhook_payload(payload: Any) -> list[EgnyteFileEvent]:
        """Accept several Egnyte-style payload shapes and normalize them."""

        events: list[EgnyteFileEvent] = []
        candidates: list[dict[str, Any]] = []

        if isinstance(payload, dict):
            if isinstance(payload.get("events"), list):
                candidates.extend(item for item in payload["events"] if isinstance(item, dict))
            elif isinstance(payload.get("data"), list):
                candidates.extend(item for item in payload["data"] if isinstance(item, dict))
            else:
                candidates.append(payload)

        for item in candidates:
            path = item.get("path") or item.get("object_path") or item.get("file_path")
            if not path:
                continue
            event_type = str(
                item.get("event_type")
                or item.get("action")
                or item.get("event")
                or item.get("type")
                or "updated"
            )
            events.append(
                EgnyteFileEvent(
                    path=str(path),
                    event_type=event_type,
                    modified_at=(
                        str(item.get("modified_at"))
                        if item.get("modified_at") is not None
                        else None
                    ),
                    entry_id=(str(item.get("id")) if item.get("id") is not None else None),
                )
            )
        return events

    def is_training_program_event(self, event: EgnyteFileEvent) -> bool:
        normalized = "/" + event.path.strip("/")
        return self._settings.egnyte_training_folder_name in normalized and normalized.startswith(
            "/" + self._settings.egnyte_sync_root.strip("/")
        )

    def download_file(self, egnyte_path: str) -> Path:
        """Download a single Egnyte file into the mirrored raw path."""

        relative = self._relative_raw_path(egnyte_path)
        destination = self._settings.raw_sources_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)

        encoded_path = quote(egnyte_path.strip("/"), safe="/")
        url = f"{self._base_url}/pubapi/v1/fs-content/{encoded_path}"
        headers = {"Authorization": f"Bearer {self._settings.egnyte_api_token}"}

        LOGGER.info("Downloading Egnyte file path=%s destination=%s", egnyte_path, destination)
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        return destination

    def list_files_recursive(self, folder_path: str) -> list[EgnyteFileEvent]:
        """Recursively list files under an Egnyte folder."""

        headers = {"Authorization": f"Bearer {self._settings.egnyte_api_token}"}
        files: list[EgnyteFileEvent] = []
        folders_to_visit = ["/" + folder_path.strip("/")]

        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            while folders_to_visit:
                current = folders_to_visit.pop()
                payload = self._get_folder_metadata(client, current)

                for folder in payload.get("folders", []) or []:
                    folder_name = folder.get("name")
                    if folder_name:
                        folders_to_visit.append(current.rstrip("/") + "/" + str(folder_name).strip("/"))

                for file_info in payload.get("files", []) or []:
                    name = file_info.get("name")
                    if not name:
                        continue
                    file_path = current.rstrip("/") + "/" + str(name).strip("/")
                    files.append(
                        EgnyteFileEvent(
                            path=file_path,
                            event_type="manual-sync",
                            modified_at=str(
                                file_info.get("last_modified")
                                or file_info.get("lastModified")
                                or file_info.get("uploaded")
                                or ""
                            ),
                            entry_id=str(file_info.get("entry_id") or file_info.get("id") or ""),
                        )
                    )
        return files

    def _get_folder_metadata(self, client: httpx.Client, folder_path: str) -> dict[str, Any]:
        encoded_path = quote(folder_path.strip("/"), safe="/")
        url = f"{self._base_url}/pubapi/v1/fs/{encoded_path}"
        response = client.get(url, params={"list_content": "true", "list_custom_metadata": "false"})
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected Egnyte folder response for {folder_path!r}")
        return payload

    def _relative_raw_path(self, egnyte_path: str) -> Path:
        normalized = egnyte_path.strip("/")
        sync_root = self._settings.egnyte_sync_root.strip("/")
        if normalized.startswith(sync_root + "/"):
            normalized = normalized[len(sync_root) + 1 :]
        return Path(normalized)
