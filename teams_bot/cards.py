"""Adaptive Card helpers used by the Teams bot."""

from __future__ import annotations

from typing import Any

from botbuilder.core import CardFactory
from botbuilder.schema import Attachment


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


def build_answer_card(request_id: str, sources: list[dict[str, Any]] | None = None) -> Attachment:
    """Card attached beneath an answer with collapsed Sources and Feedback sections.

    Both sections are hidden by default and revealed via ``Action.ToggleVisibility``
    so the answer text stays clean and uncluttered. ``sources`` is a list of
    ``{"title": str, "url": str | None}`` dicts; a URL renders as a read-only link,
    otherwise the title is shown as plain text. The feedback buttons keep the same
    ``data`` payload the bot's feedback handler expects.
    """

    sources = sources or []
    body: list[dict[str, Any]] = []

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
        body.append(
            {
                "type": "ActionSet",
                "actions": [
                    {
                        "type": "Action.ToggleVisibility",
                        "title": f"📎 Sources ({len(sources)})",
                        "targetElements": ["sourcesSection"],
                    }
                ],
            }
        )
        body.append({"type": "Container", "id": "sourcesSection", "isVisible": False, "items": source_items})

    body.append(
        {
            "type": "ActionSet",
            "actions": [
                {
                    "type": "Action.ToggleVisibility",
                    "title": "💬 Feedback",
                    "targetElements": ["feedbackSection"],
                }
            ],
        }
    )
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
        "body": body,
    }
    return CardFactory.adaptive_card(card)
