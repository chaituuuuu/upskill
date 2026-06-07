"""Tests for LLMClient and retry logic using mocked HTTP."""

from __future__ import annotations

import pytest
import respx
import httpx

from codewiki.config import LLMConfig
from codewiki.llm.client import LLMClient
from codewiki.llm.retry import with_retry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(**kwargs) -> LLMConfig:
    defaults = {
        "base_url": "http://fake-llm/v1",
        "model": "test-model",
        "temperature": 0.0,
        "max_tokens": 100,
        "timeout_s": 5.0,
    }
    defaults.update(kwargs)
    return LLMConfig(**defaults)


def _chat_response(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _embed_response(vectors: list[list[float]]) -> dict:
    return {
        "object": "list",
        "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
        "model": "test-embed",
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_returns_content() -> None:
    with respx.mock(base_url="http://fake-llm") as mock:
        mock.post("/v1/chat/completions").mock(
            return_value=httpx.Response(200, json=_chat_response("Hello!"))
        )
        async with LLMClient(_make_cfg()) as client:
            result = await client.chat([{"role": "user", "content": "hi"}])
    assert result == "Hello!"


@pytest.mark.asyncio
async def test_chat_raises_on_4xx() -> None:
    with respx.mock(base_url="http://fake-llm"):
        respx.post("http://fake-llm/v1/chat/completions").mock(
            return_value=httpx.Response(401, json={"error": "Unauthorized"})
        )
        async with LLMClient(_make_cfg()) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await client.chat([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_embed_returns_vectors() -> None:
    cfg = _make_cfg(embedding_model="test-embed")
    with respx.mock(base_url="http://fake-llm"):
        respx.post("http://fake-llm/v1/embeddings").mock(
            return_value=httpx.Response(200, json=_embed_response([[0.1, 0.2], [0.3, 0.4]]))
        )
        async with LLMClient(cfg) as client:
            vecs = await client.embed(["text a", "text b"])
    assert len(vecs) == 2
    assert vecs[0] == pytest.approx([0.1, 0.2])


@pytest.mark.asyncio
async def test_embed_raises_without_model() -> None:
    cfg = _make_cfg()  # no embedding_model
    async with LLMClient(cfg) as client:
        with pytest.raises(RuntimeError, match="Embeddings not configured"):
            await client.embed(["text"])


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_errors() -> None:
    call_count = 0

    async def flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            resp = httpx.Response(503, json={"error": "service unavailable"})
            raise httpx.HTTPStatusError("503", request=httpx.Request("POST", "http://x"), response=resp)
        return "ok"

    result = await with_retry(flaky, retries=4, base_delay=0.001)
    assert result == "ok"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_reraises_non_retryable() -> None:
    async def bad() -> str:
        resp = httpx.Response(400, json={"error": "bad request"})
        raise httpx.HTTPStatusError("400", request=httpx.Request("POST", "http://x"), response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(bad, retries=3, base_delay=0.001)


@pytest.mark.asyncio
async def test_retry_exhausted_raises() -> None:
    async def always_503() -> str:
        resp = httpx.Response(503, json={})
        raise httpx.HTTPStatusError("503", request=httpx.Request("POST", "http://x"), response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        await with_retry(always_503, retries=2, base_delay=0.001)
