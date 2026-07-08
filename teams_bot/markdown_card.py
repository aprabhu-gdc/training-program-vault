"""Convert answer Markdown into Adaptive Card elements with visual hierarchy.

Teams bot messages can't render Markdown headings and render lists only on desktop, so
answers are rendered as Adaptive Card ``TextBlock``s instead — which render consistently on
desktop and mobile. Adaptive Card ``TextBlock`` also has no true headings, so we simulate them
with size + bold weight (+ an accent color and a separator for top-level sections).

Lists are rendered one ``TextBlock`` per item with a **self-computed** marker (``•`` or a
running ``N.``) and non-breaking-space indentation, rather than relying on Adaptive Card's
Markdown list rendering — which renumbers ordered items back to ``1.`` whenever a list is
interrupted (e.g. by a nested bulleted sub-list). Computing markers ourselves keeps numbering
correct and supports nesting. (Regular leading spaces are avoided: 4+ would trigger a Markdown
indented-code block, and they collapse anyway — hence non-breaking spaces.)

This is a deliberately small, forgiving, line-based scanner; it never raises on unexpected
input (falls back to a single block). Inline ``**bold**`` / ``_italic_`` / ``[text](url)``
render natively inside a TextBlock.
"""

from __future__ import annotations

import re
from typing import Any


_HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_BULLET = re.compile(r"^(\s*)[-*]\s+(.*)$")
_NUMBERED = re.compile(r"^(\s*)\d+\.\s+(.*)$")
_FENCE = re.compile(r"^\s*```")
_BLOCKQUOTE = re.compile(r"^\s*>\s?(.*)$")

# Heading level -> TextBlock styling (all headings are bold).
_HEADING_STYLE: dict[int, dict[str, Any]] = {
    1: {"size": "ExtraLarge", "spacing": "Medium", "separator": True, "color": "Accent"},
    2: {"size": "Large", "spacing": "Medium", "separator": True, "color": "Accent"},
    3: {"size": "Medium", "spacing": "Medium"},
}

# Non-breaking spaces per nesting level (plain spaces collapse in Markdown rendering and
# 4+ leading plain spaces would be parsed as a code block).
_INDENT_UNIT = " " * 4  # 4 non-breaking spaces per nesting level
_MAX_LEVEL = 4


def _text_block(text: str, **props: Any) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "TextBlock", "text": text, "wrap": True}
    block.update(props)
    return block


def _indent_level(leading: str) -> int:
    """Map a run of leading whitespace to a nesting level (~2 spaces per level)."""

    return min(len(leading.replace("\t", "  ")) // 2, _MAX_LEVEL)


def markdown_to_adaptive_elements(markdown: str) -> list[dict[str, Any]]:
    """Return Adaptive Card elements rendering the given answer Markdown."""

    lines = (markdown or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    elements: list[dict[str, Any]] = []

    para: list[str] = []
    code: list[str] = []
    in_code = False
    ordered_counters: dict[int, int] = {}

    def flush_para() -> None:
        if para:
            elements.append(_text_block(" ".join(para).strip(), spacing="Small"))
            para.clear()

    def clear_deeper(level: int) -> None:
        for deeper in [lvl for lvl in ordered_counters if lvl > level]:
            del ordered_counters[deeper]

    for line in lines:
        if _FENCE.match(line):
            if in_code:
                elements.append(_text_block("\n".join(code), fontType="Monospace", spacing="Small"))
                code.clear()
                in_code = False
            else:
                flush_para()
                in_code = True
            continue
        if in_code:
            code.append(line)
            continue

        if not line.strip():
            flush_para()
            continue

        heading = _HEADING.match(line)
        if heading:
            flush_para()
            ordered_counters.clear()  # a new section restarts numbering
            level = min(len(heading.group(1)), 3)
            elements.append(_text_block(heading.group(2).strip(), weight="Bolder", **_HEADING_STYLE[level]))
            continue

        numbered = _NUMBERED.match(line)
        if numbered:
            flush_para()
            level = _indent_level(numbered.group(1))
            ordered_counters[level] = ordered_counters.get(level, 0) + 1
            clear_deeper(level)
            marker = f"{ordered_counters[level]}."
            elements.append(_text_block(_INDENT_UNIT * level + f"{marker} {numbered.group(2).strip()}", spacing="Small"))
            continue

        bullet = _BULLET.match(line)
        if bullet:
            flush_para()
            level = _indent_level(bullet.group(1))
            clear_deeper(level)
            elements.append(_text_block(_INDENT_UNIT * level + f"• {bullet.group(2).strip()}", spacing="Small"))
            continue

        quote = _BLOCKQUOTE.match(line)
        if quote:
            flush_para()
            content = quote.group(1).strip()
            elements.append(_text_block(f"_{content}_" if content else " ", isSubtle=True, spacing="Small"))
            continue

        # Normal prose line -> part of the current paragraph.
        para.append(line.strip())

    if in_code and code:
        elements.append(_text_block("\n".join(code), fontType="Monospace", spacing="Small"))
    flush_para()

    if not elements:
        return [_text_block((markdown or "").strip() or " ")]
    return elements
