"""Turn what Claude reports about an answer into a dashboard concept row.

Claude tells the connector which wiki page an answer was based on. This module
validates that input strictly, because it is the only free-ish text that lands
in the analytics lists: a page path must look like a wiki page path, and a
title must look like a page title. Anything that reads like a sentence or a
question is rejected, so a misbehaving client cannot smuggle a user's question
text into the ``Title``/``Concept`` columns (the privacy contract in
``teams_bot/services/analytics.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote

from teams_bot.services.analytics import UNKNOWN_CONCEPT
from teams_bot.services.concept_labels import concept_label


# Wiki categories a cited page may live in (see AGENTS.md "Vault Structure").
WIKI_CATEGORIES = ("concepts", "entities", "syntheses", "sources", "queries")

MAX_TITLE_CHARS = 100
MAX_TITLE_WORDS = 14
MAX_FILENAME_CHARS = 120

# Page titles in the vault use words, digits, and light punctuation. No "?",
# no newlines, no quotes-heavy prose.
_TITLE_PATTERN = re.compile(r"^[\w][\w &()'’.,:/+\-–—]*$")
_PATH_PATTERN = re.compile(
    r"(?:^|/)wiki/(?:wiki/)?(" + "|".join(WIKI_CATEGORIES) + r")/([^/]+\.md)$",
    re.IGNORECASE,
)


class ConceptInputError(ValueError):
    """Raised when the reported page path or title is not acceptable."""


@dataclass(frozen=True)
class ConceptRef:
    """One concept row: full title (Title column), dashboard label, page path."""

    title: str
    label: str
    path: str

    @property
    def is_unknown(self) -> bool:
        return self.label == UNKNOWN_CONCEPT


UNKNOWN_REF = ConceptRef(UNKNOWN_CONCEPT, UNKNOWN_CONCEPT, "")


def normalize_page_path(raw: str) -> str:
    """Return ``wiki/<category>/<file>.md`` for a vault path or SharePoint URL.

    Accepts the vault-relative path, the nested ``wiki/wiki/...`` duplicate,
    or a full SharePoint URL containing the path (URL-encoded or not).
    """

    candidate = unquote(raw.strip()).replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
    match = _PATH_PATTERN.search(candidate)
    if not match:
        raise ConceptInputError(
            "page_path must be a wiki page path such as 'wiki/concepts/retainage.md'."
        )
    category, filename = match.group(1).lower(), match.group(2)
    if len(filename) > MAX_FILENAME_CHARS or "\n" in filename:
        raise ConceptInputError("page_path filename is too long.")
    return f"wiki/{category}/{filename}"


def _title_from_path(path: str) -> str:
    stem = PurePosixPath(path).stem
    words = re.split(r"[-_\s]+", stem)
    return " ".join(word.capitalize() if word.islower() else word for word in words if word)


def _validate_title(raw: str) -> str:
    title = re.sub(r"\s+", " ", raw).strip()
    if not title:
        return ""
    if len(title) > MAX_TITLE_CHARS:
        raise ConceptInputError(f"page_title must be at most {MAX_TITLE_CHARS} characters.")
    if len(title.split(" ")) > MAX_TITLE_WORDS:
        raise ConceptInputError(f"page_title must be at most {MAX_TITLE_WORDS} words.")
    if not _TITLE_PATTERN.match(title):
        raise ConceptInputError(
            "page_title must be the wiki page's title (letters, digits, and simple punctuation; "
            "no question marks)."
        )
    return title


def resolve_concept(page_path: str | None, page_title: str | None) -> ConceptRef:
    """Build the concept row for an answer.

    No path and no title means the wiki did not cover the question -> Unknown.
    A title without a path is rejected: the path is what makes labels stable.
    """

    path_text = (page_path or "").strip()
    title_text = (page_title or "").strip()
    if not path_text and not title_text:
        return UNKNOWN_REF
    if title_text.lower() == UNKNOWN_CONCEPT.lower() and not path_text:
        return UNKNOWN_REF
    if not path_text:
        raise ConceptInputError("page_path is required when page_title is given.")

    path = normalize_page_path(path_text)
    title = _validate_title(title_text) or _title_from_path(path)
    label = concept_label(title, path)
    return ConceptRef(title=title, label=label, path=path)
