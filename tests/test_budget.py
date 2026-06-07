"""Tests for Budget (token accounting)."""

from __future__ import annotations

import pytest

from codewiki.llm.budget import Budget, BudgetExceeded


def test_initial_state() -> None:
    b = Budget()
    assert b.total_tokens == 0
    assert b.request_count == 0
    assert b.token_limit is None


def test_record_accumulates() -> None:
    b = Budget()
    b.record(prompt_tokens=100, completion_tokens=50)
    b.record(prompt_tokens=200, completion_tokens=80)

    assert b.prompt_tokens == 300
    assert b.completion_tokens == 130
    assert b.total_tokens == 430
    assert b.request_count == 2


def test_record_from_response() -> None:
    b = Budget()
    b.record_from_response({"prompt_tokens": 40, "completion_tokens": 20})
    assert b.total_tokens == 60


def test_check_no_limit_never_raises() -> None:
    b = Budget()
    b.record(prompt_tokens=10_000_000)
    b.check()  # should not raise


def test_check_raises_when_over_limit() -> None:
    b = Budget(token_limit=100)
    b.record(prompt_tokens=101)
    with pytest.raises(BudgetExceeded):
        b.check()


def test_check_passes_at_limit() -> None:
    b = Budget(token_limit=100)
    b.record(prompt_tokens=100)
    b.check()  # exactly at limit — should NOT raise


def test_summary_keys() -> None:
    b = Budget(token_limit=1000)
    b.record(prompt_tokens=200, completion_tokens=50)
    s = b.summary()

    assert s["prompt_tokens"] == 200
    assert s["completion_tokens"] == 50
    assert s["total_tokens"] == 250
    assert s["requests"] == 1
    assert s["limit"] == 1000
    assert s["over_budget"] is False


def test_summary_over_budget() -> None:
    b = Budget(token_limit=100)
    b.record(prompt_tokens=150)
    assert b.summary()["over_budget"] is True
