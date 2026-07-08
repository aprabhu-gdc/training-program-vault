"""Convert answer Markdown into Adaptive Card elements with visual hierarchy.

Teams bot messages can't render Markdown headings and render lists only on desktop, so
answers are rendered as Adaptive Card ``TextBlock``s instead — which render consistently on
desktop and mobile. Adaptive Card ``TextBlock`` also has no true headings, so we simulate them
with size + bold weight (+ a separator for top-level sections).

This is a deliberately small, forgiving, line-based scanner (not a full Markdown parser). It
supports the subset the answer prompt emits — headings, paragraphs, bulleted/numbered lists,
fenced code, blockquotes — and never raises on unexpected input (falls back to a single block).
Inline ``**bold**`` / ``_italic_`` / ``[text](url)`` render natively inside a TextBlock.
"""

from __future__ import annotations

import re
from typing import Any


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_BLOCKQUOTE = re.compile(r"^\s*>\s?(.*)$")

# Heading level -> TextBlock styling (weight Bolder is applied to all headings).
_HEADING_STYLE: dict[int, dict[str, Any]] = {
    1: {"size": "Large", "spacing": "Medium", "separator": True},
    2: {"size": "Medium", "spacing": "Medium", "separator": True},
    3: {"size": "Default", "spacing": "Medium"},
}


def _text_block(text: str, **props: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "TextBlock", "text": text, "wrap": True}
    block.update(props)
    return block


def markdown_to_adaptive_elements(markdown: str) -> list[dict[str, Any]]:
    """Return Adaptive Card elements rendering the given answer Markdown."""

    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    elements: list[dict[str, Any]] = []

    para: list[str] = []
    list_items: list[str] = []
    code: list[str] = []
    in_code = False

    def flush_para() -> None:
        if para:
            elements.append(_text_block(" ".join(para).strip(), spacing="Small"))
            para.clear()

    def flush_list() -> None:
        if list_items:
            elements.append(_text_block("\r".join(list_items), spacing="Small"))
            list_items.clear()

    def flush_all() -> None:
        flush_para()
        flush_list()

    for line in lines:
        if _FENCE.match(line):
            if in_code:
                elements.append(_text_block("\n".join(code), fontType="Monospace", spacing="Small"))
                code.clear()
                in_code = False
            else:
                flush_all()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue

        if not line.strip():
            flush_all()
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_all()
            level = min(len(heading.group(1)), 3)
            elements.append(_text_block(heading.group(2).strip(), weight="Bolder", **_HEADING_STYLE[level]))
            continue

        bullet = _BULLET.match(line)
        if bullet:
            flush_para()
            list_items.append(f"- {bullet.group(1).strip()}")
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            flush_para()
            list_items.append(f"{numbered.group(1)}. {numbered.group(2).strip()}")
            continue

        quote = _BLOCKQUOTE.match(line)
        if quote:
            flush_all()
            content = quote.group(1).strip()
            elements.append(_text_block(f"_{content}_" if content else " ", isSubtle=True, spacing="Small"))
            continue

        # Normal prose line -> part of the current paragraph.
        flush_list()
        para.append(line.strip())

    if in_code and code:
        elements.append(_text_block("\n".join(code), fontType="Monospace", spacing="Small"))
    flush_all()

    if not elements:
        return [_text_block((markdown or "").strip() or " ")]
    return elements
