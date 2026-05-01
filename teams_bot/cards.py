"""Adaptive Card helpers used by the Teams bot."""

from __future__ import annotations

from botbuilder.core import CardFactory
from botbuilder.schema import Attachment


def build_feedback_card(request_id: str) -> Attachment:
    """Create a tiny Adaptive Card with helpful / inaccurate buttons.

    The card intentionally keeps the payload small and only sends a request ID +
    feedback value back to the bot. The request ID lets operators correlate the
    feedback event with the original query log entry.
    """

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {
                "type": "TextBlock",
                "text": "Was this answer helpful?",
                "wrap": True,
                "size": "Small",
                "isSubtle": True,
            }
        ],
        "actions": [
            {
                "type": "Action.Submit",
                "title": "👍 Helpful",
                "data": {
                    "action": "feedback",
                    "feedback": "helpful",
                    "request_id": request_id,
                },
            },
            {
                "type": "Action.Submit",
                "title": "👎 Inaccurate",
                "data": {
                    "action": "feedback",
                    "feedback": "inaccurate",
                    "request_id": request_id,
                },
            },
        ],
    }
    return CardFactory.adaptive_card(card)
