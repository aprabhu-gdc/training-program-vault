"""Egnyte-to-wiki sync orchestration following the vault schema."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from rag_backend.config import BackendSettings
from rag_backend.egnyte_client import EgnyteClient, EgnyteFileEvent
from rag_backend.indexer import IndexingReport, VaultIndexer
from rag_backend.llm import complete_json_sync
from rag_backend.markdown import (
    compose_markdown,
    load_wiki_page,
    slugify,
    split_frontmatter,
)
from scripts.extract_text import SUPPORTED_EXTENSIONS, extract_text


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncReport:
    requested_files: int
    downloaded_files: list[str]
    updated_wiki_files: list[str]
    skipped_files: list[str]
    index_report: IndexingReport

    @property
    def updated_count(self) -> int:
        return len(self.downloaded_files)


class AutoIngestService:
    """Download changed Egnyte files, synthesize wiki updates, and upsert the index."""

    def __init__(self, settings: BackendSettings | None = None) -> None:
        self._settings = settings or BackendSettings.from_env()
        self._settings.ensure_data_dirs()
        self._settings.validate_llm()
        self._indexer = VaultIndexer(self._settings)

    def sync_from_webhook(self, payload: dict[str, Any]) -> SyncReport:
        events = [
            event
            for event in EgnyteClient.parse_webhook_payload(payload)
            if self._is_training_program_event(event)
            and Path(event.path).suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        return self.sync_events(events)

    def sync_all_training_files(self) -> SyncReport:
        """Manual sync by enumerating the Egnyte Training Program CRD folder."""

        folder_path = "/".join(
            part.strip("/")
            for part in (
                self._settings.egnyte_sync_root,
                self._settings.egnyte_training_folder_name,
            )
            if part
        )
        events = [
            event
            for event in self._get_egnyte_client().list_files_recursive(folder_path)
            if Path(event.path).suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        return self.sync_events(events, download_missing=True)

    def sync_events(
        self,
        events: Iterable[EgnyteFileEvent],
        *,
        download_missing: bool = True,
    ) -> SyncReport:
        events = list(events)
        state = self._load_state()
        downloaded_files: list[str] = []
        updated_wiki_files: list[str] = []
        skipped_files: list[str] = []

        for event in events:
            event_key = self._event_key(event)
            if event_key and state.get(event.path) == event_key:
                skipped_files.append(event.path)
                continue

            local_path = self._settings.raw_sources_root / self._relative_from_event(event)
            if download_missing:
                local_path = self._get_egnyte_client().download_file(event.path)
            elif not local_path.exists():
                skipped_files.append(event.path)
                continue

            wiki_updates = self._ingest_local_file(local_path)
            downloaded_files.append(event.path)
            updated_wiki_files.extend(wiki_updates)
            if event_key:
                state[event.path] = event_key

        self._save_state(state)
        changed_paths = [self._settings.repo_root / path for path in sorted(set(updated_wiki_files))]
        index_report = self._indexer.upsert_modified_files(changed_paths=changed_paths)

        return SyncReport(
            requested_files=len(events),
            downloaded_files=downloaded_files,
            updated_wiki_files=sorted(set(updated_wiki_files)),
            skipped_files=skipped_files,
            index_report=index_report,
        )

    def _ingest_local_file(self, raw_path: Path) -> list[str]:
        text = extract_text(raw_path).strip()
        if not text:
            LOGGER.warning("Skipping empty extracted file path=%s", raw_path)
            return []

        relative_raw_path = raw_path.relative_to(self._settings.repo_root).as_posix()
        generated = self._generate_ingest_payload(raw_path=raw_path, relative_raw_path=relative_raw_path, text=text)

        updated_paths: list[str] = []
        page_specs = list(generated.get("pages", []))
        index_candidates: list[tuple[str, str]] = []
        for page_spec in page_specs:
            relative_path = self._write_page(relative_raw_path=relative_raw_path, page_spec=page_spec)
            if relative_path:
                updated_paths.append(relative_path)
                description = self._infer_index_description(page_spec)
                if description:
                    index_candidates.append((relative_path, description))

        if generated.get("index_entry"):
            if self._upsert_index_entry(generated["index_entry"]):
                updated_paths.append("wiki/index.md")
        else:
            for relative_path, description in index_candidates:
                if self._upsert_index_entry(self._build_index_entry(relative_path, description)):
                    updated_paths.append("wiki/index.md")

        if generated.get("overview_note"):
            if self._append_overview_note(str(generated["overview_note"])):
                updated_paths.append("wiki/overview.md")

        log_entry = self._append_log_entry(raw_path=relative_raw_path, generated=generated, updated_paths=updated_paths)
        if log_entry:
            updated_paths.append("wiki/log.md")

        return sorted(set(updated_paths))

    def _generate_ingest_payload(self, *, raw_path: Path, relative_raw_path: str, text: str) -> dict[str, Any]:
        today = datetime.now(UTC).date().isoformat()
        agents_schema = self._load_agents_schema()
        system_prompt = (
            "You are maintaining a persistent markdown wiki for the Graydaze PM training vault. "
            "You must follow the AGENTS.md schema already enforced in this repository. "
            "Only create or update durable pages that add accumulation value. "
            "Raw sources are immutable, and the source of truth for future queries is wiki/.\n\n"
            "AGENTS.md schema excerpt:\n"
            f"{agents_schema}\n\n"
            "Return strict JSON with this shape:\n"
            "{\n"
            '  "pages": [\n'
            "    {\n"
            '      "relative_path": "wiki/sources/example.md",\n'
            '      "title": "Example",\n'
            '      "type": "source",\n'
            '      "status": "active",\n'
            '      "source_count": 1,\n'
            '      "sources": ["raw/sources/..."],\n'
            '      "body": "# Summary\\n..."\n'
            "    }\n"
            "  ],\n"
            '  "index_entry": "- [[wiki/sources/example|Example]] - one-line description",\n'
            '  "overview_note": "One short bullet-worthy sentence if the top-level picture changes, else empty string.",\n'
            '  "log_bullets": ["Created ...", "Updated ..."]\n'
            "}\n\n"
            "Rules:\n"
            "- Use lowercase kebab-case filenames.\n"
            "- Use Obsidian wikilinks inside page bodies.\n"
            "- Include YAML frontmatter fields: title, type, status, created, updated, source_count, sources.\n"
            "- Create at least one wiki/sources page for the raw file.\n"
            "- Create wiki/concepts or wiki/entities pages only when the source materially adds reusable knowledge.\n"
            "- Keep summaries dense, specific, factual, and grounded in the source text.\n"
            "- Surface tensions or open questions instead of flattening uncertainty.\n"
            "- Do not mention JSON or implementation details in the page body.\n"
        )

        existing_context = self._load_context_for_ingest()
        user_prompt = (
            f"Today: {today}\n"
            f"Raw source path: {relative_raw_path}\n"
            f"Raw source filename: {raw_path.name}\n\n"
            "Relevant current wiki context:\n"
            f"{existing_context}\n\n"
            "Extracted raw source text:\n"
            f"{text[: self._settings.max_source_chars]}"
        )
        return complete_json_sync(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            settings=self._settings,
            temperature=0.1,
        )

    def _load_context_for_ingest(self) -> str:
        snippets: list[str] = []
        for path in (self._settings.index_path, self._settings.overview_path):
            try:
                snippets.append(path.read_text(encoding="utf-8")[:3000].strip())
            except OSError:
                continue
        return "\n\n".join(snippets)

    def _load_agents_schema(self) -> str:
        path = self._settings.repo_root / "AGENTS.md"
        try:
            return path.read_text(encoding="utf-8")[:12000].strip()
        except OSError:
            return "AGENTS.md unavailable."

    def _write_page(self, *, relative_raw_path: str, page_spec: dict[str, Any]) -> str | None:
        relative_path = str(page_spec.get("relative_path", "")).strip()
        if not relative_path.startswith("wiki/") or not relative_path.endswith(".md"):
            return None

        page_type = str(page_spec.get("type", "source")).strip()
        title = str(page_spec.get("title", "")).strip() or Path(relative_path).stem.replace("-", " ").title()
        body = str(page_spec.get("body", "")).strip()
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

    def _upsert_index_entry(self, entry: str) -> bool:
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

    def _append_overview_note(self, note: str) -> bool:
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

    def _append_log_entry(
        self,
        *,
        raw_path: str,
        generated: dict[str, Any],
        updated_paths: list[str],
    ) -> bool:
        today = datetime.now(UTC).date().isoformat()
        bullets = [str(item).strip() for item in generated.get("log_bullets", []) if str(item).strip()]
        if not bullets:
            bullets = [
                f"Raw source: [[{raw_path}]].",
                "Updated wiki pages through automated ingest.",
            ]

        if updated_paths:
            bullets.append("Updated pages: " + ", ".join(f"`{path}`" for path in sorted(set(updated_paths))) + ".")

        entry_title = slugify(Path(raw_path).stem).replace("-", " ")
        entry_lines = [f"## [{today}] ingest | {entry_title}", ""] + [f"- {bullet}" for bullet in bullets]

        path = self._settings.log_path
        existing_text = path.read_text(encoding="utf-8")
        entry_text = "\n".join(entry_lines).strip()
        if entry_text in existing_text:
            return False
        frontmatter, body = split_frontmatter(existing_text)
        updated = body.rstrip() + "\n\n" + entry_text + "\n"
        self._write_existing_page(path, frontmatter, updated)
        return True

    def _write_existing_page(
        self,
        path: Path,
        frontmatter: dict[str, Any],
        body: str,
    ) -> None:
        today = datetime.now(UTC).date().isoformat()
        frontmatter = dict(frontmatter)
        frontmatter["updated"] = today
        sources = frontmatter.get("sources") or []
        if not isinstance(sources, list):
            sources = [str(sources)]
        frontmatter["sources"] = [str(source) for source in sources]
        frontmatter["source_count"] = max(int(frontmatter.get("source_count") or 0), len(frontmatter["sources"]))
        path.write_text(compose_markdown(frontmatter, body), encoding="utf-8")

    def _get_egnyte_client(self) -> EgnyteClient:
        return EgnyteClient(self._settings)

    def _is_training_program_event(self, event: EgnyteFileEvent) -> bool:
        normalized = "/" + event.path.strip("/")
        required_prefix = "/" + self._settings.egnyte_sync_root.strip("/") + "/"
        training_segment = "/" + self._settings.egnyte_training_folder_name.strip("/") + "/"
        return normalized.startswith(required_prefix) and training_segment in normalized

    def _build_index_entry(self, relative_path: str, description: str) -> str:
        title = Path(relative_path).stem.replace("-", " ").title()
        link_target = relative_path.removesuffix(".md")
        return f"- [[{link_target}|{title}]] - {description.strip()}"

    def _infer_index_description(self, page_spec: dict[str, Any]) -> str:
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

        insert_index = end_index
        section_entries = [
            line.strip()
            for line in lines[start_index + 1 : end_index]
            if line.strip().startswith("- ")
        ]
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

    def _load_state(self) -> dict[str, str]:
        path = self._settings.egnyte_state_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict[str, str]) -> None:
        self._settings.egnyte_state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _event_key(self, event: EgnyteFileEvent) -> str:
        fingerprint = [part for part in (event.modified_at, event.entry_id) if part]
        if not fingerprint:
            return ""
        return "|".join([event.event_type, *fingerprint])

    def _relative_from_event(self, event: EgnyteFileEvent) -> Path:
        normalized = event.path.strip("/")
        sync_root = self._settings.egnyte_sync_root.strip("/")
        if normalized.startswith(sync_root + "/"):
            normalized = normalized[len(sync_root) + 1 :]
        return Path(normalized)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Run automated Egnyte ingest and wiki reindexing.")
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Run a manual sync by enumerating the Egnyte Training Program CRD folder.",
    )
    parser.add_argument(
        "--payload",
        help="Path to a JSON webhook payload file to replay.",
    )
    args = parser.parse_args()

    service = AutoIngestService()
    if args.manual:
        report = service.sync_all_training_files()
    elif args.payload:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
        report = service.sync_from_webhook(payload)
    else:
        parser.error("Choose either --manual or --payload.")

    LOGGER.info(
        "Sync complete requested=%s downloaded=%s updated_wiki=%s indexed=%s deleted=%s",
        report.requested_files,
        len(report.downloaded_files),
        len(report.updated_wiki_files),
        len(report.index_report.indexed_files),
        len(report.index_report.deleted_files),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
