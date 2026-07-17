"""Microsoft Graph adapter for SharePoint-backed source synchronization."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from packages.contracts.sync import SourceFileEvent
from packages.shared.documents.extract_text import CONVERTIBLE_EXTENSIONS
from packages.wiki_core.settings import CoreSettings


LOGGER = logging.getLogger(__name__)

# Microsoft Graph maximum lifetime for driveItem change notifications is ~30 days
# for app-only subscriptions, but practical guidance is to renew well before
# expiry. Default to 24h and let callers renew at half-life.
DEFAULT_SUBSCRIPTION_LIFETIME_MINUTES = 60 * 24


class SharePointSourceSyncAdapter:
    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()
        self._settings.validate_source_sync()
        self._timeout = httpx.Timeout(self._settings.sharepoint_request_timeout_seconds)
        self._access_token = ""
        self._resolved_site_id = self._settings.sharepoint_site_id.strip()
        self._configured_list_id = self._settings.sharepoint_list_id.strip()
        self._resolved_drive_id = self._settings.sharepoint_drive_id.strip()
        self._resolved_drive_web_url = ""

    def parse_webhook_payload(self, payload: Any) -> list[SourceFileEvent]:
        if not isinstance(payload, dict):
            return []
        notifications = payload.get("value")
        if not isinstance(notifications, list):
            return []

        expected_client_state = self._settings.sharepoint_webhook_client_state
        events: list[SourceFileEvent] = []
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            for notification in notifications:
                if not isinstance(notification, dict):
                    continue
                if expected_client_state:
                    if str(notification.get("clientState") or "") != expected_client_state:
                        LOGGER.warning("Rejected SharePoint webhook with mismatched clientState")
                        continue

                resource_data = notification.get("resourceData") or {}
                if not isinstance(resource_data, dict):
                    continue
                item_id = str(resource_data.get("id") or "").strip()
                if not item_id:
                    continue

                item = self._fetch_drive_item(client, item_id)
                if item is None:
                    continue
                if not isinstance(item.get("file"), dict):
                    continue

                parent_path = self._graph_parent_path(item)
                item_name = str(item.get("name") or "").strip()
                if not item_name:
                    continue
                item_path = "/".join(part for part in (parent_path, item_name) if part).strip("/")
                if not item_path:
                    continue

                events.append(
                    SourceFileEvent(
                        path=item_path,
                        event_type="webhook",
                        modified_at=(str(item.get("lastModifiedDateTime")) if item.get("lastModifiedDateTime") else None),
                        entry_id=str(item.get("id") or item_id),
                    )
                )
        return events

    def _fetch_drive_item(self, client: httpx.Client, item_id: str) -> dict[str, Any] | None:
        url = f"{self.GRAPH_BASE_URL}/drives/{self._drive_id()}/items/{item_id}"
        response = client.get(url)
        if response.status_code == 404:
            LOGGER.info("SharePoint webhook references missing item id=%s", item_id)
            return None
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else None

    def create_subscription(
        self,
        *,
        notification_url: str | None = None,
        client_state: str | None = None,
        expiration_minutes: int = DEFAULT_SUBSCRIPTION_LIFETIME_MINUTES,
    ) -> dict[str, Any]:
        notification_url = (notification_url or self._settings.sharepoint_webhook_notification_url).strip()
        client_state = (client_state or self._settings.sharepoint_webhook_client_state).strip()
        if not notification_url:
            raise ValueError("SHAREPOINT_WEBHOOK_NOTIFICATION_URL is required to create a subscription.")
        if not client_state:
            raise ValueError("SHAREPOINT_WEBHOOK_CLIENT_STATE is required to create a subscription.")

        expiry = self._format_expiration(expiration_minutes)
        body = {
            "changeType": "updated",
            "notificationUrl": notification_url,
            "resource": f"/drives/{self._drive_id()}/root",
            "expirationDateTime": expiry,
            "clientState": client_state,
        }
        headers = dict(self._authorized_headers())
        headers["Content-Type"] = "application/json"
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            response = client.post(f"{self.GRAPH_BASE_URL}/subscriptions", json=body)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Microsoft Graph subscription creation returned an unexpected payload.")
        return payload

    def renew_subscription(
        self,
        subscription_id: str,
        *,
        expiration_minutes: int = DEFAULT_SUBSCRIPTION_LIFETIME_MINUTES,
    ) -> dict[str, Any]:
        if not subscription_id:
            raise ValueError("subscription_id is required to renew a subscription.")
        body = {"expirationDateTime": self._format_expiration(expiration_minutes)}
        headers = dict(self._authorized_headers())
        headers["Content-Type"] = "application/json"
        url = f"{self.GRAPH_BASE_URL}/subscriptions/{subscription_id}"
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            response = client.patch(url, json=body)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Microsoft Graph subscription renewal returned an unexpected payload.")
        return payload

    def list_subscriptions(self) -> list[dict[str, Any]]:
        """Return every Graph subscription visible to this app registration."""

        subscriptions: list[dict[str, Any]] = []
        url: str | None = f"{self.GRAPH_BASE_URL}/subscriptions"
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            while url:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()
                for item in list(payload.get("value") or []):
                    if isinstance(item, dict):
                        subscriptions.append(item)
                url = str(payload.get("@odata.nextLink") or "") or None
        return subscriptions

    def subscription_resource(self) -> str:
        """The Graph resource path our drive change subscription targets."""

        return f"/drives/{self._drive_id()}/root"

    def delete_subscription(self, subscription_id: str) -> None:
        if not subscription_id:
            return
        url = f"{self.GRAPH_BASE_URL}/subscriptions/{subscription_id}"
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            response = client.delete(url)
            if response.status_code == 404:
                return
            response.raise_for_status()

    @staticmethod
    def _format_expiration(expiration_minutes: int) -> str:
        expiry = datetime.now(UTC) + timedelta(minutes=expiration_minutes)
        return expiry.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")

    def is_in_scope(self, event: SourceFileEvent) -> bool:
        normalized = event.path.strip("/")
        raw_root = self._settings.normalized_sharepoint_raw_root_path
        return normalized == raw_root or normalized.startswith(raw_root + "/")

    def download_file(self, path: str) -> Path:
        relative = self._relative_raw_path(path)
        destination = self._settings.raw_sources_root / relative
        if Path(path).suffix.lower() in CONVERTIBLE_EXTENSIONS:
            # Graph converts legacy formats to PDF server-side; keep the original
            # name and append .pdf so provenance stays visible on disk.
            destination = destination.with_name(destination.name + ".pdf")
            return self.download_remote_file(path, destination, convert_to_pdf=True)
        return self.download_remote_file(path, destination)

    def download_remote_file(self, remote_path: str, destination: Path, *, convert_to_pdf: bool = False) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)

        download_url = self._graph_item_content_url(remote_path)
        if convert_to_pdf:
            download_url += "?format=pdf"
        LOGGER.info("Downloading SharePoint file path=%s destination=%s", remote_path, destination)
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers(), follow_redirects=True) as client:
            with client.stream("GET", download_url) as response:
                response.raise_for_status()
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes():
                        handle.write(chunk)
        return destination

    def list_files_recursive(self, root_path: str) -> list[SourceFileEvent]:
        files: list[SourceFileEvent] = []
        folders_to_visit = [root_path.strip("/")]

        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            while folders_to_visit:
                current = folders_to_visit.pop()
                payload = self._get_children_payload(client, current)

                for item in list(payload.get("value") or []):
                    if not isinstance(item, dict):
                        continue

                    parent_path = self._graph_parent_path(item)
                    item_name = str(item.get("name") or "").strip()
                    if not item_name:
                        continue

                    item_path = "/".join(part for part in (parent_path, item_name) if part).strip("/")
                    if not item_path:
                        continue

                    if isinstance(item.get("folder"), dict):
                        folders_to_visit.append(item_path)
                        continue

                    if not isinstance(item.get("file"), dict):
                        continue

                    files.append(
                        SourceFileEvent(
                            path=item_path,
                            event_type="manual-sync",
                            modified_at=(str(item.get("lastModifiedDateTime")) if item.get("lastModifiedDateTime") else None),
                            entry_id=(str(item.get("id")) if item.get("id") else None),
                        )
                    )
        return files

    def upload_text_file(self, relative_path: str, content: str) -> None:
        remote_path = self._remote_wiki_path(relative_path)
        self._upload_bytes(remote_path=remote_path, payload=content.encode("utf-8"), content_type="text/markdown; charset=utf-8")

    def ensure_remote_folder(self, remote_path: str) -> None:
        segments = [segment for segment in remote_path.strip("/").split("/") if segment]
        if not segments:
            return

        current_parent = ""
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            for segment in segments:
                child_path = "/".join(part for part in (current_parent, segment) if part)
                if self._remote_item_exists(client, child_path):
                    current_parent = child_path
                    continue

                url = self._graph_children_url(current_parent)
                response = client.post(
                    url,
                    json={
                        "name": segment,
                        "folder": {},
                        "@microsoft.graph.conflictBehavior": "replace",
                    },
                )
                response.raise_for_status()
                current_parent = child_path

    def _authorized_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Accept": "application/json",
        }

    def _get_access_token(self) -> str:
        if self._access_token:
            return self._access_token

        token_url = f"https://login.microsoftonline.com/{self._settings.sharepoint_tenant_id}/oauth2/v2.0/token"
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._settings.sharepoint_client_id,
                    "client_secret": self._settings.sharepoint_client_secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            response.raise_for_status()
            payload = response.json()

        access_token = str(payload.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("Microsoft Graph token response did not include an access_token.")
        self._access_token = access_token
        return access_token

    def _site_id(self) -> str:
        if self._resolved_site_id:
            return self._resolved_site_id

        site_path = quote(self._settings.normalized_sharepoint_site_path, safe="/")
        url = f"{self.GRAPH_BASE_URL}/sites/{self._settings.sharepoint_site_hostname}:{site_path}"
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()

        site_id = str(payload.get("id") or "").strip()
        if not site_id:
            raise ValueError("Microsoft Graph site lookup did not return an id.")
        self._resolved_site_id = site_id
        return site_id

    def _drive_id(self) -> str:
        if self._resolved_drive_id:
            return self._resolved_drive_id

        if self._configured_list_id:
            url = f"{self.GRAPH_BASE_URL}/sites/{self._site_id()}/lists/{self._configured_list_id}/drive"
            with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
                response = client.get(url)
                response.raise_for_status()
                payload = response.json()

            drive_id = str(payload.get("id") or "").strip()
            if not drive_id:
                raise ValueError(f"Microsoft Graph drive lookup did not return an id for list {self._configured_list_id!r}.")
            self._resolved_drive_id = drive_id
            return drive_id

        url = f"{self.GRAPH_BASE_URL}/sites/{self._site_id()}/drives"
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()

        target_name = self._settings.sharepoint_drive_name.strip().lower()
        for item in list(payload.get("value") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            if name == target_name:
                drive_id = str(item.get("id") or "").strip()
                if drive_id:
                    self._resolved_drive_id = drive_id
                    return drive_id

        raise ValueError(f"Could not find SharePoint drive named {self._settings.sharepoint_drive_name!r}.")

    def drive_web_url(self) -> str:
        """Return the browser-openable ``webUrl`` of the document library (drive) root.

        Used to build read-only links to source files. Cached on the instance so a
        single Graph call serves every link for the process lifetime.
        """

        if self._resolved_drive_web_url:
            return self._resolved_drive_web_url

        url = f"{self.GRAPH_BASE_URL}/drives/{self._drive_id()}"
        with httpx.Client(timeout=self._timeout, headers=self._authorized_headers()) as client:
            response = client.get(url)
            response.raise_for_status()
            payload = response.json()

        web_url = str(payload.get("webUrl") or "").strip()
        if not web_url:
            raise ValueError("Microsoft Graph drive lookup did not return a webUrl.")
        self._resolved_drive_web_url = web_url
        return web_url

    def _graph_drive_item_url(self, path: str) -> str:
        normalized_path = quote(path.strip("/"), safe="/")
        return f"{self.GRAPH_BASE_URL}/drives/{self._drive_id()}/root:/{normalized_path}"

    def _graph_item_content_url(self, path: str) -> str:
        return self._graph_drive_item_url(path) + ":/content"

    def _graph_children_url(self, path: str) -> str:
        normalized_path = path.strip("/")
        if not normalized_path:
            return f"{self.GRAPH_BASE_URL}/drives/{self._drive_id()}/root/children"
        return self._graph_drive_item_url(normalized_path) + ":/children"

    def _get_children_payload(self, client: httpx.Client, folder_path: str) -> dict[str, Any]:
        response = client.get(self._graph_children_url(folder_path))
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected SharePoint folder response for {folder_path!r}")
        return payload

    def _graph_parent_path(self, item: dict[str, Any]) -> str:
        parent_reference = item.get("parentReference")
        if not isinstance(parent_reference, dict):
            return ""

        path = str(parent_reference.get("path") or "")
        marker = "/root:/"
        if marker not in path:
            return ""
        return path.split(marker, maxsplit=1)[1].strip("/")

    def _relative_raw_path(self, sharepoint_path: str) -> Path:
        normalized = sharepoint_path.strip("/")
        raw_root = self._settings.normalized_sharepoint_raw_root_path
        if normalized == raw_root:
            return Path()
        if normalized.startswith(raw_root + "/"):
            normalized = normalized[len(raw_root) + 1 :]
        return Path(normalized)

    def _remote_wiki_path(self, relative_path: str) -> str:
        relative = relative_path.strip("/")
        wiki_root = self._settings.normalized_sharepoint_wiki_root_path
        if relative.startswith("wiki/"):
            relative = relative[len("wiki/") :]
        return "/".join(part for part in (wiki_root, relative) if part)

    def _upload_bytes(self, *, remote_path: str, payload: bytes, content_type: str) -> None:
        parent = Path(remote_path).parent.as_posix()
        if parent and parent != ".":
            self.ensure_remote_folder(parent)

        headers = dict(self._authorized_headers())
        headers["Content-Type"] = content_type
        url = self._graph_item_content_url(remote_path)
        with httpx.Client(timeout=self._timeout, headers=headers) as client:
            response = client.put(url, content=payload)
            response.raise_for_status()

    def _remote_item_exists(self, client: httpx.Client, remote_path: str) -> bool:
        response = client.get(self._graph_drive_item_url(remote_path))
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True
