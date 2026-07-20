"""Read-only previews for admin commands (/remove, /clean).

Runs in the bot process to show an admin exactly what a destructive action will
affect *before* they confirm. Pure local reads — never opens LanceDB or calls an
LLM, and never mutates anything.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from packages.wiki_core.content.file_page_store import FilePageStore
from packages.wiki_core.content.markdown import load_wiki_page
from packages.wiki_core.settings import CoreSettings

_PROTECTED_PAGES = {"wiki/index.md", "wiki/overview.md", "wiki/log.md"}
_WIKI_PATH = re.compile(r"^wiki/[A-Za-z0-9._/-]+\.md$")

_SETTINGS: CoreSettings | None = None


class RemovePreviewError(ValueError):
    """A /remove argument was invalid or the page cannot be removed."""


@dataclass(frozen=True)
class RemovePreview:
    relative_path: str
    facts: list[tuple[str, str]]
    warnings: list[str]


@dataclass(frozen=True)
class CleanPreview:
    delete_paths: list[str] = field(default_factory=list)
    new_paths: list[str] = field(default_factory=list)
    facts: list[tuple[str, str]] = field(default_factory=list)

    @property
    def will_delete(self) -> bool:
        return bool(self.delete_paths)


def _settings(settings: CoreSettings | None) -> CoreSettings:
    global _SETTINGS
    if settings is not None:
        return settings
    if _SETTINGS is None:
        _SETTINGS = CoreSettings.from_env()
    return _SETTINGS


def normalize_wiki_path(arg: str) -> str:
    """Validate and normalize a /remove target, or raise RemovePreviewError."""
    rel = arg.strip().strip("`'\"").replace("\\", "/").strip()
    if not rel:
        raise RemovePreviewError("Usage: `/remove wiki/path/to/page.md`")
    if not rel.endswith(".md"):
        rel += ".md"
    if ".." in rel.split("/") or not _WIKI_PATH.match(rel):
        raise RemovePreviewError(f"`{arg}` isn’t a valid wiki path. Use e.g. `wiki/concepts/etc.md`.")
    if rel in _PROTECTED_PAGES or rel.startswith("wiki/reports/"):
        raise RemovePreviewError(f"`{rel}` is a protected page and can’t be removed.")
    return rel


def build_remove_preview(arg: str, *, settings: CoreSettings | None = None) -> RemovePreview:
    cfg = _settings(settings)
    rel = normalize_wiki_path(arg)

    local = cfg.repo_root / rel
    if not local.exists():
        raise RemovePreviewError(
            f"I can’t find `{rel}`. Paths are wiki-relative, e.g. `wiki/concepts/etc.md`."
        )

    page = load_wiki_page(local, cfg.repo_root)
    indexed = _is_indexed(cfg, rel)
    inbound = _inbound_links(cfg, rel)
    sources = page.frontmatter.get("sources") or []

    facts = [
        ("Title", page.title),
        ("Path", rel),
        ("Indexed", "yes" if indexed else "no"),
        ("Inbound links", str(len(inbound))),
    ]
    warnings: list[str] = []
    if sources:
        warnings.append(
            "This page was generated from a raw source, so it will return on the next sync "
            "unless that source changes. To remove it permanently, delete the raw source in SharePoint."
        )
    if inbound:
        sample = ", ".join(f"`{p}`" for p in inbound[:5])
        more = "" if len(inbound) <= 5 else f" (+{len(inbound) - 5} more)"
        warnings.append(f"{len(inbound)} page(s) link here and will have broken links: {sample}{more}.")
    return RemovePreview(relative_path=rel, facts=facts, warnings=warnings)


def build_clean_preview(*, settings: CoreSettings | None = None) -> CleanPreview:
    cfg = _settings(settings)
    manifest = _read_manifest(cfg)
    store = FilePageStore(cfg)
    existing = {p.relative_to(cfg.repo_root).as_posix() for p in store.iter_wiki_pages()}

    delete_paths = sorted(set(manifest) - existing)
    new_paths = sorted(existing - set(manifest))
    facts = [
        ("Index entries to delete", str(len(delete_paths))),
        ("Pages missing from the index", str(len(new_paths))),
    ]
    return CleanPreview(delete_paths=delete_paths, new_paths=new_paths, facts=facts)


def _read_manifest(cfg: CoreSettings) -> dict[str, str]:
    path = cfg.vector_manifest_path
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _is_indexed(cfg: CoreSettings, relative_path: str) -> bool:
    return relative_path in _read_manifest(cfg)


def _inbound_links(cfg: CoreSettings, relative_path: str) -> list[str]:
    """Other wiki pages whose body links to this page (by wikilink target)."""
    target = relative_path.removesuffix(".md")
    needle = f"[[{target}"
    hits: list[str] = []
    store = FilePageStore(cfg)
    for path in store.iter_wiki_pages():
        rel = path.relative_to(cfg.repo_root).as_posix()
        if rel == relative_path or rel == "wiki/index.md":
            continue
        try:
            if needle in path.read_text(encoding="utf-8"):
                hits.append(rel)
        except OSError:
            continue
    return hits
