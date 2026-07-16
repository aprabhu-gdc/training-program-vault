"""SharePoint-to-wiki ingest orchestration following the vault schema."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from packages.shared.documents.extract_text import SUPPORTED_EXTENSIONS, extract_text
from packages.wiki_core.ai.legacy_provider_gateway import LegacyProviderGateway
from packages.wiki_core.content.file_page_store import FilePageStore
from packages.wiki_core.content.markdown import slugify
from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from packages.wiki_core.retrieval.index_service import IndexingReport, VaultIndexer
from packages.wiki_core.settings import CoreSettings


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
    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()
        self._settings.ensure_data_dirs()
        self._settings.validate_llm()
        self._settings.validate_source_sync()
        self._page_store = FilePageStore(self._settings)
        self._model_gateway = LegacyProviderGateway(self._settings)
        self._source_sync = SharePointSourceSyncAdapter(self._settings)
        self._indexer = VaultIndexer(self._settings)

    def sync_from_webhook(self, payload: dict[str, Any]) -> SyncReport:
        events = [
            event
            for event in self._source_sync.parse_webhook_payload(payload)
            if self._source_sync.is_in_scope(event) and Path(event.path).suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        return self.sync_events(events)

    def sync_all_training_files(self) -> SyncReport:
        self._refresh_local_wiki_from_sharepoint()
        folder_path = self._settings.normalized_sharepoint_raw_root_path
        events = [
            event
            for event in self._source_sync.list_files_recursive(folder_path)
            if Path(event.path).suffix.lower() in SUPPORTED_EXTENSIONS
        ]
        report = self.sync_events(events, download_missing=True)

        # The per-file upsert above only indexes LLM-regenerated pages. Pages
        # pulled from SharePoint by _refresh_local_wiki_from_sharepoint (and any
        # page that predates the current index) are now on local disk but may be
        # absent from the index. Reconcile heals that drift on every full sync.
        # Fail-soft: a reconcile failure must not undo the sync that succeeded.
        try:
            self._indexer.reconcile()
        except Exception:
            LOGGER.exception("Index reconcile after full sync failed; targeted upsert results stand")

        return report

    def sync_events(self, events: Iterable[Any], *, download_missing: bool = True) -> SyncReport:
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
                local_path = self._source_sync.download_file(event.path)
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
        self._publish_changed_wiki_files(changed_relative_paths=sorted(set(updated_wiki_files)))
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
            relative_path = self._page_store.write_managed_page(
                relative_path=str(page_spec.get("relative_path", "")).strip(),
                page_spec=page_spec,
                relative_raw_path=relative_raw_path,
            )
            if relative_path:
                updated_paths.append(relative_path)
                description = self._page_store.infer_index_description(page_spec)
                if description:
                    index_candidates.append((relative_path, description))

        if generated.get("index_entry"):
            if self._page_store.upsert_index_entry(generated["index_entry"]):
                updated_paths.append("wiki/index.md")
        else:
            for relative_path, description in index_candidates:
                if self._page_store.upsert_index_entry(self._page_store.build_index_entry(relative_path, description)):
                    updated_paths.append("wiki/index.md")

        if generated.get("overview_note"):
            if self._page_store.append_overview_note(str(generated["overview_note"])):
                updated_paths.append("wiki/overview.md")

        if self._page_store.append_ingest_log_entry(raw_path=relative_raw_path, generated=generated, updated_paths=updated_paths):
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
        return self._model_gateway.complete_json(system_prompt=system_prompt, user_prompt=user_prompt, temperature=0.1)

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

    def _load_state(self) -> dict[str, str]:
        path = self._settings.source_sync_state_path
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict[str, str]) -> None:
        self._settings.source_sync_state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

    def _event_key(self, event: Any) -> str:
        fingerprint = [part for part in (event.modified_at, event.entry_id) if part]
        if not fingerprint:
            return ""
        return "|".join([event.event_type, *fingerprint])

    def _relative_from_event(self, event: Any) -> Path:
        normalized = event.path.strip("/")
        raw_root = self._settings.normalized_sharepoint_raw_root_path
        if normalized == raw_root:
            return Path()
        if normalized.startswith(raw_root + "/"):
            normalized = normalized[len(raw_root) + 1 :]
        return Path(normalized)

    def _publish_changed_wiki_files(self, *, changed_relative_paths: list[str]) -> None:
        for relative_path in changed_relative_paths:
            if not relative_path.startswith("wiki/"):
                continue
            local_path = self._settings.repo_root / relative_path
            if not local_path.exists():
                continue
            self._source_sync.upload_text_file(relative_path, local_path.read_text(encoding="utf-8"))

    def _refresh_local_wiki_from_sharepoint(self) -> None:
        wiki_root = self._settings.normalized_sharepoint_wiki_root_path
        remote_events = self._source_sync.list_files_recursive(wiki_root)
        for event in remote_events:
            if Path(event.path).suffix.lower() != ".md":
                continue

            remote_path = event.path.strip("/")
            if remote_path == wiki_root:
                continue
            if not remote_path.startswith(wiki_root + "/"):
                continue

            relative_path = remote_path[len(wiki_root) + 1 :]
            local_destination = self._settings.wiki_root / relative_path
            self._source_sync.download_remote_file(remote_path, local_destination)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="Run automated SharePoint ingest and wiki reindexing.")
    parser.add_argument("--manual", action="store_true", help="Run a manual sync by enumerating the authoritative SharePoint raw/sources folder.")
    args = parser.parse_args()

    service = AutoIngestService()
    if args.manual:
        report = service.sync_all_training_files()
    else:
        parser.error("Choose --manual.")

    LOGGER.info(
        "Sync complete requested=%s downloaded=%s updated_wiki=%s indexed=%s deleted=%s",
        report.requested_files,
        len(report.downloaded_files),
        len(report.updated_wiki_files),
        len(report.index_report.indexed_files),
        len(report.index_report.deleted_files),
    )
    return 0
