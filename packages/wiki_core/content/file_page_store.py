"""Filesystem-backed page store for the local wiki repository."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .markdown import compose_markdown, iter_wiki_markdown_files, load_wiki_page, slugify, split_frontmatter
from packages.wiki_core.settings import CoreSettings


class FilePageStore:
    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()

    def iter_wiki_pages(self) -> list[Path]:
        # wiki/reports/ holds operational sync reports (see AutoIngestService).
        # They are published to SharePoint for humans but must never be embedded
        # into the retrieval index.
        reports_root = self._settings.wiki_root / "reports"
        return [
            path
            for path in iter_wiki_markdown_files(self._settings.wiki_root)
            if reports_root not in path.parents
        ]

    def load_wiki_page(self, path: Path):
        return load_wiki_page(path, self._settings.repo_root)

    def write_page(self, relative_path: str, frontmatter: dict[str, Any], body: str) -> None:
        destination = self._settings.repo_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(compose_markdown(frontmatter, body), encoding="utf-8")

    def read_index_summary(self, max_chars: int) -> str:
        try:
            text = self._settings.index_path.read_text(encoding="utf-8")
        except OSError:
            return "Index unavailable."

        _frontmatter, body = split_frontmatter(text)
        section_lines: list[str] = []
        current_section = "General"
        entries: list[str] = []

        def flush() -> None:
            if entries:
                section_lines.append(f"{current_section}: " + "; ".join(entries[:6]))

        for raw_line in body.splitlines():
            line = raw_line.strip()
            if line.startswith("## "):
                flush()
                current_section = line[3:].strip() or "General"
                entries = []
                continue
            if line.startswith("- "):
                entries.append(line[2:].strip())

        flush()
        summary = "\n".join(section_lines).strip()
        return summary[:max_chars] or "Index unavailable."

    def upsert_index_entry(self, entry: str) -> bool:
        entry = entry.strip()
        if not entry:
            return False

        path = self._settings.index_path
        text = path.read_text(encoding="utf-8")
        if entry in text:
            return False

        frontmatter, body = split_frontmatter(text)
        section_name = self._section_name_for_entry(entry)
        updated = self._insert_entry_under_section(body, section_name, entry)
        self._write_existing_page(path, frontmatter, updated)
        return True

    def append_overview_note(self, note: str) -> bool:
        note = note.strip()
        if not note:
            return False

        path = self._settings.overview_path
        text = path.read_text(encoding="utf-8")
        marker = "## Open Questions"
        bullet = f"- {note}"
        if bullet in text:
            return False

        frontmatter, body = split_frontmatter(text)
        if marker not in text:
            updated = body.rstrip() + f"\n\n## Current State\n\n{bullet}\n"
            self._write_existing_page(path, frontmatter, updated)
            return True

        before, after = body.split(marker, maxsplit=1)
        before = before.rstrip() + "\n" + bullet + "\n\n"
        self._write_existing_page(path, frontmatter, before + marker + after)
        return True

    def append_log_entry(self, title: str, bullets: list[str]) -> bool:
        today = datetime.now(UTC).date().isoformat()
        clean_bullets = [bullet.strip() for bullet in bullets if bullet.strip()]
        if not clean_bullets:
            return False

        entry_lines = [f"## [{today}] {title}", ""] + [f"- {bullet}" for bullet in clean_bullets]
        path = self._settings.log_path
        existing_text = path.read_text(encoding="utf-8")
        entry_text = "\n".join(entry_lines).strip()
        if entry_text in existing_text:
            return False
        frontmatter, body = split_frontmatter(existing_text)
        updated = body.rstrip() + "\n\n" + entry_text + "\n"
        self._write_existing_page(path, frontmatter, updated)
        return True

    def write_managed_page(self, relative_path: str, page_spec: dict[str, Any], relative_raw_path: str) -> str | None:
        if not relative_path.startswith("wiki/") or not relative_path.endswith(".md"):
            return None

        page_type = str(page_spec.get("type", "source")).strip()
        title = str(page_spec.get("title", "")).strip() or Path(relative_path).stem.replace("-", " ").title()
        raw_body = str(page_spec.get("body", "")).strip()
        # Strip any frontmatter the LLM included in its body so we don't emit a
        # second --- block when compose_markdown prepends the canonical one.
        _embedded_frontmatter, body = split_frontmatter(raw_body)
        body = body.strip()
        if not body:
            return None

        destination = self._settings.repo_root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now(UTC).date().isoformat()
        created = today
        existing_sources: list[str] = []
        if destination.exists():
            existing_page = load_wiki_page(destination, self._settings.repo_root)
            created = str(existing_page.frontmatter.get("created") or today)
            raw_existing_sources = existing_page.frontmatter.get("sources") or []
            if not isinstance(raw_existing_sources, list):
                raw_existing_sources = [str(raw_existing_sources)]
            existing_sources = [str(source) for source in raw_existing_sources]

        sources = page_spec.get("sources") or [relative_raw_path]
        if not isinstance(sources, list):
            sources = [str(sources)]
        merged_sources: list[str] = []
        for source in [*existing_sources, *[str(item) for item in sources]]:
            if source and source not in merged_sources:
                merged_sources.append(source)

        frontmatter = {
            "title": title,
            "type": page_type,
            "status": str(page_spec.get("status", "active")),
            "created": created,
            "updated": today,
            "source_count": max(int(page_spec.get("source_count") or 0), len(merged_sources), 1),
            "sources": merged_sources,
        }
        destination.write_text(compose_markdown(frontmatter, body), encoding="utf-8")
        return relative_path

    def build_index_entry(self, relative_path: str, description: str) -> str:
        title = Path(relative_path).stem.replace("-", " ").title()
        link_target = relative_path.removesuffix(".md")
        return f"- [[{link_target}|{title}]] - {description.strip()}"

    def infer_index_description(self, page_spec: dict[str, Any]) -> str:
        body = str(page_spec.get("body", "")).strip()
        if not body:
            return ""
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("- "):
                return stripped[2:].strip().rstrip(".")
            if stripped and not stripped.startswith("#"):
                return stripped[:140].rstrip(".")
        return ""

    def append_ingest_log_entry(self, *, raw_path: str, generated: dict[str, Any], updated_paths: list[str]) -> bool:
        bullets = [str(item).strip() for item in generated.get("log_bullets", []) if str(item).strip()]
        if not bullets:
            bullets = [f"Raw source: [[{raw_path}]].", "Updated wiki pages through automated ingest."]
        if updated_paths:
            bullets.append("Updated pages: " + ", ".join(f"`{path}`" for path in sorted(set(updated_paths))) + ".")
        entry_title = slugify(Path(raw_path).stem).replace("-", " ")
        return self.append_log_entry(title=f"ingest | {entry_title}", bullets=bullets)

    def _write_existing_page(self, path: Path, frontmatter: dict[str, Any], body: str) -> None:
        today = datetime.now(UTC).date().isoformat()
        frontmatter = dict(frontmatter)
        frontmatter["updated"] = today
        sources = frontmatter.get("sources") or []
        if not isinstance(sources, list):
            sources = [str(sources)]
        frontmatter["sources"] = [str(source) for source in sources]
        frontmatter["source_count"] = max(int(frontmatter.get("source_count") or 0), len(frontmatter["sources"]))
        path.write_text(compose_markdown(frontmatter, body), encoding="utf-8")

    def _section_name_for_entry(self, entry: str) -> str:
        if "[[wiki/sources/" in entry:
            return "Sources"
        if "[[wiki/concepts/" in entry:
            return "Concepts"
        if "[[wiki/entities/" in entry:
            return "Entities"
        if "[[wiki/syntheses/" in entry:
            return "Syntheses"
        if "[[wiki/queries/" in entry:
            return "Queries"
        return "Queries"

    def _insert_entry_under_section(self, body: str, section_name: str, entry: str) -> str:
        lines = body.splitlines()
        heading = f"## {section_name}"
        start_index = None
        end_index = len(lines)

        for index, line in enumerate(lines):
            if line.strip() == heading:
                start_index = index
                continue
            if start_index is not None and line.startswith("## "):
                end_index = index
                break

        if start_index is None:
            suffix = body.rstrip()
            return suffix + f"\n\n{heading}\n\n{entry}\n"

        section_entries = [line.strip() for line in lines[start_index + 1 : end_index] if line.strip().startswith("- ")]
        by_target: dict[str, str] = {}
        for existing_entry in section_entries + [entry]:
            by_target[self._entry_target(existing_entry)] = existing_entry
        section_entries = sorted(by_target.values(), key=str.lower)

        rebuilt = lines[: start_index + 1]
        if rebuilt and rebuilt[-1] != "":
            rebuilt.append("")
        rebuilt.extend(section_entries)
        rebuilt.append("")
        rebuilt.extend(lines[end_index:])
        return "\n".join(rebuilt).rstrip() + "\n"

    def _entry_target(self, entry: str) -> str:
        if "[[" not in entry or "]]" not in entry:
            return entry
        inner = entry.split("[[", maxsplit=1)[1].split("]]", maxsplit=1)[0]
        return inner.split("|", maxsplit=1)[0].strip()
