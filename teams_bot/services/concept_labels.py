"""Short, dashboard-friendly labels for wiki concepts.

The analytics dashboard stores a SHORT label per query (e.g. ``ETC``, ``RAMP``)
rather than the full concept title, so bar-chart axes stay readable. Labels are
resolved by concept page slug so they stay stable if a page's title is reworded.

To add a label for a new concept, add its slug here. Anything without an
override falls back to a heuristic (acronym / leading token), so the pipeline
keeps working for concepts added later — but curated overrides read best.

Follow-up (deferred): a ``label:`` field in concept-page frontmatter would let
the wiki own its own labels; that needs plumbing through the page model, the
index row schema, and the concept-candidate diagnostics, so it is not wired yet.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from teams_bot.services.analytics import UNKNOWN_CONCEPT


# Keyed by the concept page slug (wiki/concepts/<slug>.md stem).
CONCEPT_LABEL_OVERRIDES: dict[str, str] = {
    "estimate-to-complete": "ETC",
    "ramp-credit-card-coding": "RAMP",
    "graydaze-project-manager-role": "PM Role",
    "mission-support": "Mission Support",
    "field-execution-basics": "Field Execution",
    "graydaze-operating-principles": "Principles",
    "graydaze-training-program": "Training",
    "indexing-and-logging": "Indexing",
    "ingest": "Ingest",
    "lint": "Lint",
    "llm-wiki": "LLM Wiki",
    "query": "Query",
}

MAX_LABEL_CHARS = 20


def _slug_from_path(path: str) -> str:
    if not path:
        return ""
    return PurePosixPath(path).stem


def _heuristic_label(title: str) -> str:
    """Best-effort short label for a concept with no explicit override.

    Order: a leading all-caps term of art (e.g. "RAMP ...") wins; otherwise a
    3+-word title becomes an initials acronym ("Estimate to Complete" -> "ETC");
    a 1-2 word title passes through. A leading "Graydaze" org prefix is dropped
    first so it doesn't pollute the acronym.
    """

    cleaned = re.sub(r"[^\w\s-]", " ", title).strip()
    if not cleaned:
        return title.strip()[:MAX_LABEL_CHARS]

    tokens = cleaned.split()
    if tokens and tokens[0].isupper() and len(tokens[0]) >= 2:
        return tokens[0][:MAX_LABEL_CHARS]

    if len(tokens) > 1 and tokens[0].lower() == "graydaze":
        tokens = tokens[1:]

    if len(tokens) >= 3:
        return "".join(word[0].upper() for word in tokens)[:MAX_LABEL_CHARS]

    return " ".join(tokens[:3])[:MAX_LABEL_CHARS]


def concept_label(title: str, path: str = "") -> str:
    """Return the short dashboard label for a concept.

    Resolution order: explicit override by page slug, then a heuristic on the
    title. ``Unknown`` (and empty titles) pass through unchanged so the
    dashboard's Unknown bucket is preserved.
    """

    if not title or title == UNKNOWN_CONCEPT:
        return title or UNKNOWN_CONCEPT

    slug = _slug_from_path(path)
    if slug in CONCEPT_LABEL_OVERRIDES:
        return CONCEPT_LABEL_OVERRIDES[slug]

    return _heuristic_label(title)
