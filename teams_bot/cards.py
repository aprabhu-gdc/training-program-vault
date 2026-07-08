"""Adaptive Card helpers used by the Teams bot."""

from __future__ import annotations

from typing import Any

from botbuilder.core import CardFactory
from botbuilder.schema import Attachment

from teams_bot.markdown_card import markdown_to_adaptive_elements


def _feedback_buttons(request_id: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "Action.Submit",
            "title": "👍 Helpful",
            "data": {"action": "feedback", "feedback": "helpful", "request_id": request_id},
        },
        {
            "type": "Action.Submit",
            "title": "👎 Inaccurate",
            "data": {"action": "feedback", "feedback": "inaccurate", "request_id": request_id},
        },
    ]


def build_answer_card(
    request_id: str,
    answer_markdown: str,
    sources: list[dict[str, Any]] | None = None,
) -> Attachment:
    """Adaptive Card rendering the answer plus collapsed Sources and Feedback sections.

    The answer Markdown is converted to card elements (headings, paragraphs, lists) so it
    renders with real visual hierarchy on desktop and mobile. The Sources and Feedback
    sections are hidden by default and revealed via ``Action.ToggleVisibility``. ``sources``
    is a list of ``{"title": str, "url": str | None}`` dicts; a URL renders as a read-only
    link, otherwise the title is plain text. The feedback buttons keep the same ``data``
    payload the bot's feedback handler expects.
    """

    sources = sources or []
    body: list[dict[str, Any]] = list(markdown_to_adaptive_elements(answer_markdown))

    # The first toggle gets a separator so the actions are divided from the answer body.
    needs_separator = True

    def _toggle(title: str, target: str) -> dict[str, Any]:
        nonlocal needs_separator
        action_set: dict[str, Any] = {
            "type": "ActionSet",
            "actions": [
                {"type": "Action.ToggleVisibility", "title": title, "targetElements": [target]}
            ],
        }
        if needs_separator:
            action_set["separator"] = True
            action_set["spacing"] = "Medium"
            needs_separator = False
        return action_set

    if sources:
        source_items = [
            {
                "type": "TextBlock",
                "text": (f"[{s.get('title') or 'Untitled'}]({s['url']})" if s.get("url") else str(s.get("title") or "Untitled")),
                "wrap": True,
                "size": "Small",
            }
            for s in sources
        ]
        body.append(_toggle(f"📎 Sources ({len(sources)})", "sourcesSection"))
        body.append({"type": "Container", "id": "sourcesSection", "isVisible": False, "items": source_items})

    body.append(_toggle("💬 Feedback", "feedbackSection"))
    body.append(
        {
            "type": "Container",
            "id": "feedbackSection",
            "isVisible": False,
            "items": [
                {
                    "type": "TextBlock",
                    "text": "Was this answer helpful?",
                    "wrap": True,
                    "size": "Small",
                    "isSubtle": True,
                },
                {"type": "ActionSet", "actions": _feedback_buttons(request_id)},
            ],
        }
    )

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        # Teams-specific: render the card at the full available message width.
        "msteams": {"width": "Full"},
        "body": body,
    }
    return CardFactory.adaptive_card(card)
