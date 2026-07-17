"""SharePoint-to-wiki ingest orchestration following the vault schema."""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, NamedTuple

from packages.shared.documents.extract_text import (
    CONVERTIBLE_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    extract_text,
)
from packages.wiki_core.ai.legacy_provider_gateway import LegacyProviderGateway
from packages.wiki_core.content.file_page_store import FilePageStore
from packages.wiki_core.content.markdown import slugify
from packages.wiki_core.ingest.progress import ProgressReporter
from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from packages.wiki_core.retrieval.index_service import IndexingReport, VaultIndexer
from packages.wiki_core.settings import CoreSettings


LOGGER = logging.getLogger(__name__)


# Enumerated source files we know how to turn into wiki pages: parsed locally,
# or converted to PDF by Graph on download (legacy .doc).
INGESTIBLE_EXTENSIONS = SUPPORTED_EXTENSIONS | CONVERTIBLE_EXTENSIONS


class IngestFileResult(NamedTuple):
    updated_paths: list[str]
    empty: bool


@dataclass(frozen=True)
class SyncReport:
    requested_files: int
    downloaded_files: list[str]
    updated_wiki_files: list[str]
    skipped_files: list[str]
    index_report: IndexingReport
    # Per-file outcomes surfaced to users via wiki/reports/last-sync.md. Defaulted
    # so existing construction sites (tests) keep working unchanged.
    failed_files: list[dict[str, str]] = field(default_factory=list)
    empty_extraction_files: list[str] = field(default_factory=list)
    unsupported_files: dict[str, int] = field(default_factory=dict)

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
            if self._source_sync.is_in_scope(event) and Path(event.path).suffix.lower() in INGESTIBLE_EXTENSIONS
        ]
        return self.sync_events(events)

    def sync_all_training_files(self, progress: ProgressReporter | None = None) -> SyncReport:
        progress = progress or ProgressReporter()
        progress.phase("refreshing_wiki")
        self._refresh_local_wiki_from_sharepoint()
        progress.phase("listing")
        folder_path = self._settings.normalized_sharepoint_raw_root_path
        all_files = self._source_sync.list_files_recursive(folder_path)
        events = [event for event in all_files if Path(event.path).suffix.lower() in INGESTIBLE_EXTENSIONS]

        # Tally files we skip for lack of an extractor so the sync report can tell
        # users exactly which sources are not being ingested (and why).
        unsupported_files: dict[str, int] = {}
        for event in all_files:
            suffix = Path(event.path).suffix.lower()
            if suffix not in INGESTIBLE_EXTENSIONS:
                key = suffix or "(no extension)"
                unsupported_files[key] = unsupported_files.get(key, 0) + 1
        progress.set_unsupported(unsupported_files)

        report = self.sync_events(
            events, download_missing=True, unsupported_files=unsupported_files, progress=progress
        )

        # The per-file upsert above only indexes LLM-regenerated pages. Pages
        # pulled from SharePoint by _refresh_local_wiki_from_sharepoint (and any
        # page that predates the current index) are now on local disk but may be
        # absent from the index. Reconcile heals that drift on every full sync.
        # Fail-soft: a reconcile failure must not undo the sync that succeeded.
        progress.phase("indexing")
        try:
            self._indexer.reconcile()
        except Exception:
            LOGGER.exception("Index reconcile after full sync failed; targeted upsert results stand")

        self._publish_sync_report(report)
        return report

    def sync_events(
        self,
        events: Iterable[Any],
        *,
        download_missing: bool = True,
        unsupported_files: dict[str, int] | None = None,
        progress: ProgressReporter | None = None,
    ) -> SyncReport:
        progress = progress or ProgressReporter()
        events = list(events)
        state = self._load_state()
        downloaded_files: list[str] = []
        updated_wiki_files: list[str] = []
        skipped_files: list[str] = []
        failed_files: list[dict[str, str]] = []
        empty_extraction_files: list[str] = []
        processed_state: dict[str, str] = {}

        progress.phase("processing")
        progress.set_total(len(events))
        for event in events:
            event_key = self._event_key(event)
            if event_key and state.get(event.path) == event_key:
                skipped_files.append(event.path)
                progress.record("skipped_unchanged", path=event.path)
                continue

            progress.begin_file(event.path)
            # Fail-soft: one unreadable source (corrupt PDF, Graph download error,
            # LLM returning invalid JSON) must not abort the whole sync and strand
            # every other file. Record it and move on; it retries next sync because
            # its fingerprint is never written to state.
            try:
                local_path = self._settings.raw_sources_root / self._relative_from_event(event)
                if download_missing:
                    local_path = self._source_sync.download_file(event.path)
                elif not local_path.exists():
                    skipped_files.append(event.path)
                    progress.record("skipped_unchanged", path=event.path)
                    continue

                result = self._ingest_local_file(local_path)
            except Exception as exc:
                LOGGER.exception("Ingest failed for source file path=%s", event.path)
                failed_files.append({"path": event.path, "error": f"{type(exc).__name__}: {exc}"})
                progress.record("failed", path=event.path, error=f"{type(exc).__name__}: {exc}")
                continue

            downloaded_files.append(event.path)
            updated_wiki_files.extend(result.updated_paths)
            if result.empty:
                empty_extraction_files.append(event.path)
                progress.record("empty", path=event.path)
            else:
                progress.record("updated", path=event.path)
            if event_key:
                processed_state[event.path] = event_key

        changed_relative = sorted(set(updated_wiki_files))
        changed_paths = [self._settings.repo_root / path for path in changed_relative]
        # Publish + index BEFORE recording state, so a file is only marked
        # processed once its wiki output is durable on SharePoint and in the index.
        # A crash between here and _save_state just re-does idempotent work.
        self._publish_changed_wiki_files(changed_relative_paths=changed_relative)
        index_report = self._indexer.upsert_modified_files(changed_paths=changed_paths)

        state.update(processed_state)
        self._save_state(state)

        return SyncReport(
            requested_files=len(events),
            downloaded_files=downloaded_files,
            updated_wiki_files=changed_relative,
            skipped_files=skipped_files,
            index_report=index_report,
            failed_files=failed_files,
            empty_extraction_files=empty_extraction_files,
            unsupported_files=dict(unsupported_files or {}),
        )

    def _ingest_local_file(self, raw_path: Path) -> IngestFileResult:
        text = extract_text(raw_path).strip()
        if not text:
            LOGGER.warning("Skipping empty extracted file path=%s", raw_path)
            return IngestFileResult(updated_paths=[], empty=True)

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

        return IngestFileResult(updated_paths=sorted(set(updated_paths)), empty=False)

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

    def _publish_sync_report(self, report: SyncReport) -> None:
        """Write a human-readable sync report to wiki/reports/last-sync.md and
        publish it to SharePoint so users can see exactly what happened —
        including which files failed or were skipped and why. Fail-soft: a report
        problem must never mask an otherwise successful sync.

        The report contains only file paths and error classes — never config or
        secret values (org data-security policy).
        """

        try:
            generated_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            lines: list[str] = [
                "# Last sync report",
                "",
                f"- Generated: {generated_at}",
                f"- Source files considered: {report.requested_files}",
                f"- Ingested (new/changed): {len(report.downloaded_files)}",
                f"- Skipped (unchanged): {len(report.skipped_files)}",
                f"- Wiki pages updated: {len(report.updated_wiki_files)}",
                f"- Indexed pages: {len(report.index_report.indexed_files)}",
                f"- Failed: {len(report.failed_files)}",
                f"- Empty (no extractable text): {len(report.empty_extraction_files)}",
                "",
            ]

            if report.failed_files:
                lines.append("## Failed files")
                lines.append("")
                for entry in report.failed_files:
                    lines.append(f"- `{entry.get('path', '')}` — {entry.get('error', 'unknown error')}")
                lines.append("")

            if report.empty_extraction_files:
                lines.append("## Empty extractions (likely scanned images — need OCR)")
                lines.append("")
                for path in report.empty_extraction_files:
                    lines.append(f"- `{path}`")
                lines.append("")

            if report.unsupported_files:
                lines.append("## Unsupported file types (not ingested)")
                lines.append("")
                for suffix, count in sorted(report.unsupported_files.items()):
                    lines.append(f"- `{suffix}`: {count}")
                lines.append("")

            content = "\n".join(lines)
            relative_path = "wiki/reports/last-sync.md"
            local_path = self._settings.wiki_root / "reports" / "last-sync.md"
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(content, encoding="utf-8")
            self._source_sync.upload_text_file(relative_path, content)
        except Exception:
            LOGGER.exception("Failed to publish sync report; sync results are unaffected")

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
        "Sync complete requested=%s downloaded=%s updated_wiki=%s indexed=%s deleted=%s failed=%s empty=%s",
        report.requested_files,
        len(report.downloaded_files),
        len(report.updated_wiki_files),
        len(report.index_report.indexed_files),
        len(report.index_report.deleted_files),
        len(report.failed_files),
        len(report.empty_extraction_files),
    )
    return 0
