"""Microsoft Teams activity handler for the Graydaze PM Training Vault bot."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import uuid
from typing import Any

from botbuilder.core import ConversationState, TurnContext, UserState
from botbuilder.core.teams import TeamsActivityHandler
from botbuilder.schema import Activity, ActivityTypes, InvokeResponse

from teams_bot.cards import build_feedback_card
from teams_bot.config import Settings
from teams_bot.services.feedback import FeedbackEvent, FeedbackLogger
from teams_bot.services.wiki_query import WikiIntegrationError, WikiQueryRequest, WikiQueryService


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
    ) -> None:
        super().__init__()
        self._settings = settings
        self._user_state = user_state
        self._conversation_state = conversation_state
        self._wiki_query_service = wiki_query_service
        self._feedback_logger = feedback_logger
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
