"""Query engine for the Graydaze training vault RAG pipeline."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import lancedb

from rag_backend.config import BackendSettings
from rag_backend.indexer import VaultIndexer
from rag_backend.llm import complete_text_async, embed_texts_async
from rag_backend.markdown import clean_obsidian_links, parse_sources_metadata, split_frontmatter


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    document: str
    metadata: dict[str, Any]


def _load_index_summary(settings: BackendSettings) -> str:
    try:
        text = settings.index_path.read_text(encoding="utf-8")
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
            entries.append(clean_obsidian_links(line[2:].strip()))

    flush()
    summary = "\n".join(section_lines).strip()
    return summary[: settings.rag_index_summary_chars] or "Index unavailable."


def _build_system_prompt(index_summary: str) -> str:
    return (
        "You are the Graydaze PM Training Vault answer engine. "
        "Answer only from the retrieved wiki context and the vault index summary. "
        "Do not use outside knowledge, do not speculate, and explicitly say when the wiki context is insufficient.\n\n"
        "Vault map summary from wiki/index.md:\n"
        f"{index_summary}\n\n"
        "Rules:\n"
        "- Every factual sentence grounded in retrieved context must end with a citation in the exact format [Source: Title].\n"
        "- If a sentence is supported by multiple chunks from the same title, cite it once with that title.\n"
        "- If retrieved context conflicts, state the conflict explicitly and cite each claim separately.\n"
        "- Convert Obsidian wikilinks into plain bold labels for Teams, for example [[wiki/concepts/etc|Estimate to Complete]] becomes **Estimate to Complete**.\n"
        "- Do not emit raw wikilink syntax anywhere in the answer.\n"
        "- Keep the answer concise and useful for Graydaze PMs.\n"
    )


def _build_user_prompt(query: str, chunks: list[RetrievedChunk]) -> str:
    context_sections: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        title = str(chunk.metadata.get("title", "Untitled"))
        section = str(chunk.metadata.get("section", "Overview"))
        path = str(chunk.metadata.get("path", ""))
        sources = parse_sources_metadata(chunk.metadata.get("sources"))
        source_text = ", ".join(sources) if sources else "[]"
        context_sections.append(
            f"Context {index}\n"
            f"Title: {title}\n"
            f"Path: {path}\n"
            f"Section: {section}\n"
            f"Sources: {source_text}\n"
            f"Content:\n{chunk.document.strip()}"
        )

    joined_context = "\n\n".join(context_sections) if context_sections else "[No retrieved context]"
    return f"User question:\n{query.strip()}\n\nRetrieved wiki context:\n{joined_context}"


def _open_table(settings: BackendSettings):
    settings.ensure_data_dirs()
    db = lancedb.connect(str(settings.vector_db_path))
    response = db.list_tables()
    table_names = set(getattr(response, "tables", []) or [])
    if settings.vector_table_name not in table_names:
        return None
    return db.open_table(settings.vector_table_name)


async def query_vault(query: str, request_id: str, **kwargs) -> str:
    """Retrieve wiki chunks, call the LLM, and return a cited answer string."""

    settings = BackendSettings.from_env()
    settings.validate_openai()

    table = _open_table(settings)
    if table is None or table.count_rows() == 0:
        LOGGER.info("RAG index empty; building initial index request_id=%s", request_id)
        VaultIndexer(settings).build()
        table = _open_table(settings)

    if table is None or table.count_rows() == 0:
        return "I couldn’t find anything relevant in the current wiki for that question."

    query_embedding = (await embed_texts_async([query], settings))[0]
    results = table.search(query_embedding).limit(settings.rag_top_k).to_list()

    chunks = [
        RetrievedChunk(
            document=str(result.get("text", "")).strip(),
            metadata={
                "title": result.get("title", "Untitled"),
                "section": result.get("section", "Overview"),
                "path": result.get("path", ""),
                "sources": result.get("sources", "[]"),
                "type": result.get("type", "unknown"),
                "distance": result.get("_distance"),
            },
        )
        for result in results
        if str(result.get("text", "")).strip()
    ]

    if not chunks:
        return "I couldn’t find anything relevant in the current wiki for that question."

    index_summary = _load_index_summary(settings)
    answer = await complete_text_async(
        system_prompt=_build_system_prompt(index_summary=index_summary),
        user_prompt=_build_user_prompt(query=query, chunks=chunks),
        settings=settings,
        temperature=0.1,
    )
    return clean_obsidian_links(answer)
