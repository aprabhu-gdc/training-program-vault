"""Markdown and frontmatter helpers for the vault."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_WIKILINK_WITH_ALIAS = re.compile(r"\[\[([^\]|]+)\|([^\]]+)\]\]")
_WIKILINK_SIMPLE = re.compile(r"\[\[([^\]]+)\]\]")
MAX_CHUNK_CHARS = 6000


@dataclass(frozen=True)
class WikiPage:
    path: Path
    relative_path: str
    title: str
    page_type: str
    sources: list[str]
    frontmatter: dict[str, Any]
    body: str
    raw_text: str
    sha256: str


@dataclass(frozen=True)
class MarkdownChunk:
    chunk_id: str
    relative_path: str
    title: str
    page_type: str
    section_heading: str
    sources: list[str]
    text: str
    metadata: dict[str, Any]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            frontmatter_text = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            parsed = yaml.safe_load(frontmatter_text) or {}
            if isinstance(parsed, dict):
                return parsed, body.lstrip("\n")
            return {}, body.lstrip("\n")

    return {}, text


def dump_frontmatter(frontmatter: dict[str, Any]) -> str:
    return yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()


def compose_markdown(frontmatter: dict[str, Any], body: str) -> str:
    clean_body = body.strip() + "\n"
    return f"---\n{dump_frontmatter(frontmatter)}\n---\n\n{clean_body}"


def load_wiki_page(path: Path, repo_root: Path) -> WikiPage:
    raw_text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(raw_text)
    title = str(frontmatter.get("title") or path.stem.replace("-", " ").title()).strip()
    page_type = str(frontmatter.get("type") or "unknown").strip()
    sources = frontmatter.get("sources") or []
    if not isinstance(sources, list):
        sources = [str(sources)]

    return WikiPage(
        path=path,
        relative_path=path.relative_to(repo_root).as_posix(),
        title=title,
        page_type=page_type,
        sources=[str(source) for source in sources],
        frontmatter=frontmatter,
        body=body,
        raw_text=raw_text,
        sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )


def iter_wiki_markdown_files(wiki_root: Path) -> list[Path]:
    return sorted(path for path in wiki_root.rglob("*.md") if path.is_file())


def split_by_h2_sections(body: str) -> list[tuple[str, str]]:
    if not body.strip():
        return []

    sections: list[tuple[str, str]] = []
    current_heading = "Overview"
    current_lines: list[str] = []

    for line in body.splitlines():
        if line.startswith("## "):
            chunk = "\n".join(current_lines).strip()
            if chunk:
                sections.append((current_heading, chunk))
            current_heading = line[3:].strip() or "Untitled"
            current_lines = [line]
            continue
        current_lines.append(line)

    chunk = "\n".join(current_lines).strip()
    if chunk:
        sections.append((current_heading, chunk))

    return sections or [("Overview", body.strip())]


def build_chunks_for_page(page: WikiPage) -> list[MarkdownChunk]:
    chunks: list[MarkdownChunk] = []
    chunk_index = 0
    for section_index, (section_heading, section_text) in enumerate(split_by_h2_sections(page.body)):
        section_bodies = _split_large_section_text(section_text.strip(), max_chars=MAX_CHUNK_CHARS)
        for part_index, chunk_body in enumerate(section_bodies, start=1):
            if not chunk_body:
                continue

            section_label = section_heading
            if len(section_bodies) > 1:
                section_label = f"{section_heading} (Part {part_index})"

            chunk_id = hashlib.sha1(
                f"{page.relative_path}:{section_index}:{part_index}:{section_heading}".encode("utf-8")
            ).hexdigest()
            text = (
                f"Page Title: {page.title}\n"
                f"Page Type: {page.page_type}\n"
                f"Section: {section_label}\n\n"
                f"{chunk_body}"
            )
            metadata = {
                "path": page.relative_path,
                "title": page.title,
                "type": page.page_type,
                "section": section_label,
                "chunk_index": chunk_index,
                "sha256": page.sha256,
                "sources": json.dumps(page.sources),
            }
            chunks.append(
                MarkdownChunk(
                    chunk_id=chunk_id,
                    relative_path=page.relative_path,
                    title=page.title,
                    page_type=page.page_type,
                    section_heading=section_label,
                    sources=page.sources,
                    text=text,
                    metadata=metadata,
                )
            )
            chunk_index += 1
    return chunks


def _split_large_section_text(text: str, *, max_chars: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    parts = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    if not parts:
        return [text[:max_chars]]

    chunks: list[str] = []
    current_parts: list[str] = []
    current_length = 0

    for part in parts:
        if len(part) > max_chars:
            if current_parts:
                chunks.append("\n\n".join(current_parts).strip())
                current_parts = []
                current_length = 0
            chunks.extend(_split_oversized_part(part, max_chars=max_chars))
            continue

        separator_length = 2 if current_parts else 0
        if current_parts and current_length + separator_length + len(part) > max_chars:
            chunks.append("\n\n".join(current_parts).strip())
            current_parts = [part]
            current_length = len(part)
            continue

        current_parts.append(part)
        current_length += separator_length + len(part)

    if current_parts:
        chunks.append("\n\n".join(current_parts).strip())

    return [chunk for chunk in chunks if chunk]


def _split_oversized_part(text: str, *, max_chars: int) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        chunks: list[str] = []
        current_lines: list[str] = []
        current_length = 0
        for line in lines:
            stripped_line = line.strip()
            if len(stripped_line) > max_chars:
                if current_lines:
                    chunks.append("\n".join(current_lines).strip())
                    current_lines = []
                    current_length = 0
                chunks.extend(_slice_text(stripped_line, max_chars=max_chars))
                continue

            separator_length = 1 if current_lines else 0
            if current_lines and current_length + separator_length + len(stripped_line) > max_chars:
                chunks.append("\n".join(current_lines).strip())
                current_lines = [stripped_line]
                current_length = len(stripped_line)
                continue

            current_lines.append(stripped_line)
            current_length += separator_length + len(stripped_line)

        if current_lines:
            chunks.append("\n".join(current_lines).strip())
        if chunks:
            return chunks

    return _slice_text(text, max_chars=max_chars)


def _slice_text(text: str, *, max_chars: int) -> list[str]:
    return [text[index : index + max_chars].strip() for index in range(0, len(text), max_chars) if text[index : index + max_chars].strip()]


def parse_sources_metadata(metadata_value: Any) -> list[str]:
    if isinstance(metadata_value, list):
        return [str(item) for item in metadata_value]
    if isinstance(metadata_value, str):
        try:
            parsed = json.loads(metadata_value)
        except json.JSONDecodeError:
            return [metadata_value]
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def clean_obsidian_links(text: str) -> str:
    def replace_with_alias(match: re.Match[str]) -> str:
        return f"**{match.group(2).strip()}**"

    def replace_simple(match: re.Match[str]) -> str:
        target = match.group(1).strip().split("|", maxsplit=1)[0]
        label = target.split("/")[-1].replace("-", " ").strip().title()
        return f"**{label}**"

    text = _WIKILINK_WITH_ALIAS.sub(replace_with_alias, text)
    return _WIKILINK_SIMPLE.sub(replace_simple, text)


def slugify(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    return lowered.strip("-") or "untitled"
