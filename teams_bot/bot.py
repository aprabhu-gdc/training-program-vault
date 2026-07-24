"""Microsoft Teams activity handler for the Graydaze PM Training Vault bot."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import mimetypes
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp
from botbuilder.core import ConversationState, TurnContext, UserState
from botbuilder.core.teams import TeamsActivityHandler
from botbuilder.schema import Activity, ActivityTypes, Attachment, InvokeResponse
from botframework.connector.auth import MicrosoftAppCredentials

from packages.contracts.identity import CallerIdentity
from packages.contracts.query import ConversationTurn, QueryAttachment, QueryRequest
from packages.shared.documents.extract_text import SUPPORTED_EXTENSIONS, extract_text
from teams_bot.cards import (
    build_admin_confirm_card,
    build_admin_job_card,
    build_admin_result_card,
    build_answer_card,
    build_sync_progress_card,
)
from teams_bot.commands import COMMANDS, ParsedCommand, parse_command
from teams_bot.config import Settings
from teams_bot.services.admin_preview import (
    RemovePreviewError,
    build_clean_preview,
    build_remove_preview,
)
from teams_bot.services.analytics import AnalyticsService, ConceptMapResolver, derive_concept
from teams_bot.services.concept_labels import concept_label
from teams_bot.services.feedback import FeedbackEvent, FeedbackLogger
from teams_bot.services.ingest_admin_client import HttpIngestAdminClient
from teams_bot.services.pending_actions import PendingActionStore
from teams_bot.services.session_images import SessionImageStore
from teams_bot.services.source_links import SourceLinkResolver
from teams_bot.services.sync_monitor import SyncProgressMonitor
from teams_bot.services.wiki_query import (
    WikiIntegrationError,
    WikiQueryService,
)


LOGGER = logging.getLogger(__name__)


# Azure/OpenAI vision accepts only these image types; anything else (HEIC/HEIF,
# bmp, tiff, ...) is transcoded to JPEG before it reaches the model.
_WEB_NATIVE_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
# Longest-edge cap: downscaling large photos keeps the base64 request small.
_MAX_IMAGE_EDGE = 2000
# Hosts whose attachment URLs are served by the Bot Connector and therefore need
# the bot's bearer token. SharePoint/OneDrive downloadUrls (paperclip uploads)
# are pre-authenticated and must NOT receive the token, so they are excluded.
_BOT_ATTACHMENT_HOST_SUFFIXES = ("botframework.com", "trafficmanager.net", "skype.com")


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
        ingest_admin_client: HttpIngestAdminClient,
        analytics: AnalyticsService | None = None,
        concept_map: ConceptMapResolver | None = None,
        sync_monitor: SyncProgressMonitor | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._user_state = user_state
        self._conversation_state = conversation_state
        self._wiki_query_service = wiki_query_service
        self._feedback_logger = feedback_logger
        self._ingest_admin_client = ingest_admin_client
        self._sync_monitor = sync_monitor or SyncProgressMonitor(ingest_admin_client)
        self._analytics = analytics or AnalyticsService()
        self._concept_map = concept_map or ConceptMapResolver()
        # Strong references keep fire-and-forget analytics tasks alive until done.
        self._analytics_tasks: set[asyncio.Task] = set()
        self._source_links = SourceLinkResolver()
        # Destructive admin actions await confirmation here (in-memory, TTL'd).
        self._pending_actions = PendingActionStore()
        # Short-term, per-conversation chat memory. Stored as a plain dict
        # ({"updated_at": epoch, "turns": [{"role", "text"}]}) so it stays
        # JSON-serializable, and kept text-only (never attachment/image blobs).
        self._history_accessor = self._conversation_state.create_property("History")
        # The most recent image(s) per conversation, held in process memory (not
        # in ConversationState) so a follow-up turn can re-send them to the
        # vision model. Reset after the same idle gap as the text history.
        self._session_images = SessionImageStore()
        # Credentials for downloading Bot Connector-hosted attachments (e.g.
        # pasted/inline images), which require the bot's bearer token. None when
        # app creds are absent (local Emulator) — downloads then run anonymously.
        self._app_credentials = self._build_app_credentials(settings)

    async def on_turn(self, turn_context: TurnContext) -> None:
        """Run the normal activity pipeline and then persist bot state changes."""

        await super().on_turn(turn_context)
        await self._conversation_state.save_changes(turn_context, force=False)
        await self._user_state.save_changes(turn_context, force=False)

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        """Handle a user message, send typing indicators, then answer via the wiki."""

        if self._is_feedback_submission(turn_context.activity):
            await self._handle_feedback(turn_context)
            return

        # Adaptive Card Confirm/Cancel for a destructive admin action.
        if self._is_admin_card_action(turn_context.activity):
            await self._handle_admin_confirmation(turn_context)
            return

        # Slash commands are dispatched before any typing indicator and are NEVER
        # forwarded to the wiki query path (so `/remove …` from a non-admin can't
        # be answered as a hallucinated question).
        query_text = self._extract_message_text(turn_context)
        parsed = parse_command(query_text)
        if parsed is not None:
            await self._dispatch_command(turn_context, parsed)
            return

        # Send typing synchronously first so Teams users get immediate feedback,
        # then continue sending periodic typing activities while the query runs.
        await turn_context.send_activity(Activity(type=ActivityTypes.typing))
        typing_task = asyncio.create_task(self._typing_loop(turn_context))

        try:
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

            # Short-term image memory: remember a freshly attached image, or (on a
            # text-only follow-up within the active session) re-send the most
            # recent one so the vision model can still "see" it.
            now = time.time()
            conversation_key = self._conversation_key(turn_context)
            new_images = [a for a in query_attachments if a.image_data_url]
            effective_attachments = list(query_attachments)
            if new_images:
                self._session_images.remember(conversation_key, new_images, now=now)
            else:
                carried = self._session_images.recent(
                    conversation_key,
                    idle_seconds=self._settings.session_idle_minutes * 60.0,
                    now=now,
                )
                effective_attachments.extend(carried)

            session_history = await self._load_session_history(turn_context)

            request_id = uuid.uuid4().hex[:12]
            request = QueryRequest(
                request_id=request_id,
                query=query_text,
                identity=CallerIdentity(
                    user_id=turn_context.activity.from_property.id if turn_context.activity.from_property else None,
                    user_name=turn_context.activity.from_property.name if turn_context.activity.from_property else None,
                    tenant_id=self._extract_tenant_id(turn_context.activity.channel_data),
                    client_app="teams-bot",
                    channel_id=turn_context.activity.channel_id,
                    conversation_id=(
                        turn_context.activity.conversation.id if turn_context.activity.conversation else None
                    ),
                    locale=turn_context.activity.locale,
                ),
                attachments=tuple(effective_attachments),
                client_context={"channel_data": turn_context.activity.channel_data},
                history=tuple(session_history),
            )

            # Log the query's length, not its text: question content is user data
            # and stays out of app logs (same posture as the analytics pipeline).
            LOGGER.info(
                "Dispatching Teams query request_id=%s user_id=%s conversation_id=%s query_chars=%d",
                request.request_id,
                request.identity.user_id,
                request.identity.conversation_id,
                len(request.query),
            )

            result = await self._wiki_query_service.query(request)

            # The map build reads the whole wiki on a cold/expired cache, so it
            # runs off the event loop. mapping() is fail-soft and never raises.
            source_concepts = await asyncio.to_thread(self._concept_map.mapping)
            diagnostics = getattr(result, "retrieval_diagnostics", None) or {}
            match = derive_concept(
                getattr(result, "citations", ()),
                source_concepts,
                concept_candidates=diagnostics.get("concept_candidates"),
            )
            label = concept_label(match.title, match.path)
            answer_activity = Activity(
                type=ActivityTypes.message,
                text=self._answer_preview(result.answer_text),
                attachments=[
                    build_answer_card(
                        request.request_id,
                        result.answer_text,
                        sources=self._build_source_links(getattr(result, "citations", ())),
                        concepts=(label,),
                    )
                ],
            )
            await turn_context.send_activity(answer_activity)
            # Remember only what the user actually saw (delivered answer text),
            # keeping the running session context available for follow-ups.
            await self._append_session_history(
                turn_context,
                user_text=query_text,
                assistant_text=result.answer_text,
            )
            self._fire_analytics(
                self._analytics.record_query(
                    request_id=request.request_id,
                    user_id=request.identity.user_id,
                    user_name=request.identity.user_name,
                    concept=label,
                    concept_title=match.title,
                )
            )
        except WikiIntegrationError as exc:
            LOGGER.exception("Wiki integration failure", exc_info=exc)
            if getattr(exc, "category", "backend") == "index_not_ready":
                await turn_context.send_activity(
                    "The Vault’s search index is being rebuilt right now. Please try again in a few minutes."
                )
            else:
                await turn_context.send_activity(
                    "I couldn’t reach the Graydaze PM Training Vault right now. Please try again in a moment."
                )
        finally:
            typing_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await typing_task

    def _build_source_links(self, citations: Any) -> list[dict[str, Any]]:
        """Dedupe citations by wiki page and resolve read-only SharePoint links.

        Returns a list of ``{"title", "url"}`` dicts for the Sources card. ``url`` is
        None when a SharePoint link can't be resolved, in which case the card shows
        the title as plain text.
        """

        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for citation in citations or ():
            path = str(getattr(citation, "path", "") or "")
            title = str(getattr(citation, "title", "") or "").strip() or "Untitled"
            key = path or title
            if key in seen:
                continue
            seen.add(key)
            url = self._source_links.link_for(path) if path else None
            sources.append({"title": title, "url": url})
        return sources

    @staticmethod
    def _answer_preview(answer_text: str, limit: int = 90) -> str:
        """Short, non-duplicative preview for the message ``text`` field.

        The full formatted answer lives in the Adaptive Card, so this only drives the Teams
        notification/toast and the accessibility summary. Prefer the first section header (a
        short title) so it doesn't repeat the answer's opening paragraph shown in the card.
        """

        def _clip(text: str) -> str:
            text = text.strip()
            return text if len(text) <= limit else text[:limit].rstrip() + "…"

        lines = [ln.strip() for ln in (answer_text or "").splitlines() if ln.strip()]
        for line in lines:
            heading = re.match(r"^#{1,6}\s+(.*\S)\s*$", line)
            if heading:
                return _clip(re.sub(r"[*_`]+", "", heading.group(1)))
        for line in lines:
            plain = re.sub(r"[*_`]+", "", re.sub(r"^[-*>]\s+", "", re.sub(r"^#{1,6}\s*", "", line)))
            if plain.strip():
                return _clip(plain)
        return "Answer from the PM Training Vault."

    async def on_invoke_activity(self, turn_context: TurnContext) -> InvokeResponse:
        """Handle invoke-style submissions such as some Adaptive Card actions.

        Teams can deliver card actions either as message activities or invoke
        activities depending on client surface and card behavior. Supporting both
        keeps the feedback buttons reliable.
        """

        if self._is_feedback_submission(turn_context.activity):
            await self._handle_feedback(turn_context)
            return InvokeResponse(status=200)

        if self._is_admin_card_action(turn_context.activity):
            await self._handle_admin_confirmation(turn_context)
            return InvokeResponse(status=200)

        return await super().on_invoke_activity(turn_context)

    # --- Conversation memory (in-process, per-conversation) ------------------

    @staticmethod
    def _conversation_key(turn_context: TurnContext) -> str:
        """Stable per-conversation key (channel + conversation id).

        Mirrors how ``ConversationState`` scopes the text history so the image
        memory and the text memory share the same conversation boundary.
        """

        activity = turn_context.activity
        channel = activity.channel_id or ""
        conversation = activity.conversation.id if activity.conversation else ""
        return f"{channel}:{conversation}"

    async def _load_session_history(self, turn_context: TurnContext) -> list[ConversationTurn]:
        """Return recent turns for this conversation, or [] if the session is idle.

        A "session" ends after ``session_idle_minutes`` of inactivity: stale
        history is dropped so an unrelated later question starts clean.
        """

        record = await self._history_accessor.get(turn_context, None)
        if not isinstance(record, dict) or not isinstance(record.get("turns"), list):
            return []

        updated_at = record.get("updated_at")
        idle_limit = self._settings.session_idle_minutes * 60.0
        if (
            idle_limit > 0
            and isinstance(updated_at, (int, float))
            and (time.time() - updated_at) > idle_limit
        ):
            await self._history_accessor.set(turn_context, {"updated_at": time.time(), "turns": []})
            return []

        turns: list[ConversationTurn] = []
        for item in record["turns"]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            text = str(item.get("text") or "").strip()
            if role in {"user", "assistant"} and text:
                turns.append(ConversationTurn(role=role, text=text))
        return turns

    async def _append_session_history(
        self,
        turn_context: TurnContext,
        *,
        user_text: str,
        assistant_text: str,
    ) -> None:
        """Append the just-completed exchange and trim to the configured caps."""

        record = await self._history_accessor.get(turn_context, None)
        turns: list[dict[str, str]] = []
        if isinstance(record, dict) and isinstance(record.get("turns"), list):
            turns = [item for item in record["turns"] if isinstance(item, dict)]

        user_text = (user_text or "").strip()
        assistant_text = (assistant_text or "").strip()
        if user_text:
            turns.append({"role": "user", "text": user_text})
        if assistant_text:
            turns.append({"role": "assistant", "text": assistant_text})

        await self._history_accessor.set(
            turn_context,
            {"updated_at": time.time(), "turns": self._trim_history(turns)},
        )

    def _trim_history(self, turns: list[dict[str, str]]) -> list[dict[str, str]]:
        """Cap history by turn count, then by total character budget (recent-first)."""

        max_turns = max(0, self._settings.history_max_turns)
        if max_turns and len(turns) > max_turns:
            turns = turns[-max_turns:]

        budget = max(0, self._settings.history_char_budget)
        if not budget:
            return turns

        kept: list[dict[str, str]] = []
        running = 0
        for turn in reversed(turns):
            length = len(str(turn.get("text") or ""))
            if kept and running + length > budget:
                break
            running += length
            kept.append(turn)
        return list(reversed(kept))

    async def _handle_feedback(self, turn_context: TurnContext) -> None:
        """Log user feedback from the Adaptive Card buttons and acknowledge it."""

        value = turn_context.activity.value or {}
        feedback = str(value.get("feedback", "unknown")).strip().lower()
        request_id = str(value.get("request_id", "unknown")).strip()
        # `comment` comes from the card's Input.Text; `concepts` rides in the
        # Action.Submit data. Both default safely for cards sent before this
        # feature existed (they linger in chat history indefinitely).
        comment = str(value.get("comment") or "").strip()[:1000]
        raw_concepts = value.get("concepts")
        concepts = (
            tuple(str(item).strip() for item in raw_concepts if str(item).strip())
            if isinstance(raw_concepts, list)
            else ()
        )
        event = FeedbackEvent(
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
            comment=comment,
            concepts=concepts,
        )
        await self._feedback_logger.log(event)
        self._fire_analytics(
            self._analytics.record_feedback(
                request_id=event.request_id,
                user_id=event.user_id,
                user_name=event.user_name,
                rating=event.feedback,
                comment=event.comment,
                concepts=event.concepts,
            )
        )

        await turn_context.send_activity("Thanks for the feedback.")

    def _fire_analytics(self, coro: Any) -> None:
        """Run an analytics coroutine in the background, never blocking a reply."""

        task = asyncio.create_task(coro)
        self._analytics_tasks.add(task)

        def _done(finished: asyncio.Task) -> None:
            self._analytics_tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                LOGGER.warning("Analytics task failed", exc_info=finished.exception())

        task.add_done_callback(_done)

    async def _handle_sync_command(self, turn_context: TurnContext) -> None:
        """Trigger a manual SharePoint sync and post a live-updating progress card.

        The sync itself runs in a separate worker process, so a background monitor
        polls its status and redraws the card in place until the sync finishes.
        """

        user_name = (
            turn_context.activity.from_property.name if turn_context.activity.from_property else None
        )
        try:
            result = await self._ingest_admin_client.request_manual_sync(
                requested_by_user_id=(
                    turn_context.activity.from_property.id if turn_context.activity.from_property else None
                ),
                requested_by_user_name=user_name,
            )
        except Exception:
            LOGGER.exception("Manual Teams sync failed")
            await turn_context.send_activity(
                "The SharePoint refresh could not be queued. Check the ingest service and app logs for details."
            )
            return

        if result.already_running:
            record = result.progress or {"status": "running", "job_id": result.job_id}
            started_by = record.get("requested_by_user_name")
            note = "A vault refresh is already in progress"
            if started_by:
                note += f" (started by {started_by})"
            await turn_context.send_activity(note + ". Here’s its live status:")
        else:
            record = {
                "status": "queued",
                "phase": "queued",
                "job_id": result.job_id,
                "requested_by_user_name": user_name,
            }

        response = await turn_context.send_activity(
            Activity(type=ActivityTypes.message, attachments=[build_sync_progress_card(record)])
        )

        activity_id = getattr(response, "id", None)
        if activity_id and result.job_id:
            self._sync_monitor.start(
                job_id=result.job_id,
                adapter=turn_context.adapter,
                app_id=self._settings.app_id,
                conversation_reference=TurnContext.get_conversation_reference(turn_context.activity),
                activity_id=activity_id,
            )

    async def _extract_query_attachments(
        self,
        attachments: list[Attachment],
    ) -> tuple[tuple[QueryAttachment, ...], tuple[str, ...]]:
        """Download and normalize supported Teams attachments for the query backend."""

        processed: list[QueryAttachment] = []
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
    ) -> QueryAttachment | None:
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
            prepared = self._prepare_image(payload=payload, name=name, content_type=content_type)
            if prepared is None:
                LOGGER.info(
                    "Skipping image we couldn't prepare for the vision model name=%s content_type=%s",
                    name,
                    content_type,
                )
                return None
            image_bytes, image_content_type = prepared
            return QueryAttachment(
                name=name,
                content_type=image_content_type,
                image_data_url=self._to_data_url(image_bytes, image_content_type),
            )

        extracted_text = await self._extract_attachment_text(
            name=name,
            content_type=content_type,
            payload=payload,
        )
        if not extracted_text:
            return None

        return QueryAttachment(
            name=name,
            content_type=content_type or "application/octet-stream",
            text_content=extracted_text,
        )

    async def _download_attachment(self, download_url: str) -> bytes:
        headers: dict[str, str] = {}
        if self._app_credentials is not None and self._requires_bot_token(download_url):
            # Inline/pasted Teams images live behind the Bot Connector and 401
            # without the bot token. SharePoint downloadUrls are excluded by host.
            try:
                MicrosoftAppCredentials.trust_service_url(download_url)
                token = await asyncio.to_thread(self._app_credentials.get_access_token)
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            except Exception:
                LOGGER.warning(
                    "Failed to obtain bot token for attachment download; retrying anonymously",
                    exc_info=True,
                )

        timeout = aiohttp.ClientTimeout(total=self._settings.wiki_query_timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(download_url, headers=headers) as response:
                response.raise_for_status()
                return await response.read()

    @staticmethod
    def _build_app_credentials(settings: Settings) -> MicrosoftAppCredentials | None:
        """Build connector credentials from the bot's app id/password, if present."""

        if not settings.app_id or not settings.app_password:
            return None
        try:
            tenant = (
                settings.app_tenant_id
                if settings.app_type.strip().lower() == "singletenant"
                else None
            )
            return MicrosoftAppCredentials(
                settings.app_id, settings.app_password, channel_auth_tenant=tenant or None
            )
        except Exception:
            LOGGER.warning(
                "Could not build MicrosoftAppCredentials; attachment downloads will be anonymous",
                exc_info=True,
            )
            return None

    @staticmethod
    def _requires_bot_token(url: str) -> bool:
        host = (urlsplit(url).hostname or "").lower()
        return any(
            host == suffix or host.endswith("." + suffix)
            for suffix in _BOT_ATTACHMENT_HOST_SUFFIXES
        )

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
        # .heic/.heif are common phone-photo formats that Python's mimetypes DB
        # often doesn't map, so detect them explicitly.
        if Path(name).suffix.lower() in {".heic", ".heif"}:
            return True
        guessed, _encoding = mimetypes.guess_type(name)
        return bool(guessed and guessed.startswith("image/"))

    def _prepare_image(
        self,
        *,
        payload: bytes,
        name: str,
        content_type: str,
    ) -> tuple[bytes, str] | None:
        """Normalize an image for the vision model.

        Returns ``(bytes, content_type)`` ready to send, or ``None`` when the
        image can't be made model-ready (e.g. HEIC with no transcoder available).
        png/jpeg/gif/webp within the size cap pass through untouched; everything
        else (HEIC/HEIF, bmp, tiff, oversized photos) is transcoded/downscaled.
        """

        normalized_type = (content_type or "").split(";")[0].strip().lower()
        if not normalized_type or normalized_type == "application/octet-stream":
            normalized_type = (mimetypes.guess_type(name)[0] or "").lower()

        suffix = Path(name).suffix.lower()
        needs_transcode = (
            suffix in {".heic", ".heif"}
            or normalized_type not in _WEB_NATIVE_IMAGE_TYPES
        )

        processed = self._process_image_bytes(payload, force_jpeg=needs_transcode)
        if processed is not None:
            return processed

        # Pillow unavailable or failed. A web-native format can still be sent
        # as-is; anything requiring transcode has to be skipped.
        if not needs_transcode and normalized_type in _WEB_NATIVE_IMAGE_TYPES:
            return payload, normalized_type
        return None

    @staticmethod
    def _process_image_bytes(payload: bytes, *, force_jpeg: bool) -> tuple[bytes, str] | None:
        """Decode, optionally downscale, and re-encode an image via Pillow.

        Returns ``None`` to mean "no processing applied" — either the source is
        already an acceptable web-native image within the size cap, or Pillow
        (and the HEIF plugin) isn't available. Callers treat ``None`` per their
        own fallback rules.
        """

        try:
            import io

            from PIL import Image

            try:  # HEIC/HEIF support is an optional plugin.
                import pillow_heif

                pillow_heif.register_heif_opener()
            except Exception:  # pragma: no cover - only when plugin missing
                pass

            with Image.open(io.BytesIO(payload)) as image:
                image.load()
                fmt = (image.format or "").upper()
                width, height = image.size
                longest = max(width, height)
                needs_resize = longest > _MAX_IMAGE_EDGE
                already_web = fmt in {"PNG", "JPEG", "GIF", "WEBP"}

                if already_web and not force_jpeg and not needs_resize:
                    return None  # pass the original bytes through unchanged

                if needs_resize:
                    scale = _MAX_IMAGE_EDGE / float(longest)
                    image = image.resize(
                        (max(1, int(width * scale)), max(1, int(height * scale)))
                    )

                buffer = io.BytesIO()
                if force_jpeg or fmt not in {"PNG", "GIF", "WEBP"}:
                    image.convert("RGB").save(buffer, format="JPEG", quality=85)
                    return buffer.getvalue(), "image/jpeg"
                image.save(buffer, format=fmt)
                return buffer.getvalue(), f"image/{fmt.lower()}"
        except Exception:
            LOGGER.warning("Image processing failed; falling back", exc_info=True)
            return None

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

    # --- Command dispatch ----------------------------------------------------

    @staticmethod
    def _sender_identity(turn_context: TurnContext) -> tuple[str | None, str | None]:
        prop = turn_context.activity.from_property
        aad = getattr(prop, "aad_object_id", None) if prop else None
        name = prop.name if prop else None
        return (aad.lower() if aad else None), name

    def _is_admin(self, turn_context: TurnContext) -> bool:
        aad, _ = self._sender_identity(turn_context)
        return bool(self._settings.admin_object_ids) and aad is not None and aad in self._settings.admin_object_ids

    @staticmethod
    def _is_admin_card_action(activity: Activity) -> bool:
        return bool(
            activity
            and isinstance(activity.value, dict)
            and activity.value.get("action") in {"admin_confirm", "admin_cancel"}
        )

    async def _dispatch_command(self, turn_context: TurnContext, parsed: ParsedCommand) -> None:
        spec = parsed.spec
        if spec is None:
            await turn_context.send_activity(f"Unknown command `/{parsed.raw_name}`. Try /help.")
            return

        if spec.name == "help":
            await self._handle_help(turn_context)
            return
        if spec.name == "whoami":
            await self._handle_whoami(turn_context)
            return

        # Everything below is admin-only.
        aad, _name = self._sender_identity(turn_context)
        if not self._is_admin(turn_context):
            if not self._settings.admin_object_ids:
                await turn_context.send_activity(
                    "Admin commands are disabled: no admin allowlist is configured (BOT_ADMIN_OBJECT_IDS)."
                )
            else:
                await turn_context.send_activity(
                    "That’s an admin-only command. Ask a vault admin, or use /whoami to share your ID with them."
                )
            LOGGER.info("Denied admin command command=%s aad_object_id=%s", spec.name, aad)
            return

        LOGGER.info("Admin command dispatched command=%s aad_object_id=%s", spec.name, aad)
        if spec.name == "sync":
            await self._handle_sync_command(turn_context)
        elif spec.name == "stopsync":
            await self._handle_stopsync_command(turn_context)
        elif spec.name == "remove":
            await self._handle_remove(turn_context, parsed.args)
        elif spec.name == "clean":
            await self._handle_clean(turn_context)
        elif spec.name == "lint":
            await self._handle_lint(turn_context)

    async def _handle_whoami(self, turn_context: TurnContext) -> None:
        prop = turn_context.activity.from_property
        aad = getattr(prop, "aad_object_id", None) if prop else None
        name = (prop.name if prop else None) or "there"
        if aad:
            message = (
                f"**{name}**\n\nYour Entra object ID:\n`{aad}`\n\n"
                "To grant admin access, add this ID to the `BOT_ADMIN_OBJECT_IDS` app setting."
            )
        else:
            fallback = (prop.id if prop else None) or "unknown"
            message = (
                f"**{name}**\n\nI can’t see your Entra object ID on this surface "
                f"(common in the Bot Framework Emulator). Channel identity: `{fallback}`."
            )
        await turn_context.send_activity(message)

    async def _handle_help(self, turn_context: TurnContext) -> None:
        lines = ["**Graydaze PM Training Vault — commands**", ""]
        for spec in COMMANDS.values():
            tag = " _(admin)_" if spec.admin_only else ""
            lines.append(f"- `{spec.usage}` — {spec.description}{tag}")
        lines.append("")
        lines.append("Or just ask a training question in plain language.")
        await turn_context.send_activity("\n".join(lines))

    async def _handle_stopsync_command(self, turn_context: TurnContext) -> None:
        _aad, user_name = self._sender_identity(turn_context)
        try:
            result = await self._ingest_admin_client.request_cancel(requested_by_user_name=user_name)
        except Exception:
            LOGGER.exception("Teams stopsync failed")
            await turn_context.send_activity(
                "I couldn’t reach the ingest service to stop the refresh. Check the app logs for details."
            )
            return

        if result.no_active_sync:
            await turn_context.send_activity("No vault refresh is running right now. Use /sync to start one.")
        elif result.cancelled_stale:
            await turn_context.send_activity(
                "The last refresh had stopped responding (no worker heartbeat), so I’ve marked it cancelled. "
                "You can start a fresh one with /sync."
            )
        elif (result.progress or {}).get("status") == "queued":
            await turn_context.send_activity("Cancelled the queued refresh before it started.")
        else:
            await turn_context.send_activity(
                "Stop requested. The refresh will finish the file it’s currently processing and then stop — "
                "this can take a few minutes for a large file. The progress card will update when it’s done."
            )

    async def _handle_remove(self, turn_context: TurnContext, args: str) -> None:
        if not args:
            await turn_context.send_activity("Usage: `/remove wiki/path/to/page.md`")
            return
        try:
            preview = await asyncio.to_thread(build_remove_preview, args)
        except RemovePreviewError as exc:
            await turn_context.send_activity(str(exc))
            return
        except Exception:
            LOGGER.exception("Remove preview failed for arg=%s", args)
            await turn_context.send_activity("I couldn’t build a preview for that page. Check the app logs.")
            return

        aad, name = self._sender_identity(turn_context)
        conversation_id = turn_context.activity.conversation.id if turn_context.activity.conversation else None
        action = self._pending_actions.create(
            command="remove",
            payload={"path": preview.relative_path},
            initiator_aad_object_id=aad or "",
            initiator_name=name,
            conversation_id=conversation_id,
        )
        card = build_admin_confirm_card(
            title=f"Remove `{preview.relative_path}`?",
            facts=preview.facts,
            warnings=preview.warnings,
            token=action.token,
            initiator_name=name,
        )
        response = await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[card]))
        action.payload["preview_activity_id"] = getattr(response, "id", None)

    async def _handle_clean(self, turn_context: TurnContext) -> None:
        try:
            preview = await asyncio.to_thread(build_clean_preview)
        except Exception:
            LOGGER.warning("Clean preview failed; submitting without a preview", exc_info=True)
            preview = None

        if preview and preview.will_delete:
            aad, name = self._sender_identity(turn_context)
            conversation_id = turn_context.activity.conversation.id if turn_context.activity.conversation else None
            action = self._pending_actions.create(
                command="clean",
                payload={},
                initiator_aad_object_id=aad or "",
                initiator_name=name,
                conversation_id=conversation_id,
            )
            doomed = ", ".join(f"`{p}`" for p in preview.delete_paths[:25])
            card = build_admin_confirm_card(
                title="Run vault cleanup?",
                facts=preview.facts,
                warnings=[f"{len(preview.delete_paths)} stale index entries will be deleted: {doomed}"],
                token=action.token,
                initiator_name=name,
            )
            response = await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[card]))
            action.payload["preview_activity_id"] = getattr(response, "id", None)
            return

        note = (
            "Index looks clean — running a hygiene pass to prune stale state…"
            if preview
            else "Running a vault hygiene pass…"
        )
        await turn_context.send_activity(note)
        await self._submit_admin_job(turn_context, job_type="clean")

    async def _handle_lint(self, turn_context: TurnContext) -> None:
        await turn_context.send_activity("Starting a vault lint — I’ll post progress here.")
        await self._submit_admin_job(turn_context, job_type="lint")

    async def _submit_admin_job(
        self,
        turn_context: TurnContext,
        *,
        job_type: str,
        payload: dict[str, Any] | None = None,
        replace_activity_id: str | None = None,
    ) -> None:
        _aad, user_name = self._sender_identity(turn_context)
        try:
            result = await self._ingest_admin_client.request_admin_job(
                job_type=job_type,
                payload=payload,
                requested_by_user_id=(
                    turn_context.activity.from_property.id if turn_context.activity.from_property else None
                ),
                requested_by_user_name=user_name,
            )
        except Exception:
            LOGGER.exception("Admin job submit failed job_type=%s", job_type)
            await turn_context.send_activity(
                "The job could not be queued. Check the ingest service and app logs for details."
            )
            return

        if result.status == "sync_running":
            await turn_context.send_activity(
                "A vault refresh is running right now; try again once it finishes (or /stopsync it first)."
            )
            return
        if result.status == "already_running":
            await turn_context.send_activity("Another maintenance job is already running. Here’s its status:")

        record = result.progress or {
            "status": "queued",
            "phase": "queued",
            "job_id": result.job_id,
            "job_type": job_type,
        }
        record.setdefault("job_type", job_type)
        card = build_admin_job_card(record)

        activity_id = replace_activity_id
        if activity_id:
            await self._update_activity_card(turn_context, card, activity_id=activity_id)
        else:
            response = await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[card]))
            activity_id = getattr(response, "id", None)

        if activity_id and result.job_id:
            self._sync_monitor.start(
                job_id=result.job_id,
                adapter=turn_context.adapter,
                app_id=self._settings.app_id,
                conversation_reference=TurnContext.get_conversation_reference(turn_context.activity),
                activity_id=activity_id,
                fetch_status=self._ingest_admin_client.get_admin_job_status,
                build_card=build_admin_job_card,
            )

    async def _handle_admin_confirmation(self, turn_context: TurnContext) -> None:
        value = turn_context.activity.value or {}
        action_kind = value.get("action")
        token = str(value.get("token") or "")
        pending = self._pending_actions.pop(token)
        if pending is None:
            await turn_context.send_activity(
                "This confirmation expired or was already handled. Run the command again if you still need it."
            )
            return

        aad, name = self._sender_identity(turn_context)
        if not self._is_admin(turn_context) or (aad or "") != pending.initiator_aad_object_id:
            # Only the original admin may resolve their own pending action.
            self._pending_actions.put_back(pending)
            await turn_context.send_activity(
                f"Only {pending.initiator_name or 'the requester'} can confirm or cancel this action."
            )
            return

        preview_activity_id = pending.payload.get("preview_activity_id")
        if action_kind == "admin_cancel":
            await self._update_activity_card(
                turn_context,
                build_admin_result_card("Cancelled", f"Cancelled by {name or 'admin'}.", tone="warning"),
                activity_id=preview_activity_id,
            )
            return

        payload = {"path": pending.payload["path"]} if pending.command == "remove" else {}
        await self._submit_admin_job(
            turn_context,
            job_type=pending.command,
            payload=payload,
            replace_activity_id=preview_activity_id,
        )

    async def _update_activity_card(
        self,
        turn_context: TurnContext,
        card: Attachment,
        *,
        activity_id: str | None = None,
    ) -> None:
        target_id = activity_id or turn_context.activity.reply_to_id
        if not target_id:
            await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[card]))
            return
        activity = Activity(id=target_id, type=ActivityTypes.message, attachments=[card])
        try:
            await turn_context.update_activity(activity)
        except Exception:
            LOGGER.warning("Failed to update card in place; sending a new one", exc_info=True)
            await turn_context.send_activity(Activity(type=ActivityTypes.message, attachments=[card]))

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
