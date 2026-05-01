"""Adapter layer between the Teams bot and the existing wiki query function.

Important: this module does *not* implement chunking, retrieval, vector search,
or LLM response generation. It only:
- loads your already-existing query function via import path
- passes the Teams request context into it safely
- normalizes the returned answer into a text response for the bot
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping

import aiohttp


LOGGER = logging.getLogger(__name__)


class WikiIntegrationError(RuntimeError):
    """Raised when the Teams bot cannot call the existing wiki query layer."""


@dataclass(frozen=True)
class WikiQueryAttachment:
    """Preprocessed attachment context passed to the wiki query backend."""

    name: str
    content_type: str
    text_content: str | None = None
    image_data_url: str | None = None


@dataclass(frozen=True)
class WikiQueryRequest:
    """Structured request context passed into the existing wiki query function."""

    request_id: str
    query: str
    user_id: str | None
    user_name: str | None
    conversation_id: str | None
    channel_id: str | None
    tenant_id: str | None
    locale: str | None
    channel_data: Any
    attachments: tuple[WikiQueryAttachment, ...] = ()


@dataclass(frozen=True)
class WikiQueryResult:
    """Normalized response returned from the existing wiki query function."""

    answer_text: str
    raw_result: Any


class WikiQueryService:
    """Thin adapter around a pre-existing wiki query callable.

    The existing callable is loaded dynamically from ``WIKI_QUERY_CALLABLE`` so
    the Teams bot stays decoupled from your retrieval/LLM implementation.
    """

    QUERY_PARAM_ALIASES = ("query", "question", "text", "prompt", "message")

    def __init__(self, query_callable: Callable[..., Any], timeout_seconds: float = 45.0) -> None:
        self._query_callable = query_callable
        self._timeout_seconds = timeout_seconds
        self._callable_name = getattr(query_callable, "__qualname__", repr(query_callable))

    @classmethod
    def from_import_path(cls, import_path: str, timeout_seconds: float = 45.0) -> "WikiQueryService":
        """Load the existing wiki query callable from ``module:function`` format."""

        if ":" not in import_path:
            raise ValueError(
                "WIKI_QUERY_CALLABLE must use the format 'package.module:function_name'."
            )

        module_name, function_name = import_path.split(":", maxsplit=1)
        module = importlib.import_module(module_name)

        try:
            query_callable = getattr(module, function_name)
        except AttributeError as exc:
            raise ValueError(
                f"Configured wiki query function '{function_name}' was not found in module '{module_name}'."
            ) from exc

        if not callable(query_callable):
            raise ValueError(
                f"Configured wiki query target '{import_path}' is not callable."
            )

        return cls(query_callable=query_callable, timeout_seconds=timeout_seconds)

    async def query(self, request: WikiQueryRequest) -> WikiQueryResult:
        """Invoke the existing wiki query function and normalize the answer text."""

        args, kwargs = self._build_arguments(request)

        LOGGER.info(
            "Calling wiki backend request_id=%s callable=%s",
            request.request_id,
            self._callable_name,
        )

        try:
            raw_result = await asyncio.wait_for(
                self._invoke(*args, **kwargs),
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise WikiIntegrationError(
                f"Wiki query callable timed out after {self._timeout_seconds} seconds."
            ) from exc
        except Exception as exc:
            raise WikiIntegrationError("Wiki query callable raised an exception.") from exc

        answer_text = self._extract_answer_text(raw_result)
        if not answer_text:
            raise WikiIntegrationError("Wiki query callable returned an empty answer.")

        return WikiQueryResult(answer_text=answer_text, raw_result=raw_result)

    async def _invoke(self, *args: Any, **kwargs: Any) -> Any:
        """Call the backend function while supporting both sync and async callables."""

        if inspect.iscoroutinefunction(self._query_callable):
            return await self._query_callable(*args, **kwargs)

        result = await asyncio.to_thread(self._query_callable, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _build_arguments(self, request: WikiQueryRequest) -> tuple[list[Any], dict[str, Any]]:
        """Map Teams context into the existing query function's signature.

        This keeps integration flexible when the existing backend function uses a
        slightly different parameter name, for example:
        - ``query_wiki(query: str)``
        - ``ask_training_vault(question: str, user_id: str | None = None)``
        - ``handle_query(prompt: str, **context)``
        """

        signature = inspect.signature(self._query_callable)
        parameters = signature.parameters

        available_context = {
            "request": request,
            "query": request.query,
            "question": request.query,
            "text": request.query,
            "prompt": request.query,
            "message": request.query,
            "request_id": request.request_id,
            "user_id": request.user_id,
            "user_name": request.user_name,
            "conversation_id": request.conversation_id,
            "channel_id": request.channel_id,
            "tenant_id": request.tenant_id,
            "locale": request.locale,
            "channel_data": request.channel_data,
            "attachments": request.attachments,
        }

        args: list[Any] = []
        kwargs: dict[str, Any] = {}
        has_var_kwargs = any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

        if has_var_kwargs:
            kwargs.update(available_context)
            return args, kwargs

        query_mapped = False
        for name, parameter in parameters.items():
            if name == "request":
                kwargs[name] = request
                query_mapped = True
                continue

            if name in self.QUERY_PARAM_ALIASES:
                kwargs[name] = request.query
                query_mapped = True
                continue

            if name in available_context:
                kwargs[name] = available_context[name]

        if not query_mapped:
            for parameter in parameters.values():
                if parameter.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                ) and parameter.name not in kwargs:
                    args.append(request.query)
                    query_mapped = True
                    break

        if not query_mapped:
            for parameter in parameters.values():
                if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
                    args.append(request.query)
                    query_mapped = True
                    break

        if not query_mapped:
            raise WikiIntegrationError(
                "Could not map the Teams message text into the configured wiki query function signature."
            )

        return args, kwargs

    def _extract_answer_text(self, raw_result: Any) -> str:
        """Normalize several common backend return shapes into plain response text."""

        if raw_result is None:
            return ""

        if isinstance(raw_result, str):
            return raw_result.strip()

        if isinstance(raw_result, Mapping):
            for key in ("answer", "response", "text", "content", "result"):
                value = raw_result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return str(dict(raw_result)).strip()

        for attribute_name in ("answer", "response", "text", "content", "result"):
            if hasattr(raw_result, attribute_name):
                value = getattr(raw_result, attribute_name)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return str(raw_result).strip()


class HttpWikiQueryService:
    """HTTP-based adapter for a separately hosted wiki query service.

    Use this when the Teams bot cannot directly import the existing wiki backend,
    for example when the backend runs on another machine that already has Egnyte
    access or private network connectivity to the vault.
    """

    def __init__(self, base_url: str, timeout_seconds: float = 45.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def query(self, request: WikiQueryRequest) -> WikiQueryResult:
        """POST the Teams query to an existing HTTP query endpoint.

        Expected response shapes are the same as the local callable adapter: a
        plain text body or JSON containing ``answer``/``response``/``text``.
        """

        payload = {
            "request_id": request.request_id,
            "query": request.query,
            "user_id": request.user_id,
            "user_name": request.user_name,
            "conversation_id": request.conversation_id,
            "channel_id": request.channel_id,
            "tenant_id": request.tenant_id,
            "locale": request.locale,
            "attachments": [asdict(attachment) for attachment in request.attachments],
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self._base_url, json=payload) as response:
                    response.raise_for_status()

                    content_type = response.headers.get("Content-Type", "")
                    if "application/json" in content_type.lower():
                        raw_result = await response.json()
                    else:
                        raw_result = await response.text()
        except asyncio.TimeoutError as exc:
            raise WikiIntegrationError(
                f"Wiki HTTP endpoint timed out after {self._timeout_seconds} seconds."
            ) from exc
        except aiohttp.ClientError as exc:
            raise WikiIntegrationError("Wiki HTTP endpoint request failed.") from exc

        answer_text = WikiQueryService(lambda *_args, **_kwargs: None)._extract_answer_text(raw_result)
        if not answer_text:
            raise WikiIntegrationError("Wiki HTTP endpoint returned an empty answer.")

        return WikiQueryResult(answer_text=answer_text, raw_result=raw_result)
