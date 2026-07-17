"""Admin maintenance service: remove a page, clean (reconcile) the index, and
lint (audit) the wiki.

Runs inside the sync worker (single-threaded) so its mutations serialize against
manual syncs, scheduled reconciles, and webhook ingests. Progress/results are
reported through a ProgressReporter; the bot renders them as an Adaptive Card.

Data-security: results carry page paths and counts only — never page bodies or
secrets. Log the same way.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from packages.wiki_core.ai.legacy_provider_gateway import LegacyProviderGateway
from packages.wiki_core.content.file_page_store import FilePageStore
from packages.wiki_core.ingest.progress import ProgressReporter
from packages.wiki_core.ingest.sharepoint_adapter import SharePointSourceSyncAdapter
from packages.wiki_core.retrieval.index_service import VaultIndexer
from packages.wiki_core.settings import CoreSettings

LOGGER = logging.getLogger(__name__)

# Pages that structurally cannot be removed and are excluded from orphan/link checks.
_SPECIAL_PAGES = ("wiki/index.md", "wiki/overview.md", "wiki/log.md")

# Page types that are expected to cite raw sources; used for the weak-sourcing check.
_SOURCE_BACKED_TYPES = {"source", "concept", "synthesis", "entity"}

_LINT_MODEL_REPORT_PATH = "wiki/reports/last-lint.md"
_LINT_BATCH_SIZE = 12
_LINT_BODY_CHARS = 6000
_MAX_LISTED_PATHS = 50

_WIKILINK = re.compile(r"\[\[([^\]|]+)")


class VaultAdminService:
    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()
        self._settings.ensure_data_dirs()
        self._page_store = FilePageStore(self._settings)
        self._indexer = VaultIndexer(self._settings)
        self._source_sync = SharePointSourceSyncAdapter(self._settings)
        self._model_gateway = LegacyProviderGateway(self._settings)

    # --- /remove -------------------------------------------------------------

    def remove_page(self, relative_path: str, *, requested_by: str | None, progress: ProgressReporter) -> dict[str, Any]:
        """Remove a wiki page from SharePoint, disk, the index, and index.md.

        Deletes SharePoint FIRST: a sync re-downloads every wiki page from
        SharePoint, so a local-only delete would be resurrected on the next sync.
        """
        relative_path = relative_path.strip().replace("\\", "/")
        self._validate_removable(relative_path)

        progress.phase("removing")
        result: dict[str, Any] = {
            "path": relative_path,
            "sharepoint_deleted": False,
            "local_deleted": False,
            "index_rows_deleted": False,
            "index_entry_removed": False,
            "log_appended": False,
        }

        # 1) SharePoint first (source of truth). A failure here aborts before any
        # local mutation, so state stays consistent.
        result["sharepoint_deleted"] = self._source_sync.delete_wiki_file(relative_path)

        # 2) Local file.
        local_path = self._settings.repo_root / relative_path
        if local_path.exists():
            local_path.unlink()
            result["local_deleted"] = True

        # 3) Vector index + manifest (no embeddings).
        result["index_rows_deleted"] = self._indexer.delete_page(relative_path)

        # 4) index.md entry.
        result["index_entry_removed"] = self._page_store.remove_index_entry(relative_path)
        if result["index_entry_removed"]:
            self._publish(relative_path="wiki/index.md")

        # 5) Append an audit entry naming the admin, and publish the log.
        slug = Path(relative_path).stem.replace("-", " ")
        bullets = [
            f"Removed `{relative_path}`.",
            f"Requested by {requested_by or 'an admin'} via Teams /remove.",
            "Vector index and index.md updated; raw sources left untouched.",
        ]
        result["log_appended"] = self._page_store.append_log_entry(title=f"remove | {slug}", bullets=bullets)
        if result["log_appended"]:
            self._publish(relative_path="wiki/log.md")

        LOGGER.info(
            "Removed wiki page path=%s sharepoint=%s local=%s index=%s index_entry=%s by=%s",
            relative_path, result["sharepoint_deleted"], result["local_deleted"],
            result["index_rows_deleted"], result["index_entry_removed"], requested_by,
        )
        progress.set_result(result)
        progress.finish_ok()
        return result

    def _validate_removable(self, relative_path: str) -> None:
        if not relative_path.startswith("wiki/") or not relative_path.endswith(".md"):
            raise ValueError(f"Only wiki/*.md pages can be removed, not {relative_path!r}.")
        if ".." in relative_path.split("/"):
            raise ValueError("Path traversal is not allowed.")
        if relative_path in _SPECIAL_PAGES or relative_path.startswith("wiki/reports/"):
            raise ValueError(f"{relative_path} is a protected page and cannot be removed.")
        resolved = (self._settings.repo_root / relative_path).resolve()
        if not str(resolved).startswith(str(self._settings.wiki_root.resolve())):
            raise ValueError("Resolved path escapes the wiki root.")

    # --- /clean --------------------------------------------------------------

    def clean(self, progress: ProgressReporter) -> dict[str, Any]:
        """Deterministic hygiene: reconcile the index, prune orphaned state.

        No chat LLM; embeddings only happen if reconcile finds drifted pages.
        """
        progress.phase("reconciling")
        report = self._indexer.reconcile()

        progress.phase("pruning_state")
        state_pruned, pruned_paths = self._prune_source_sync_state()

        progress.phase("pruning_job_state")
        job_ids_pruned = self._cap_processed_job_ids()

        result = {
            "reindexed": len(report.indexed_files),
            "index_deleted": len(report.deleted_files),
            "state_pruned": state_pruned,
            "job_ids_pruned": job_ids_pruned,
            "reindexed_paths": report.indexed_files[:_MAX_LISTED_PATHS],
            "deleted_paths": report.deleted_files[:_MAX_LISTED_PATHS],
            "state_pruned_paths": pruned_paths[:_MAX_LISTED_PATHS],
        }
        LOGGER.info(
            "Clean complete reindexed=%d index_deleted=%d state_pruned=%d job_ids_pruned=%d",
            result["reindexed"], result["index_deleted"], state_pruned, job_ids_pruned,
        )
        progress.set_result(result)
        progress.finish_ok()
        return result

    def _prune_source_sync_state(self) -> tuple[int, list[str]]:
        """Drop state entries whose raw source no longer exists in SharePoint.

        Verified against a live listing (not the possibly-incomplete local mirror),
        so we never re-ingest a file that is merely missing locally.
        """
        path = self._settings.source_sync_state_path
        state = self._read_json_dict(path)
        if not state:
            return 0, []

        raw_root = self._settings.normalized_sharepoint_raw_root_path
        try:
            live = {event.path.strip("/") for event in self._source_sync.list_files_recursive(raw_root)}
        except Exception:
            # If we cannot confirm what still exists remotely, prune nothing —
            # deleting a live file's fingerprint would trigger a needless re-ingest.
            LOGGER.warning("Could not list raw sources; skipping state pruning", exc_info=True)
            return 0, []

        pruned = [key for key in state if key.strip("/") not in live]
        for key in pruned:
            state.pop(key, None)
        if pruned:
            path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        return len(pruned), sorted(pruned)

    def _cap_processed_job_ids(self, keep: int = 500) -> int:
        """Bound the unbounded processed-job-id list; returns how many were dropped."""
        path = self._settings.sync_job_state_path
        payload = self._read_json_dict(path)
        processed = payload.get("processed_job_ids")
        if not isinstance(processed, list) or len(processed) <= keep:
            return 0
        dropped = len(processed) - keep
        payload["processed_job_ids"] = processed[-keep:]
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return dropped

    # --- /lint ---------------------------------------------------------------

    def lint(self, progress: ProgressReporter) -> dict[str, Any]:
        """Audit the wiki (report-only): deterministic checks + an LLM pass."""
        progress.phase("scanning")
        pages = [self._page_store.load_wiki_page(path) for path in self._page_store.iter_wiki_pages()]
        findings: list[dict[str, Any]] = self._deterministic_findings(pages)

        progress.phase("auditing")
        findings.extend(self._llm_findings(pages))
        findings = self._dedupe(findings)

        progress.phase("reporting")
        report_body = self._render_report(pages, findings)
        (self._settings.repo_root / _LINT_MODEL_REPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
        (self._settings.repo_root / _LINT_MODEL_REPORT_PATH).write_text(report_body, encoding="utf-8")
        self._publish(relative_path=_LINT_MODEL_REPORT_PATH, content=report_body)

        by_type: dict[str, int] = {}
        for finding in findings:
            by_type[finding["type"]] = by_type.get(finding["type"], 0) + 1

        self._page_store.append_log_entry(
            title="lint | vault audit",
            bullets=[
                f"Scanned {len(pages)} pages; {len(findings)} findings "
                + ", ".join(f"{count} {kind}" for kind, count in sorted(by_type.items())) + ".",
                f"Report: {_LINT_MODEL_REPORT_PATH}.",
                "Report-only run; no pages were edited.",
            ],
        )
        self._publish(relative_path="wiki/log.md")

        result = {
            "pages_scanned": len(pages),
            "findings_total": len(findings),
            "by_type": by_type,
            "report_path": _LINT_MODEL_REPORT_PATH,
        }
        LOGGER.info("Lint complete pages=%d findings=%d by_type=%s", len(pages), len(findings), by_type)
        progress.set_result(result)
        progress.finish_ok()
        return result

    def _deterministic_findings(self, pages: list[Any]) -> list[dict[str, Any]]:
        content_pages = [p for p in pages if p.relative_path not in _SPECIAL_PAGES and not p.relative_path.startswith("wiki/reports/")]
        inbound: dict[str, int] = {p.relative_path: 0 for p in content_pages}
        for page in pages:
            if page.relative_path in _SPECIAL_PAGES:
                continue
            for target in self._link_targets(page.body):
                key = target if target.endswith(".md") else target + ".md"
                if key in inbound:
                    inbound[key] += 1

        findings: list[dict[str, Any]] = []
        for page in content_pages:
            if inbound.get(page.relative_path, 0) == 0:
                findings.append(self._finding("orphan", [page.relative_path],
                    "No other wiki page links to this page.",
                    "Add an inbound cross-reference from a related page, or merge/remove it.", "medium"))
            if not self._link_targets(page.body):
                findings.append(self._finding("missing_crossref", [page.relative_path],
                    "This page has no outbound wikilinks to related pages.",
                    "Link to the concepts/sources it relates to.", "low"))
            if page.page_type in _SOURCE_BACKED_TYPES and not (page.frontmatter.get("sources") or []):
                findings.append(self._finding("weak_sourcing", [page.relative_path],
                    "Source-backed page with no `sources` in frontmatter.",
                    "Cite the raw source(s) this page synthesizes.", "medium"))
        return findings

    def _llm_findings(self, pages: list[Any]) -> list[dict[str, Any]]:
        content_pages = [p for p in pages if p.relative_path not in _SPECIAL_PAGES and not p.relative_path.startswith("wiki/reports/")]
        if not content_pages:
            return []

        guidance = self._load_lint_guidance()
        index_summary = self._page_store.read_index_summary(self._settings.rag_index_summary_chars)
        system_prompt = (
            "You audit a persistent markdown wiki for the Graydaze PM training vault. "
            "You are auditing ONLY — do not rewrite pages. Report contradictions between pages, "
            "stale/superseded claims, and weak sourcing. Follow the AGENTS.md Lint workflow.\n\n"
            f"AGENTS.md lint guidance:\n{guidance}\n\n"
            "Return strict JSON: {\"findings\": [{\"type\": "
            "\"contradiction\"|\"stale\"|\"weak_sourcing\"|\"missing_crossref\"|\"other\", "
            "\"paths\": [\"wiki/...\"], \"summary\": \"...\", \"suggested_edit\": \"... or null\", "
            "\"severity\": \"low\"|\"medium\"|\"high\"}]}. Empty list if nothing is wrong."
        )

        findings: list[dict[str, Any]] = []
        for batch in self._batches(content_pages, _LINT_BATCH_SIZE):
            pages_blob = "\n\n".join(
                f"### {p.relative_path} (type={p.page_type})\n{p.body[:_LINT_BODY_CHARS]}" for p in batch
            )
            user_prompt = f"Index summary:\n{index_summary}\n\nPages to audit:\n{pages_blob}"
            try:
                raw = self._model_gateway.complete_json(system_prompt=system_prompt, user_prompt=user_prompt)
            except Exception:
                # Fail-soft per batch, matching the sync's per-file posture.
                LOGGER.warning("Lint LLM batch failed; continuing", exc_info=True)
                continue
            for item in raw.get("findings", []) if isinstance(raw, dict) else []:
                normalized = self._normalize_finding(item)
                if normalized:
                    findings.append(normalized)
        return findings

    # --- helpers -------------------------------------------------------------

    def _publish(self, *, relative_path: str, content: str | None = None) -> None:
        if content is None:
            content = (self._settings.repo_root / relative_path).read_text(encoding="utf-8")
        try:
            self._source_sync.upload_text_file(relative_path, content)
        except Exception:
            # Publishing is best-effort; the local + index state is already correct.
            LOGGER.warning("Failed to publish %s to SharePoint", relative_path, exc_info=True)

    @staticmethod
    def _link_targets(body: str) -> set[str]:
        return {match.strip() for match in _WIKILINK.findall(body) if match.strip()}

    @staticmethod
    def _finding(kind: str, paths: list[str], summary: str, suggested_edit: str | None, severity: str) -> dict[str, Any]:
        return {"type": kind, "paths": paths, "summary": summary, "suggested_edit": suggested_edit, "severity": severity}

    @staticmethod
    def _normalize_finding(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        kind = str(item.get("type") or "other").strip()
        paths = [str(p).strip() for p in (item.get("paths") or []) if str(p).strip()]
        summary = str(item.get("summary") or "").strip()
        if not summary:
            return None
        severity = str(item.get("severity") or "low").strip()
        suggested = item.get("suggested_edit")
        suggested = str(suggested).strip() if suggested else None
        return {"type": kind, "paths": paths, "summary": summary, "suggested_edit": suggested, "severity": severity}

    @staticmethod
    def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple] = set()
        unique: list[dict[str, Any]] = []
        for finding in findings:
            key = (finding["type"], tuple(sorted(finding["paths"])))
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
        return unique

    @staticmethod
    def _batches(items: list[Any], size: int):
        for start in range(0, len(items), size):
            yield items[start : start + size]

    def _render_report(self, pages: list[Any], findings: list[dict[str, Any]]) -> str:
        lines = [
            "---",
            "title: Last Lint Report",
            "type: report",
            "---",
            "",
            "# Vault Lint Report",
            "",
            f"Scanned {len(pages)} pages. {len(findings)} findings.",
            "",
        ]
        by_type: dict[str, list[dict[str, Any]]] = {}
        for finding in findings:
            by_type.setdefault(finding["type"], []).append(finding)
        for kind in sorted(by_type):
            lines.append(f"## {kind} ({len(by_type[kind])})")
            lines.append("")
            for finding in by_type[kind]:
                paths = ", ".join(f"`{p}`" for p in finding["paths"]) or "(wiki-wide)"
                lines.append(f"- **{paths}** — {finding['summary']}")
                if finding.get("suggested_edit"):
                    lines.append(f"  - Suggested: {finding['suggested_edit']}")
            lines.append("")
        lines.append("> Report-only. Findings across LLM batches may miss cross-batch contradictions.")
        return "\n".join(lines) + "\n"

    def _load_lint_guidance(self) -> str:
        path = self._settings.repo_root / "AGENTS.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return "AGENTS.md unavailable; audit for contradictions, stale claims, orphans, and weak sourcing."
        # Prefer the section whose heading mentions "Lint"; fall back to a prefix.
        match = re.search(r"(#+ [^\n]*Lint[^\n]*\n.*?)(?=\n#+ |\Z)", text, re.IGNORECASE | re.DOTALL)
        return (match.group(1) if match else text)[:4000].strip()

    @staticmethod
    def _read_json_dict(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}
