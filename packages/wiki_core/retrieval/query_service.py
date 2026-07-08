"""Query engine for the maintained training vault wiki."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from packages.contracts.identity import CallerIdentity
from packages.contracts.query import Citation, QueryAttachment, QueryRequest, QueryResponse
from packages.wiki_core.ai.legacy_provider_gateway import LegacyProviderGateway
from packages.wiki_core.content.file_page_store import FilePageStore
from packages.wiki_core.content.markdown import clean_obsidian_links, parse_sources_metadata, strip_source_tags
from packages.wiki_core.retrieval.lancedb_adapter import LanceDbVectorStore
from packages.wiki_core.retrieval.models import RetrievedChunk
from packages.wiki_core.settings import CoreSettings


LOGGER = logging.getLogger(__name__)


def _build_system_prompt(index_summary: str) -> str:
    return (
        "You are the Graydaze PM Training Vault answer engine. "
        "Answer only from the retrieved wiki context and the vault index summary. "
        "Do not use outside knowledge, do not speculate, and explicitly say when the wiki context is insufficient.\n\n"
        "Vault map summary from wiki/index.md:\n"
        f"{index_summary}\n\n"
        "Grounding rules:\n"
        "- Use only the retrieved wiki context and the summary above. If they are insufficient, say so plainly.\n"
        "- Uploaded attachment content is user-supplied context, not a wiki source.\n"
        "- If retrieved context conflicts, state the conflict explicitly.\n"
        "- Do NOT add inline citations or source tags. Never write '[Source: ...]' or '[Sources: ...]' "
        "anywhere in the answer — the cited sources are shown to the user separately.\n\n"
        "Formatting rules (the answer is rendered into a structured Microsoft Teams card — optimize for quick scanning):\n"
        "- Organize the answer with Markdown section headers: use '## ' for main sections and '### ' for subsections. "
        "Open with a one- or two-sentence lead paragraph before the first header. Do not use a single-'#' title.\n"
        "- Separate ideas into short paragraphs (2-3 sentences) with a blank line between them.\n"
        "- Use '## '/'### ' headers for section titles — never a numbered list as section headings. "
        "Use '- ' bullets for options or criteria, and numbered lists ('1.', '2.', ...) only for genuinely sequential steps within a section.\n"
        "- **Bold** key terms, metrics, and definitions on first mention.\n"
        "- For any math or formulas use plain Unicode (for example: EAC = AC + ETC, CPI = EV ÷ AC, x², √x, Σ). "
        "Never use LaTeX or $...$ delimiters — Teams cannot render them.\n"
        "- Convert Obsidian wikilinks into plain bold labels, for example [[wiki/concepts/etc|Estimate to Complete]] "
        "becomes **Estimate to Complete**. Never emit raw wikilink syntax ([[...]]).\n"
        "- Do not use Markdown tables or images. Prefer bold over inline code.\n"
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


def _build_attachment_context(attachments: list[QueryAttachment]) -> str:
    sections: list[str] = []
    for index, attachment in enumerate(attachments, start=1):
        details = [f"Attachment {index}", f"Name: {attachment.name}", f"Content-Type: {attachment.content_type}"]
        if attachment.text_content:
            details.append("Extracted Text:")
            details.append(attachment.text_content.strip())
        elif attachment.image_data_url:
            details.append("Image supplied for visual analysis.")
        sections.append("\n".join(details))
    return "\n\n".join(sections)


def _normalize_attachments(raw_attachments: list[Any]) -> list[QueryAttachment]:
    attachments: list[QueryAttachment] = []
    for item in raw_attachments:
        if isinstance(item, QueryAttachment):
            attachments.append(item)
            continue
        if isinstance(item, Mapping):
            attachments.append(
                QueryAttachment(
                    name=str(item.get("name") or "attachment"),
                    content_type=str(item.get("content_type") or "application/octet-stream"),
                    text_content=(str(item.get("text_content")) if item.get("text_content") is not None else None),
                    image_data_url=(str(item.get("image_data_url")) if item.get("image_data_url") is not None else None),
                    blob_ref=(str(item.get("blob_ref")) if item.get("blob_ref") is not None else None),
                )
            )
    return attachments


def _build_retrieval_query(query: str, attachments: list[QueryAttachment]) -> str:
    if not attachments:
        return query

    parts = [query.strip()]
    for attachment in attachments:
        name = attachment.name.strip()
        if name:
            parts.append(f"Attachment name: {name}")
        if attachment.text_content:
            parts.append("Attachment excerpt: " + attachment.text_content[:1500])
    combined = "\n".join(part for part in parts if part)
    return combined[:4000] or query


def _build_user_content(*, query: str, chunks: list[RetrievedChunk], attachments: list[QueryAttachment]) -> str | list[dict[str, Any]]:
    text_prompt = _build_user_prompt(query=query, chunks=chunks)
    if attachments:
        attachment_context = _build_attachment_context(attachments)
        text_prompt = text_prompt + "\n\nAttachment context:\n" + attachment_context

    image_inputs = [attachment for attachment in attachments if attachment.image_data_url]
    if not image_inputs:
        return text_prompt

    content: list[dict[str, Any]] = [{"type": "text", "text": text_prompt}]
    for attachment in image_inputs:
        content.append({"type": "image_url", "image_url": {"url": attachment.image_data_url}})
    return content


class QueryService:
    def __init__(self, settings: CoreSettings | None = None) -> None:
        self._settings = settings or CoreSettings.from_env()
        self._settings.validate_llm()
        self._page_store = FilePageStore(self._settings)
        self._model_gateway = LegacyProviderGateway(self._settings)
        self._vector_store = LanceDbVectorStore(self._settings)

    async def query(self, request: QueryRequest) -> QueryResponse:
        if not self._vector_store.is_ready():
            raise RuntimeError(
                f"Wiki index is not ready at {self._settings.vector_db_path} "
                f"(table '{self._settings.vector_table_name}'). Build or refresh the index before serving queries."
            )

        attachments = _normalize_attachments(list(request.attachments))
        retrieval_query = _build_retrieval_query(request.query, attachments)
        query_embedding = (await self._model_gateway.embed_texts_async([retrieval_query]))[0]
        results = self._vector_store.search(query_embedding, top_k=self._settings.rag_top_k)
        LOGGER.info(
            "request_id=%s vault retrieval returned %d results (top_k=%d)",
            request.request_id, len(results), self._settings.rag_top_k,
        )

        chunks = [
            RetrievedChunk(
                document=str(result.get("text", "")).strip(),
                metadata={
                    "id": result.get("id", ""),
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
            return QueryResponse(answer_text="I couldn’t find anything relevant in the current wiki for that question.")

        index_summary = self._page_store.read_index_summary(self._settings.rag_index_summary_chars)
        answer = await self._model_gateway.complete_text(
            system_prompt=_build_system_prompt(index_summary=index_summary),
            user_prompt=_build_user_content(query=request.query, chunks=chunks, attachments=attachments),
            temperature=0.1,
            requires_vision=any(attachment.image_data_url for attachment in attachments),
        )

        citations = tuple(
            Citation(
                title=str(chunk.metadata.get("title", "Untitled")),
                path=str(chunk.metadata.get("path", "")),
                section=str(chunk.metadata.get("section", "Overview")),
                sources=tuple(parse_sources_metadata(chunk.metadata.get("sources"))),
            )
            for chunk in chunks
        )
        diagnostics = {
            "chunk_ids": [str(chunk.metadata.get("id", "")) for chunk in chunks],
            "top_k": self._settings.rag_top_k,
        }
        return QueryResponse(
            answer_text=strip_source_tags(clean_obsidian_links(answer)),
            citations=citations,
            retrieval_diagnostics=diagnostics,
        )


async def query_vault_structured(query: str, request_id: str, **kwargs) -> QueryResponse:
    identity = CallerIdentity(
        user_id=kwargs.get("user_id"),
        user_name=kwargs.get("user_name"),
        tenant_id=kwargs.get("tenant_id"),
        client_app=kwargs.get("client_app") or "legacy-query-wrapper",
        channel_id=kwargs.get("channel_id"),
        conversation_id=kwargs.get("conversation_id"),
        locale=kwargs.get("locale"),
    )
    request = QueryRequest(
        request_id=request_id,
        query=query,
        identity=identity,
        attachments=tuple(kwargs.get("attachments") or ()),
        client_context={"channel_data": kwargs.get("channel_data")},
    )
    return await QueryService().query(request)
