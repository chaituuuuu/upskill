"""
Async, OpenAI-compatible LLM client.

Works with any provider that speaks the OpenAI REST schema:
  OpenAI, Azure OpenAI, Ollama, vLLM, LM Studio, OpenRouter, Together, etc.

Usage::

    from codewiki.config import load_config
    from codewiki.llm.client import LLMClient

    cfg = load_config()
    client = LLMClient(cfg.llm)

    result = await client.chat([{"role": "user", "content": "Hello!"}])
    print(result.text)
    vectors = await client.embed(["some text"])
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from codewiki.config import LLMConfig
from codewiki.llm.result import LLMResult

logger = logging.getLogger(__name__)

_CHAT_PATH = "/chat/completions"
_EMBED_PATH = "/embeddings"


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    prompt_tokens = _to_int(usage.get("prompt_tokens"))
    completion_tokens = _to_int(usage.get("completion_tokens"))
    total_tokens = _to_int(usage.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


class LLMClient:
    """Thin async wrapper around any OpenAI-compatible endpoint."""

    def __init__(self, cfg: LLMConfig) -> None:
        self._cfg = cfg
        headers: dict[str, str] = {"Content-Type": "application/json"}
        key = cfg.get_api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        self._http = httpx.AsyncClient(
            base_url=cfg.base_url.rstrip("/"),
            headers=headers,
            timeout=cfg.timeout_s,
        )

    # ------------------------------------------------------------------
    # Chat completions
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **extra: Any,
    ) -> LLMResult:
        """
        Send a chat-completions request and return normalized completion data.

        Args:
            messages:    List of ``{"role": ..., "content": ...}`` dicts.
            model:       Override the configured model for this call.
            temperature: Override the configured temperature.
            max_tokens:  Override the configured max_tokens.
            **extra:     Any extra fields forwarded to the API payload.

        Returns:
            Structured completion data including usage and finish metadata.

        Raises:
            httpx.HTTPStatusError: On 4xx/5xx from the endpoint.
        """
        payload: dict[str, Any] = {
            "model": model or self._cfg.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self._cfg.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self._cfg.max_tokens,
            **extra,
        }
        logger.debug("chat -> model=%s messages=%d", payload["model"], len(messages))
        resp = await self._http.post(_CHAT_PATH, json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        content = message.get("content", "") if isinstance(message, dict) else ""
        if isinstance(content, list):
            content = "".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in content
            )

        finish_reason = ""
        if isinstance(choice, dict):
            finish_reason = str(choice.get("finish_reason", "") or "")

        return LLMResult(
            text=str(content or ""),
            usage=_normalize_usage(data.get("usage")),
            model=str(data.get("model") or payload["model"]),
            finish_reason=finish_reason,
        )

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
    ) -> list[list[float]]:
        """
        Embed a list of texts using the configured embedding endpoint.

        Falls back to the chat endpoint/model if no dedicated embedding
        endpoint is configured.

        Args:
            texts: Strings to embed.
            model: Override the embedding model.

        Returns:
            List of float vectors, one per input text.

        Raises:
            RuntimeError:            If embeddings are not configured.
            httpx.HTTPStatusError:   On 4xx/5xx from the endpoint.
        """
        embed_model = model or self._cfg.embedding_model
        if not embed_model:
            raise RuntimeError(
                "Embeddings not configured. Set llm.embedding_model in codewiki.yaml "
                "or CODEWIKI_LLM_EMBEDDING_MODEL env var."
            )

        # Use a separate client if a dedicated embedding base_url is set
        client = self._http
        own_client = False
        if self._cfg.embedding_base_url:
            key = self._cfg.get_api_key()
            hdrs = {"Content-Type": "application/json"}
            if key:
                hdrs["Authorization"] = f"Bearer {key}"
            client = httpx.AsyncClient(
                base_url=self._cfg.embedding_base_url.rstrip("/"),
                headers=hdrs,
                timeout=self._cfg.timeout_s,
            )
            own_client = True

        try:
            payload = {"model": embed_model, "input": texts}
            logger.debug("embed -> model=%s texts=%d", embed_model, len(texts))
            resp = await client.post(_EMBED_PATH, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return [item["embedding"] for item in data["data"]]
        finally:
            if own_client:
                await client.aclose()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()