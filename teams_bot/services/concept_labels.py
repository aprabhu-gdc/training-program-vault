"""Readable, dashboard-friendly labels for wiki concepts.

The analytics dashboard stores one label per query in the ``Concept`` column,
which drives the "Questions by concept" bar chart. Labels aim for a balance
between detail and concision so dashboard viewers don't have to guess what a
topic is:

* Prefer the readable concept name (e.g. ``Joint Filler Replacement``,
  ``Safety Near Live Power Lines``) over an invented initialism.
* Only use an acronym when the wiki itself refers to the topic that way.
  Phrase-acronyms are spelled out with the acronym in parentheses
  (``Estimate to Complete (ETC)``); proper-noun / product acronyms stay as-is
  (``RAMP``, ``OSHA``, ``DOWSIL``, ``RS88``, ``MM EP90``, ``LLM``, ``ACI``).

Labels are resolved by concept page slug so they stay stable if a page's title
is reworded. To add a label for a new concept, add its slug to
``CONCEPT_LABEL_OVERRIDES``. Anything without an override falls back to a
readable heuristic (drop an org/``The`` prefix, then pass the title through,
truncating overly long titles) so the pipeline keeps working for concepts added
later — but curated overrides read best.

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
    "apron-joint-sealing": "Apron Joint Sealing",
    "aci-joint-filler-depth": "ACI Joint Filler Depth",
    "change-orders": "Change Orders",
    "coatings": "Coatings",
    "drum-pump-sealant-application": "Drum Pump Sealant Application",
    "commercial-repaint-estimating": "Commercial Repaint Estimating",
    "dowsil-all-guard-silicone-elastomeric-coating": "DOWSIL All Guard Coating",
    "dump-profit": "Dump Profit",
    "emseal-joints": "Emseal Joints",
    "estimate-to-complete": "Estimate to Complete (ETC)",
    "estimating-and-etc": "Estimating & ETC",
    "field-execution-basics": "Field Execution Basics",
    "floor-buffer": "Floor Buffer",
    "field-testing-routine": "Field Testing Routine",
    "gc-pay": "General Contractor Pay (GC Pay)",
    "graydaze-pm-role": "Project Manager Role",
    "graydaze-operating-principles": "Operating Principles",
    "graydaze-project-manager-role": "Project Manager Role",
    "hotel-corporate-codes": "Hotel Corporate Codes",
    "graydaze-training-program": "Training Program",
    "indexing-and-logging": "Indexing & Logging",
    "ingest": "Ingest",
    "joint-filler-replacement": "Joint Filler Replacement",
    "joint-filler": "Joint Filler",
    "lint": "Lint",
    "llm-wiki": "LLM Wiki",
    "material-handling": "Material Handling",
    "mission-support": "Mission Support",
    "mm-ep90": "MM EP90",
    "moisture-testing": "Moisture Testing",
    "new-construction-estimating": "New Construction Estimating",
    "osha-flammable-liquids": "OSHA Flammable Liquids",
    "pay-apps": "Payment Applications (Pay Apps)",
    "query": "Query",
    "rally-the-relationship": "Rally the Relationship",
    "ratio-test": "Ratio Test",
    "repaint-project-scheduling": "Repaint Project Scheduling",
    "ramp-credit-card-coding": "RAMP Credit Card Coding",
    "retainage": "Retainage",
    "road-to-uncommon-success": "Road to Uncommon Success",
    "safety-near-live-power-lines": "Safety Near Live Power Lines",
    "slab-on-grade-joints": "Slab-on-Grade Joints (SOG)",
    "rs88-scraping-in-freezer": "RS88 Freezer Scraping",
    "slab-stabilization": "Slab Stabilization",
    "zip-strip-joint-prep": "Zip Strip Joint Prep",
    "texture-pump-repair": "Texture Pump Repair",
    "stock-ordering": "Stock Ordering",
    "spraylastic-dryfall": "Spraylastic Dryfall",
    "sog-floor-repairs": "Slab-on-Grade Floor Repairs",
}

MAX_LABEL_CHARS = 34

# Leading words dropped from a title before it becomes a fallback label, so an
# org prefix or article doesn't crowd out the meaningful part of the name.
_LEADING_STOPWORDS = ("graydaze", "the")


def _slug_from_path(path: str) -> str:
    if not path:
        return ""
    return PurePosixPath(path).stem


def _truncate(text: str) -> str:
    """Truncate to the cap on a word boundary, adding an ellipsis when cut."""

    if len(text) <= MAX_LABEL_CHARS:
        return text
    # Reserve one char for the ellipsis, then trim back to the last whole word.
    clipped = text[: MAX_LABEL_CHARS - 1].rstrip()
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")].rstrip()
    return f"{clipped}…"


def _heuristic_label(title: str) -> str:
    """Best-effort readable label for a concept with no explicit override.

    Drops a leading org/``The`` prefix and normalizes whitespace, then returns
    the title as-is (truncated on a word boundary if it exceeds the cap). It
    never invents an initialism — a genuinely acronymic title (e.g. one that
    already reads ``OSHA ...``) simply passes through unchanged.
    """

    cleaned = re.sub(r"\s+", " ", title).strip()
    if not cleaned:
        return title.strip()[:MAX_LABEL_CHARS]

    tokens = cleaned.split()
    if len(tokens) > 1 and tokens[0].lower() in _LEADING_STOPWORDS:
        tokens = tokens[1:]

    return _truncate(" ".join(tokens))


def concept_label(title: str, path: str = "") -> str:
    """Return the dashboard label for a concept.

    Resolution order: explicit override by page slug, then a readable heuristic
    on the title. ``Unknown`` (and empty titles) pass through unchanged so the
    dashboard's Unknown bucket is preserved.
    """

    if not title or title == UNKNOWN_CONCEPT:
        return title or UNKNOWN_CONCEPT

    slug = _slug_from_path(path)
    if slug in CONCEPT_LABEL_OVERRIDES:
        return CONCEPT_LABEL_OVERRIDES[slug]

    return _heuristic_label(title)
