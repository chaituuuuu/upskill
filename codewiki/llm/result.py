"""Structured result contract for LLM chat calls."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class LLMResult:
    """Normalized response payload returned by chat completions."""

    text: str
    usage: dict[str, int]
    model: str
    finish_reason: str = ""