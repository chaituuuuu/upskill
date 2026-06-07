"""Tests for config loading and precedence."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from codewiki.config import load_config


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_no_file(tmp_path: Path) -> None:
    """load_config with no yaml file returns sensible defaults."""
    orig = Path.cwd()
    os.chdir(tmp_path)
    try:
        cfg = load_config()
    finally:
        os.chdir(orig)

    assert cfg.llm.base_url == "https://api.openai.com/v1"
    assert cfg.llm.model == "gpt-4o-mini"
    assert cfg.llm.temperature == 0.0
    assert cfg.wiki.strict_grounding is True
    assert cfg.run.dry_run is False


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def test_yaml_overrides_defaults(tmp_path: Path) -> None:
    """Values in codewiki.yaml override defaults."""
    cfg_file = tmp_path / "codewiki.yaml"
    cfg_file.write_text(
        yaml.dump(
            {
                "llm": {
                    "base_url": "http://localhost:11434/v1",
                    "model": "qwen2.5-coder:32b",
                },
                "run": {"dry_run": True},
            }
        )
    )
    cfg = load_config(yaml_path=cfg_file)

    assert cfg.llm.base_url == "http://localhost:11434/v1"
    assert cfg.llm.model == "qwen2.5-coder:32b"
    assert cfg.run.dry_run is True
    # Untouched defaults survive
    assert cfg.llm.temperature == 0.0


def test_empty_yaml_is_ok(tmp_path: Path) -> None:
    """An empty YAML file is treated as {} (no error)."""
    cfg_file = tmp_path / "codewiki.yaml"
    cfg_file.write_text("")
    cfg = load_config(yaml_path=cfg_file)
    assert cfg.llm.model == "gpt-4o-mini"


# ---------------------------------------------------------------------------
# CLI overrides (double-underscore key expansion)
# ---------------------------------------------------------------------------


def test_cli_overrides_yaml(tmp_path: Path) -> None:
    """CLI overrides (double-underscore keys) take highest precedence."""
    cfg_file = tmp_path / "codewiki.yaml"
    cfg_file.write_text(yaml.dump({"llm": {"model": "gpt-4o"}}))

    cfg = load_config(yaml_path=cfg_file, overrides={"llm__model": "gpt-4-turbo"})
    assert cfg.llm.model == "gpt-4-turbo"


def test_nested_override_multiple_keys(tmp_path: Path) -> None:
    """Multiple independent CLI overrides all apply."""
    cfg = load_config(
        overrides={
            "llm__model": "claude-3-5-sonnet",
            "llm__temperature": 0.2,
            "run__dry_run": True,
        }
    )
    assert cfg.llm.model == "claude-3-5-sonnet"
    assert cfg.llm.temperature == pytest.approx(0.2)
    assert cfg.run.dry_run is True


# ---------------------------------------------------------------------------
# api_key_env resolution
# ---------------------------------------------------------------------------


def test_api_key_env_resolution(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """api_key_env reads the key from the named env var."""
    monkeypatch.setenv("MY_TEST_KEY", "sk-test-12345")
    cfg_file = tmp_path / "codewiki.yaml"
    cfg_file.write_text(yaml.dump({"llm": {"api_key_env": "MY_TEST_KEY"}}))

    cfg = load_config(yaml_path=cfg_file)
    assert cfg.llm.get_api_key() == "sk-test-12345"


def test_no_key_returns_empty_string(tmp_path: Path) -> None:
    """get_api_key() returns '' when no key is configured (local models)."""
    cfg = load_config()
    assert cfg.llm.get_api_key() == ""
