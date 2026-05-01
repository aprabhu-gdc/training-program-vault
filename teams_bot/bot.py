"""Microsoft Teams activity handler for the Graydaze PM Training Vault bot."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import mimetypes
import re
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp
from botbuilder.core import ConversationState, TurnContext, UserState
from botbuilder.core.teams import TeamsActivityHandler
from botbuilder.schema import Activity, ActivityTypes, Attachment, InvokeResponse

from rag_backend.auto_ingest import SyncReport
from scripts.extract_text import SUPPORTED_EXTENSIONS, extract_text
from teams_bot.cards import build_feedback_card
from teams_bot.config import Settings
from teams_bot.services.feedback import FeedbackEvent, FeedbackLogger
from teams_bot.services.wiki_query import (
    WikiIntegrationError,
    WikiQueryAttachment,
    WikiQueryRequest,
    WikiQueryService,
)


LOGGER = logging.getLogger(__name__)


class GraydazeTrainingBot(TeamsActivityHandler):
    """Teams bot that routes user questions into the existing wiki query layer."""

    def __init__(
        self,
        *,
        settings: Settings,
        user_state: UserState,
        conversation_state: ConversationState,
        wiki_query_service: WikiQueryService,
        feedback_logger: FeedbackLogger,
        sync_runner: Callable[[str, dict[str, Any] | None], Awaitable[SyncReport]],
    ) -> None:
        super().__init__()
        self._settings = settings
        self._user_state = user_state
        self._conversation_state = conversation_state
        self._wiki_query_service = wiki_query_service
        self._feedback_logger = feedback_logger
        self._sync_runner = sync_runner
        # Use a simple boolean property rather than a custom object so the state
        # remains JSON-serializable across real storage providers.
        self._welcome_accessor = self._user_state.create_property("HasSeenWelcome")

    async def on_turn(self, turn_context: TurnContext) -> None:
        """Run the normal activity pipeline and then persist bot state changes."""

        await super().on_turn(turn_context)
        await self._conversation_state.save_changes(turn_context, force=False)
        await self._user_state.save_changes(turn_context, force=False)

    async def on_members_added_activity(
        self,
        members_added,
        turn_context: TurnContext,
    ) -> None:
        """Send the welcome message when the bot is installed or a chat starts."""

        bot_id = turn_context.activity.recipient.id if turn_context.activity.recipient else None

        for member in members_added:
            if member.id != bot_id:
                await self._send_welcome_if_needed(turn_context)
                break

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        """Handle a user message, send typing indicators, then answer via the wiki."""

        if self._is_feedback_submission(turn_context.activity):
            await self._handle_feedback(turn_context)
            return

        # Send typing synchronously first so Teams users get immediate feedback,
        # then continue sending periodic typing activities while the query runs.
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        typing_task = asyncio.create_task(self._typing_loop(turn_context))

        try:
            await self._send_welcome_if_needed(turn_context)

            query_text = self._extract_message_text(turn_context)
            if self._is_sync_command(query_text):
                await self._handle_sync_command(turn_context)
                return

            incoming_attachments = [
                attachment
                for attachment in (turn_context.activity.attachments or [])
                if self._is_user_file_attachment(attachment)
            ]
            query_attachments, skipped_attachments = await self._extract_query_attachments(
                incoming_attachments
            )

            if incoming_attachments and not query_attachments:
                await turn_context.send_activity(
                    "I couldn’t read the attached file. I currently support images plus .pdf, .docx, .pptx, .xlsx, .xlsm, .txt, .md, .csv, and .json attachments."
                )
                return

            if skipped_attachments:
                await turn_context.send_activity(
                    "I used the supported attachment inputs and skipped: "
                    + ", ".join(f"`{name}`" for name in skipped_attachments)
                    + "."
                )

            if not query_text and query_attachments:
                query_text = (
                    "Please analyze the attached file or image using the Graydaze PM Training Vault as the reference."
                )

            if not query_text:
                await turn_context.send_activity(
                    "Send me a training question and I’ll search the Graydaze PM Training Vault for you."
                )
                return

            request_id = uuid.uuid4().hex[:12]
            request = WikiQueryRequest(
                request_id=request_id,
                query=query_text,
                user_id=turn_context.activity.from_property.id if turn_context.activity.from_property else None,
                user_name=turn_context.activity.from_property.name if turn_context.activity.from_property else None,
                conversation_id=(
                    turn_context.activity.conversation.id
                    if turn_context.activity.conversation
                    else None
                ),
                channel_id=turn_context.activity.channel_id,
                tenant_id=self._extract_tenant_id(turn_context.activity.channel_data),
                locale=turn_context.activity.locale,
                channel_data=turn_context.activity.channel_data,
                attachments=query_attachments,
            )

            LOGGER.info(
                "Dispatching Teams query request_id=%s user_id=%s conversation_id=%s text=%r",
                request.request_id,
                request.user_id,
                request.conversation_id,
                request.query,
            )

            result = await self._wiki_query_service.query(request)

            answer_activity = Activity(
                type=ActivityTypes.message,
                text=result.answer_text,
                attachments=[build_feedback_card(request.request_id)],
            )
            await turn_context.send_activity(answer_activity)
        except WikiIntegrationError as exc:
            LOGGER.exception("Wiki integration failure", exc_info=exc)
            await turn_context.send_activity(
                "I couldn’t reach the Graydaze PM Training Vault right now. Please try again in a moment."
            )
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    async def on_invoke_activity(self, turn_context: TurnContext) -> InvokeResponse:
        """Handle invoke-style submissions such as some Adaptive Card actions.

        Teams can deliver card actions either as message activities or invoke
        activities depending on client surface and card behavior. Supporting both
        keeps the feedback buttons reliable.
        """

        if self._is_feedback_submission(turn_context.activity):
            await self._handle_feedback(turn_context)
            return InvokeResponse(status=200)

        return await super().on_invoke_activity(turn_context)

    async def _send_welcome_if_needed(self, turn_context: TurnContext) -> None:
        """Send the first-run welcome exactly once per user state record."""

        has_seen_welcome = await self._welcome_accessor.get(turn_context, False)
        if has_seen_welcome:
            return

        welcome_text = (
            "Hi, I’m the **Graydaze PM Training Vault**. I can answer questions from the company’s PM "
            "training materials right inside Teams.\n\n"
            "Try asking:\n"
            f"- {self._settings.welcome_examples[0]}\n"
            f"- {self._settings.welcome_examples[1]}"
        )
        await turn_context.send_activity(welcome_text)
        await self._welcome_accessor.set(turn_context, True)

    async def _handle_feedback(self, turn_context: TurnContext) -> None:
        """Log user feedback from the Adaptive Card buttons and acknowledge it."""

        value = turn_context.activity.value or {}
        feedback = str(value.get("feedback", "unknown")).strip().lower()
        request_id = str(value.get("request_id", "unknown")).strip()
        await self._feedback_logger.log(
            FeedbackEvent(
                request_id=request_id,
                feedback=feedback,
                user_id=(
                    turn_context.activity.from_property.id
                    if turn_context.activity.from_property
                    else None
                ),
                user_name=(
                    turn_context.activity.from_property.name
                    if turn_context.activity.from_property
                    else None
                ),
                conversation_id=(
                    turn_context.activity.conversation.id
                    if turn_context.activity.conversation
                    else None
                ),
                tenant_id=self._extract_tenant_id(turn_context.activity.channel_data),
                channel_id=turn_context.activity.channel_id,
            )
        )

        await turn_context.send_activity("Thanks for the feedback.")

    async def _handle_sync_command(self, turn_context: TurnContext) -> None:
        """Trigger a manual Egnyte sync instead of querying the RAG pipeline."""

        await turn_context.send_activity("Syncing with Egnyte. I’ll post the result here when it finishes.")
        try:
            report = await self._sync_runner("teams-manual-sync", None)
        except Exception:
            LOGGER.exception("Manual Teams sync failed")
            await turn_context.send_activity(
                "Syncing with Egnyte failed before the wiki could be refreshed. Check the app logs for details."
            )
            return

        message = (
            "Syncing with Egnyte complete. "
            f"{len(report.downloaded_files)} files updated, "
            f"{len(report.updated_wiki_files)} wiki files changed, and "
            f"{len(report.index_report.indexed_files)} wiki files were re-indexed."
        )
        if report.skipped_files:
            message += f" {len(report.skipped_files)} files were unchanged and skipped."
        await turn_context.send_activity(message)

    async def _extract_query_attachments(
        self,
        attachments: list[Attachment],
    ) -> tuple[tuple[WikiQueryAttachment, ...], tuple[str, ...]]:
        """Download and normalize supported Teams attachments for the query backend."""

        processed: list[WikiQueryAttachment] = []
        skipped: list[str] = []
        for attachment in attachments:
            query_attachment = await self._build_query_attachment(attachment)
            if query_attachment is not None:
                processed.append(query_attachment)
            else:
                skipped.append(self._attachment_name(attachment))
        return tuple(processed), tuple(skipped)

    async def _build_query_attachment(
        self,
        attachment: Attachment,
    ) -> WikiQueryAttachment | None:
        """Convert a Teams attachment into text or image context for the backend."""

        download_url = self._attachment_download_url(attachment)
        if not download_url:
            LOGGER.info(
                "Skipping attachment without download URL name=%s content_type=%s",
                self._attachment_name(attachment),
                self._attachment_content_type(attachment),
            )
            return None

        name = self._attachment_name(attachment)
        content_type = self._attachment_content_type(attachment)
        try:
            payload = await self._download_attachment(download_url)
        except Exception:
            LOGGER.warning("Failed to download attachment name=%s url=%s", name, download_url, exc_info=True)
            return None

        if self._is_image_attachment(name=name, content_type=content_type):
            image_content_type = content_type or mimetypes.guess_type(name)[0] or "image/png"
            return WikiQueryAttachment(
                name=name,
                content_type=image_content_type,
                image_data_url=self._to_data_url(payload, image_content_type),
            )

        extracted_text = await self._extract_attachment_text(
            name=name,
            content_type=content_type,
            payload=payload,
        )
        if not extracted_text:
            return None

        return WikiQueryAttachment(
            name=name,
            content_type=content_type or "application/octet-stream",
            text_content=extracted_text,
        )

    async def _download_attachment(self, download_url: str) -> bytes:
        timeout = aiohttp.ClientTimeout(total=self._settings.wiki_query_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(download_url) as response:
                response.raise_for_status()
                return await response.read()

    async def _extract_attachment_text(
        self,
        *,
        name: str,
        content_type: str,
        payload: bytes,
    ) -> str:
        suffix = Path(name).suffix.lower()
        if suffix in SUPPORTED_EXTENSIONS:
            with tempfile.TemporaryDirectory(prefix="graydaze-attachment-") as temp_dir:
                file_path = Path(temp_dir) / name
                file_path.write_bytes(payload)
                text = await asyncio.to_thread(extract_text, file_path)
        elif content_type.startswith("text/") or suffix in {".txt", ".md", ".csv", ".json"}:
            text = payload.decode("utf-8", errors="replace")
        else:
            return ""

        return text.strip()[:12000]

    @staticmethod
    def _attachment_download_url(attachment: Attachment) -> str:
        if isinstance(attachment.content, dict):
            download_url = attachment.content.get("downloadUrl") or attachment.content.get("url")
            if download_url:
                return str(download_url)
        if attachment.content_url:
            return str(attachment.content_url)
        return ""

    @staticmethod
    def _attachment_name(attachment: Attachment) -> str:
        if attachment.name:
            return str(attachment.name)
        if isinstance(attachment.content, dict):
            content_name = attachment.content.get("name")
            if content_name:
                return str(content_name)
            file_type = str(attachment.content.get("fileType") or "").strip().lower()
            if file_type:
                return f"attachment.{file_type}"
        download_url = str(attachment.content_url or "")
        if download_url:
            path = urlsplit(download_url).path
            candidate = Path(unquote(path)).name
            if candidate:
                return candidate
        return "attachment.bin"

    @staticmethod
    def _attachment_content_type(attachment: Attachment) -> str:
        content_type = str(attachment.content_type or "").strip().lower()
        if content_type and content_type != "application/vnd.microsoft.teams.file.download.info":
            return content_type
        guessed, _encoding = mimetypes.guess_type(GraydazeTrainingBot._attachment_name(attachment))
        return guessed or ""

    @staticmethod
    def _is_image_attachment(*, name: str, content_type: str) -> bool:
        if content_type.startswith("image/"):
            return True
        guessed, _encoding = mimetypes.guess_type(name)
        return bool(guessed and guessed.startswith("image/"))

    @staticmethod
    def _to_data_url(payload: bytes, content_type: str) -> str:
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

    @staticmethod
    def _is_user_file_attachment(attachment: Attachment) -> bool:
        content_type = str(attachment.content_type or "").lower()
        if content_type == "application/vnd.microsoft.card.adaptive":
            return False
        return bool(attachment.content_url or isinstance(attachment.content, dict))

    def _is_feedback_submission(self, activity: Activity) -> bool:
        """Return True when the inbound activity is one of our feedback button clicks."""

        if not activity or not activity.value:
            return False
        if not isinstance(activity.value, dict):
            return False
        return activity.value.get("action") == "feedback"

    def _extract_message_text(self, turn_context: TurnContext) -> str:
        """Normalize a Teams message into clean user query text.

        Teams channel messages often include a bot mention. We strip that so the
        downstream wiki query function receives only the actual user question.
        """

        activity = turn_context.activity
        text = activity.text or ""

        # The SDK helper removes a mention in-place when present. We keep a regex
        # cleanup afterwards as a defensive fallback across Teams surfaces.
        try:
            TurnContext.remove_recipient_mention(activity)
        except Exception:  # pragma: no cover - defensive fallback only
            LOGGER.debug("Failed to remove Teams mention via SDK helper", exc_info=True)

        text = activity.text or text
        text = re.sub(r"<at>.*?</at>", "", text, flags=re.IGNORECASE)
        text = text.replace("&nbsp;", " ")
        return text.strip()

    @staticmethod
    def _is_sync_command(text: str) -> bool:
        normalized = text.strip().lower()
        return normalized == "/sync" or normalized.startswith("/sync ")

    async def _typing_loop(self, turn_context: TurnContext, interval_seconds: float = 3.0) -> None:
        """Send repeated typing indicators until the current operation completes."""

        while True:
            await asyncio.sleep(interval_seconds)
            await turn_context.send_activity(Activity(type=ActivityTypes.typing))

    @staticmethod
    def _extract_tenant_id(channel_data: Any) -> str | None:
        """Safely pull the Teams tenant ID from channel data when available."""

        if isinstance(channel_data, dict):
            tenant = channel_data.get("tenant")
            if isinstance(tenant, dict):
                tenant_id = tenant.get("id")
                if tenant_id:
                    return str(tenant_id)
        return None
