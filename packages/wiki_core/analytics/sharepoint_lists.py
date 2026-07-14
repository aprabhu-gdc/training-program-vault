"""Microsoft Graph client for writing analytics rows to SharePoint lists.

This is a deliberate sibling of ``SharePointSourceSyncAdapter`` rather than an
extension of it: the sync adapter validates drive/webhook configuration this
client does not need, and it caches its app-only token forever (safe only for
short-lived adapter instances). Analytics clients live for the whole bot
process, so the token here is cached with its expiry and refreshed on demand.

Privacy note: rows written through this client must never contain a user's
question or the bot's answer text — only concept titles, identity fields, and
feedback ratings/comments. See ``teams_bot/services/analytics.py``.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

import httpx

from packages.wiki_core.settings import CoreSettings


LOGGER = logging.getLogger(__name__)

# Refresh the app-only token this many seconds before Graph says it expires.
TOKEN_EXPIRY_BUFFER_SECONDS = 300

# Shared column names so the setup script and the writers can never drift.
# "Timestamp" is not a reserved SharePoint internal name for generic lists; if a
# tenant rejects it, change this constant and re-run the setup script.
TIMESTAMP_COLUMN = "Timestamp"

QUERY_EVENT_COLUMNS: tuple[dict[str, Any], ...] = (
    {"name": TIMESTAMP_COLUMN, "dateTime": {}},
    {"name": "RequestId", "text": {}},
    {"name": "UserId", "text": {}},
    {"name": "UserName", "text": {}},
    {"name": "Concept", "text": {}},
    {"name": "IsUnknown", "boolean": {}},
)

FEEDBACK_COLUMNS: tuple[dict[str, Any], ...] = (
    {"name": TIMESTAMP_COLUMN, "dateTime": {}},
    {"name": "RequestId", "text": {}},
    {"name": "UserId", "text": {}},
    {"name": "UserName", "text": {}},
    {"name": "Rating", "text": {}},
    {"name": "Comment", "text": {"allowMultipleLines": True}},
    {"name": "Concepts", "text": {}},
)


class SharePointListClient:
    """App-only Graph client for list-item writes and idempotent list creation.

    All methods are synchronous (httpx sync client, matching the ingest adapter);
    async callers must wrap calls in ``asyncio.to_thread``.
    """

    GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()
        self._validate()
        self._timeout = httpx.Timeout(self._settings.sharepoint_request_timeout_seconds)
        self._access_token = ""
        self._token_expires_at = 0.0
        self._resolved_site_id = self._settings.sharepoint_site_id.strip()
        self._list_ids: dict[str, str] = {}

    def _validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("SHAREPOINT_TENANT_ID", self._settings.sharepoint_tenant_id),
                ("SHAREPOINT_CLIENT_ID", self._settings.sharepoint_client_id),
                ("SHAREPOINT_CLIENT_SECRET", self._settings.sharepoint_client_secret),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "SharePoint analytics requires the following settings: " + ", ".join(missing)
            )
        if not self._settings.sharepoint_site_id and (
            not self._settings.sharepoint_site_hostname
            or not self._settings.normalized_sharepoint_site_path
        ):
            raise ValueError(
                "Set SHAREPOINT_SITE_ID or both SHAREPOINT_SITE_HOSTNAME and SHAREPOINT_SITE_PATH "
                "for SharePoint analytics."
            )

    def create_item(self, list_name: str, fields: dict[str, Any]) -> None:
        """Append one row to the named list. Raises on any Graph error."""

        url = (
            f"{self.GRAPH_BASE_URL}/sites/{self._site_id()}"
            f"/lists/{self._list_id(list_name)}/items"
        )
        response = self._request("POST", url, json={"fields": fields})
        response.raise_for_status()

    def ensure_list(self, list_name: str, columns: tuple[dict[str, Any], ...]) -> bool:
        """Create the list with the given columns if it does not exist.

        Returns True when the list was created, False when it already existed.
        Existing lists are left untouched (columns are not reconciled).
        """

        if self._find_list_id(list_name) is not None:
            return False

        url = f"{self.GRAPH_BASE_URL}/sites/{self._site_id()}/lists"
        body = {
            "displayName": list_name,
            "columns": list(columns),
            "list": {"template": "genericList"},
        }
        response = self._request("POST", url, json=body)
        response.raise_for_status()
        payload = response.json()
        list_id = str(payload.get("id") or "").strip()
        if list_id:
            self._list_ids[list_name] = list_id
        return True

    def _list_id(self, list_name: str) -> str:
        cached = self._list_ids.get(list_name)
        if cached:
            return cached
        list_id = self._find_list_id(list_name)
        if not list_id:
            raise ValueError(
                f"SharePoint list {list_name!r} was not found. "
                "Run scripts/setup_analytics_lists.py to provision it."
            )
        self._list_ids[list_name] = list_id
        return list_id

    def _find_list_id(self, list_name: str) -> str | None:
        # OData string literals escape single quotes by doubling them.
        escaped = list_name.replace("'", "''")
        url = (
            f"{self.GRAPH_BASE_URL}/sites/{self._site_id()}/lists"
            f"?$filter=displayName eq '{quote(escaped)}'&$select=id,displayName"
        )
        response = self._request("GET", url)
        response.raise_for_status()
        for item in list(response.json().get("value") or []):
            if isinstance(item, dict) and str(item.get("displayName") or "") == list_name:
                list_id = str(item.get("id") or "").strip()
                if list_id:
                    return list_id
        return None

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> httpx.Response:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.request(method, url, json=json, headers=self._authorized_headers())
        if response.status_code == 401 and retry_auth:
            # Token may have been revoked before its stated expiry; refresh once.
            self._access_token = ""
            self._token_expires_at = 0.0
            return self._request(method, url, json=json, retry_auth=False)
        return response

    def _authorized_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_access_token()}",
            "Accept": "application/json",
        }

    def _get_access_token(self) -> str:
        if self._access_token and time.monotonic() < self._token_expires_at:
            return self._access_token

        token_url = (
            f"https://login.microsoftonline.com/{self._settings.sharepoint_tenant_id}"
            "/oauth2/v2.0/token"
        )
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
        expires_in = float(payload.get("expires_in") or 3600)
        self._access_token = access_token
        self._token_expires_at = time.monotonic() + max(
            expires_in - TOKEN_EXPIRY_BUFFER_SECONDS, 60.0
        )
        return access_token

    def _site_id(self) -> str:
        if self._resolved_site_id:
            return self._resolved_site_id

        site_path = quote(self._settings.normalized_sharepoint_site_path, safe="/")
        url = f"{self.GRAPH_BASE_URL}/sites/{self._settings.sharepoint_site_hostname}:{site_path}"
        response = self._request("GET", url)
        response.raise_for_status()
        site_id = str(response.json().get("id") or "").strip()
        if not site_id:
            raise ValueError("Microsoft Graph site lookup did not return an id.")
        self._resolved_site_id = site_id
        return site_id
