"""
Token accounting and cost estimation for CodeWiki runs.

Tracks prompt + completion tokens across all LLM calls in a run.
Used by ``--dry-run`` (estimate only) and logged in ``log.md``.

Usage::

    from codewiki.llm.budget import Budget

    budget = Budget(token_limit=500_000)
    budget.record(prompt_tokens=200, completion_tokens=80)
    budget.record(prompt_tokens=150, completion_tokens=60)

    print(budget.summary())
    # {'prompt_tokens': 350, 'completion_tokens': 140, 'total_tokens': 490, ...}

    budget.check()   # raises BudgetExceeded if over the limit
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """Raised when a run exceeds its configured token budget."""


@dataclass
class Budget:
    """Thread-safe token counter for a single run."""

    token_limit: int | None = None
    """Hard cap; ``None`` means unlimited."""

    prompt_tokens: int = field(default=0, init=False)
    completion_tokens: int = field(default=0, init=False)
    request_count: int = field(default=0, init=False)

    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @staticmethod
    def estimate(text: str) -> int:
        """Estimate tokens from raw text using the shared heuristic."""
        return int(len(text.split()) * 1.35)

    def record(self, *, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        """
        Record token usage from a completed LLM call.

        Args:
            prompt_tokens:     Tokens used in the prompt.
            completion_tokens: Tokens used in the completion.
        """
        with self._lock:
            self.prompt_tokens += prompt_tokens
            self.completion_tokens += completion_tokens
            self.request_count += 1

        self.enforce()

    def record_from_response(self, usage: dict) -> None:
        """
        Record from an OpenAI-style ``usage`` dict
        (``{"prompt_tokens": N, "completion_tokens": M}``).
        """
        self.record(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )

    def enforce(self) -> None:
        """Raise :exc:`BudgetExceeded` if the token limit has been exceeded."""
        if self.token_limit is not None and self.total_tokens > self.token_limit:
            raise BudgetExceeded(
                f"Token budget exceeded: {self.total_tokens:,} > {self.token_limit:,}"
            )

    def check(self) -> None:
        """Backward-compatible alias for :meth:`enforce`."""
        self.enforce()

    def summary(self) -> dict:
        """Return a dict snapshot of current usage."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "requests": self.request_count,
            "limit": self.token_limit,
            "over_budget": (
                self.total_tokens > self.token_limit
                if self.token_limit is not None
                else False
            ),
        }
