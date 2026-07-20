"""Adaptive Card helpers used by the Teams bot."""

from __future__ import annotations

from typing import Any

from botbuilder.core import CardFactory
from botbuilder.schema import Attachment

from teams_bot.markdown_card import markdown_to_adaptive_elements


_PROGRESS_BAR_WIDTH = 20

_PHASE_LABELS = {
    "queued": "Waiting for the sync worker",
    "starting": "Starting",
    "refreshing_wiki": "Refreshing wiki from SharePoint",
    "listing": "Listing source files",
    "processing": "Processing files",
    "indexing": "Rebuilding the search index",
    "done": "Done",
    "cancelled": "Cancelled",
}

# Statuses at which a sync is finished and the card shows a final summary.
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _progress_bar(done: int, total: int) -> str:
    if total <= 0:
        return ""
    ratio = max(0.0, min(1.0, done / total))
    filled = round(ratio * _PROGRESS_BAR_WIDTH)
    return "▓" * filled + "░" * (_PROGRESS_BAR_WIDTH - filled) + f"  {round(ratio * 100)}%"


def _capped_list_items(entries: list[str], *, limit: int = 25) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": text, "wrap": True, "size": "Small"} for text in entries[:limit]
    ]
    if len(entries) > limit:
        items.append(
            {
                "type": "TextBlock",
                "text": f"…and {len(entries) - limit} more",
                "wrap": True,
                "size": "Small",
                "isSubtle": True,
            }
        )
    return items


def build_sync_progress_card(record: dict[str, Any], *, stalled: bool = False) -> Attachment:
    """Render a single, in-place-updatable vault-sync progress card.

    ``record`` is the progress dict served by the ingest API (see
    packages/wiki_core/ingest/progress.py). The same card id is redrawn via
    update_activity as the sync advances, so this must render every state:
    queued, running, stalled, completed, and failed.
    """

    status = str(record.get("status") or "none")
    job_id = str(record.get("job_id") or "")
    requested_by = record.get("requested_by_user_name")
    files_total = int(record.get("files_total") or 0)
    files_done = int(record.get("files_done") or 0)
    updated = int(record.get("updated_files") or 0)
    skipped = int(record.get("skipped_unchanged") or 0)
    failed_files = list(record.get("failed_files") or [])
    empty_files = int(record.get("empty_files") or 0)
    unsupported = dict(record.get("unsupported_files") or {})
    current_file = record.get("current_file")

    body: list[dict[str, Any]] = []

    cancel_requested = bool(record.get("cancel_requested"))
    if status == "completed":
        header = "✅ Vault refresh complete"
    elif status == "failed":
        header = "❌ Vault refresh failed"
    elif status == "cancelled":
        header = "🛑 Vault refresh cancelled"
    elif cancel_requested:
        header = "🛑 Stopping after the current file…"
    elif stalled:
        header = "⚠️ Vault refresh appears stalled"
    elif status in {"queued", "none"}:
        header = "⏳ Vault refresh queued"
    else:
        header = "🔄 Vault refresh in progress"
    body.append({"type": "TextBlock", "text": header, "weight": "Bolder", "size": "Medium", "wrap": True})

    subtitle_bits = []
    if requested_by:
        subtitle_bits.append(f"Requested by {requested_by}")
    if job_id:
        subtitle_bits.append(f"Job {job_id[:8]}")
    if subtitle_bits:
        body.append(
            {"type": "TextBlock", "text": " · ".join(subtitle_bits), "isSubtle": True, "size": "Small", "wrap": True}
        )

    if status not in _TERMINAL_STATUSES:
        bar = _progress_bar(files_done, files_total)
        if bar:
            body.append({"type": "TextBlock", "text": bar, "fontType": "Monospace", "wrap": False})
        phase_label = _PHASE_LABELS.get(str(record.get("phase") or ""), "Working")
        facts = [{"title": "Phase", "value": phase_label}]
        if files_total:
            facts.append({"title": "Files", "value": f"{files_done} / {files_total}"})
        facts.append({"title": "Pages updated", "value": str(updated)})
        if skipped:
            facts.append({"title": "Skipped (unchanged)", "value": str(skipped)})
        if failed_files:
            facts.append({"title": "Failed", "value": str(len(failed_files))})
        body.append({"type": "FactSet", "facts": facts})
        if current_file and not stalled:
            body.append(
                {"type": "TextBlock", "text": f"Current: {current_file}", "size": "Small", "isSubtle": True, "wrap": True}
            )
        if stalled:
            body.append(
                {
                    "type": "TextBlock",
                    "text": "No worker heartbeat for a while — it may be restarting. Still watching.",
                    "size": "Small",
                    "isSubtle": True,
                    "wrap": True,
                }
            )
        else:
            body.append(
                {
                    "type": "TextBlock",
                    "text": "This card refreshes about every 10 seconds.",
                    "size": "Small",
                    "isSubtle": True,
                    "wrap": True,
                }
            )

    if status in _TERMINAL_STATUSES:
        summary_facts = [
            {"title": "Pages updated", "value": str(updated)},
            {"title": "Skipped (unchanged)", "value": str(skipped)},
            {"title": "Empty (no text)", "value": str(empty_files)},
            {"title": "Failed", "value": str(len(failed_files))},
        ]
        body.append({"type": "FactSet", "facts": summary_facts})
        if status == "failed" and record.get("error"):
            body.append(
                {"type": "TextBlock", "text": str(record.get("error")), "wrap": True, "color": "Attention", "size": "Small"}
            )

        needs_separator = True

        def _toggle(title: str, target: str) -> dict[str, Any]:
            nonlocal needs_separator
            action_set: dict[str, Any] = {
                "type": "ActionSet",
                "actions": [{"type": "Action.ToggleVisibility", "title": title, "targetElements": [target]}],
            }
            if needs_separator:
                action_set["separator"] = True
                action_set["spacing"] = "Medium"
                needs_separator = False
            return action_set

        if failed_files:
            body.append(_toggle(f"⚠️ Failed files ({len(failed_files)})", "failedSection"))
            failed_lines = [f"`{entry.get('path', '')}` — {entry.get('error', '')}" for entry in failed_files]
            body.append(
                {"type": "Container", "id": "failedSection", "isVisible": False, "items": _capped_list_items(failed_lines)}
            )

        if unsupported:
            total_unsupported = sum(unsupported.values())
            body.append(_toggle(f"⏭️ Unsupported ({total_unsupported})", "unsupportedSection"))
            unsupported_lines = [f"`{suffix}`: {count}" for suffix, count in sorted(unsupported.items())]
            body.append(
                {
                    "type": "Container",
                    "id": "unsupportedSection",
                    "isVisible": False,
                    "items": _capped_list_items(unsupported_lines),
                }
            )

    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }
    return CardFactory.adaptive_card(card)


def _adaptive_card(body: list[dict[str, Any]], *, actions: list[dict[str, Any]] | None = None) -> Attachment:
    card: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return CardFactory.adaptive_card(card)


def build_admin_confirm_card(
    *,
    title: str,
    facts: list[tuple[str, str]],
    warnings: list[str],
    token: str,
    initiator_name: str | None,
) -> Attachment:
    """Preview + confirm card for a destructive admin action.

    Confirm/Cancel submits carry ``{action, token}`` so the bot can resolve the
    pending action and enforce initiator-only confirmation.
    """
    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True},
    ]
    if facts:
        body.append({"type": "FactSet", "facts": [{"title": t, "value": v} for t, v in facts]})
    for warning in warnings:
        body.append({"type": "TextBlock", "text": f"⚠️ {warning}", "wrap": True, "color": "Attention", "size": "Small"})
    footer = "Only " + (initiator_name or "the requester") + " can confirm. This request expires in 5 minutes."
    body.append({"type": "TextBlock", "text": footer, "wrap": True, "isSubtle": True, "size": "Small"})

    actions = [
        {"type": "Action.Submit", "title": "Confirm", "style": "destructive", "data": {"action": "admin_confirm", "token": token}},
        {"type": "Action.Submit", "title": "Cancel", "data": {"action": "admin_cancel", "token": token}},
    ]
    return _adaptive_card(body, actions=actions)


def build_admin_result_card(title: str, message: str, *, tone: str = "default") -> Attachment:
    """Simple terminal card that replaces a confirm card once resolved."""
    color = {"good": "Good", "warning": "Warning", "attention": "Attention"}.get(tone, "Default")
    body = [
        {"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True, "color": color},
        {"type": "TextBlock", "text": message, "wrap": True},
    ]
    return _adaptive_card(body)


_ADMIN_PHASE_LABELS = {
    "queued": "Waiting for the worker",
    "starting": "Starting",
    "removing": "Removing page",
    "reconciling": "Reconciling the index",
    "pruning_state": "Pruning stale source state",
    "pruning_job_state": "Pruning job history",
    "scanning": "Scanning pages",
    "auditing": "Auditing with the model",
    "reporting": "Writing the report",
    "done": "Done",
    "cancelled": "Cancelled",
}

_ADMIN_JOB_TITLES = {"remove": "Remove page", "clean": "Vault cleanup", "lint": "Vault lint"}


def build_admin_job_card(record: dict[str, Any], *, stalled: bool = False) -> Attachment:
    """Render a remove/clean/lint job's live progress and terminal summary."""
    status = str(record.get("status") or "none")
    job_type = str(record.get("job_type") or "")
    requested_by = record.get("requested_by_user_name")
    result = record.get("result") or {}
    title = _ADMIN_JOB_TITLES.get(job_type, "Admin job")

    if status == "completed":
        header = f"✅ {title} complete"
    elif status == "failed":
        header = f"❌ {title} failed"
    elif status == "cancelled":
        header = f"🛑 {title} cancelled"
    elif stalled:
        header = f"⚠️ {title} appears stalled"
    elif status in {"queued", "none"}:
        header = f"⏳ {title} queued"
    else:
        header = f"🔄 {title} in progress"

    body: list[dict[str, Any]] = [
        {"type": "TextBlock", "text": header, "weight": "Bolder", "size": "Medium", "wrap": True}
    ]
    if requested_by:
        body.append({"type": "TextBlock", "text": f"Requested by {requested_by}", "isSubtle": True, "size": "Small", "wrap": True})

    if status not in _TERMINAL_STATUSES:
        phase = _ADMIN_PHASE_LABELS.get(str(record.get("phase") or ""), "Working")
        body.append({"type": "FactSet", "facts": [{"title": "Phase", "value": phase}]})
        body.append({"type": "TextBlock", "text": "This card refreshes about every 10 seconds.", "isSubtle": True, "size": "Small", "wrap": True})
        return _adaptive_card(body)

    if status == "failed" and record.get("error"):
        body.append({"type": "TextBlock", "text": str(record.get("error")), "wrap": True, "color": "Attention", "size": "Small"})

    body.extend(_admin_result_elements(job_type, result))
    return _adaptive_card(body)


def _admin_result_elements(job_type: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if not result:
        return []
    if job_type == "remove":
        facts = [
            ("Path", str(result.get("path", ""))),
            ("Removed from SharePoint", "yes" if result.get("sharepoint_deleted") else "no"),
            ("Removed locally", "yes" if result.get("local_deleted") else "no"),
            ("Index rows removed", "yes" if result.get("index_rows_deleted") else "no"),
            ("index.md entry removed", "yes" if result.get("index_entry_removed") else "no (hand-edit may be needed)"),
        ]
        return [{"type": "FactSet", "facts": [{"title": t, "value": v} for t, v in facts]}]
    if job_type == "clean":
        facts = [
            ("Pages re-indexed", str(result.get("reindexed", 0))),
            ("Index entries deleted", str(result.get("index_deleted", 0))),
            ("State entries pruned", str(result.get("state_pruned", 0))),
            ("Job history trimmed", str(result.get("job_ids_pruned", 0))),
        ]
        return [{"type": "FactSet", "facts": [{"title": t, "value": v} for t, v in facts]}]
    if job_type == "lint":
        by_type = result.get("by_type") or {}
        facts = [("Pages scanned", str(result.get("pages_scanned", 0))), ("Findings", str(result.get("findings_total", 0)))]
        facts.extend((kind, str(count)) for kind, count in sorted(by_type.items()))
        elements: list[dict[str, Any]] = [{"type": "FactSet", "facts": [{"title": t, "value": v} for t, v in facts]}]
        if result.get("report_path"):
            elements.append({"type": "TextBlock", "text": f"Full report: `{result['report_path']}`", "wrap": True, "size": "Small"})
        return elements
    return []


def _feedback_buttons(request_id: str, concepts: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    # `data` must stay a JSON object (not a string) so Teams merges the card's
    # Input values (the optional comment) into `activity.value` on submit. The
    # matched concepts ride along so feedback rows can be joined to concepts
    # without any server-side request state.
    def _data(feedback: str) -> dict[str, Any]:
        return {
            "action": "feedback",
            "feedback": feedback,
            "request_id": request_id,
            "concepts": list(concepts),
        }

    return [
        {"type": "Action.Submit", "title": "👍 Helpful", "data": _data("helpful")},
        {"type": "Action.Submit", "title": "👎 Inaccurate", "data": _data("inaccurate")},
    ]


def build_answer_card(
    request_id: str,
    answer_markdown: str,
    sources: list[dict[str, Any]] | None = None,
    concepts: tuple[str, ...] = (),
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
                {
                    "type": "Input.Text",
                    "id": "comment",
                    "isMultiline": True,
                    "maxLength": 1000,
                    "placeholder": "Optional: tell us what was helpful or missing",
                },
                {"type": "ActionSet", "actions": _feedback_buttons(request_id, concepts)},
            ],
        }
    )
    body.append(
        {
            "type": "TextBlock",
            "text": "Topic and feedback usage is logged for training analytics — your question text is never stored.",
            "wrap": True,
            "size": "Small",
            "isSubtle": True,
            "spacing": "Small",
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
