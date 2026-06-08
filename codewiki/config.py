"""
Configuration loader for CodeWiki.

Precedence (lowest → highest):
  1. codewiki.yaml  (file)
  2. .env           (dotenv file)
  3. Environment variables
  4. CLI flags      (applied by cli.py after loading this)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# LLM sub-config
# ---------------------------------------------------------------------------


class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEWIKI_LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    base_url: str = Field(
        default="https://api.openai.com/v1",
        description="Base URL for the OpenAI-compatible endpoint.",
    )
    model: str = Field(
        default="gpt-4o-mini",
        description="Model name passed to /chat/completions.",
    )
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key (or leave blank for local models that skip auth).",
    )
    api_key_env: Optional[str] = Field(
        default=None,
        description="Name of the env var that holds the real key (alternative to api_key).",
    )
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, gt=0)
    timeout_s: float = Field(default=120.0, gt=0)

    # Embeddings (optional — leave blank to skip vector indexing)
    embedding_model: Optional[str] = Field(default=None)
    embedding_base_url: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def _resolve_api_key(self) -> "LLMConfig":
        """If api_key_env is set, read the key from that env var."""
        if self.api_key_env:
            raw = os.environ.get(self.api_key_env, "")
            if raw:
                self.api_key = SecretStr(raw)
        return self

    def get_api_key(self) -> str:
        """Return the plaintext API key (or empty string for local models)."""
        if self.api_key:
            return self.api_key.get_secret_value()
        return ""


# ---------------------------------------------------------------------------
# Ingest sub-config
# ---------------------------------------------------------------------------


class IngestConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEWIKI_INGEST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    include_globs: list[str] = Field(
        default_factory=lambda: ["**/*"],
        description="Glob patterns to include (relative to repo root).",
    )
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            "**/node_modules/**",
            "**/.git/**",
            "**/vendor/**",
            "**/dist/**",
            "**/build/**",
            "**/__pycache__/**",
            "**/*.min.js",
            "**/*.lock",
            "**/package-lock.json",
            "**/poetry.lock",
            "**/yarn.lock",
        ],
        description="Glob patterns to exclude.",
    )
    skip_binary: bool = Field(default=True)
    max_file_size_kb: int = Field(default=512, description="Skip files larger than this.")
    git_token_env: Optional[str] = Field(
        default=None,
        description="Env var name holding a personal access token for private Git repos.",
    )
    parser_backend: Literal["auto", "ast", "tree-sitter"] = Field(
        default="auto",
        description="Symbol parser backend strategy.",
    )


# ---------------------------------------------------------------------------
# Wiki sub-config
# ---------------------------------------------------------------------------


class WikiConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEWIKI_WIKI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    output_dir: Path = Field(default=Path("wiki"))
    strict_grounding: bool = Field(
        default=True,
        description="Refuse to write claims without code citations.",
    )
    default_audience: Literal["business", "technical", "both"] = Field(default="both")


# ---------------------------------------------------------------------------
# Generation sub-config
# ---------------------------------------------------------------------------


class GenerationConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEWIKI_GENERATION_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    map_reduce_concurrency: int = Field(
        default=4,
        gt=0,
        description="Maximum parallel map-stage file summaries.",
    )
    lens: Literal["business", "onboarding", "compliance", "security", "ai_opportunity"] = Field(
        default="business",
        description="Lens that controls extra analysis pages and detectors.",
    )
    summary_cache: bool = Field(
        default=True,
        description="Enable hash-keyed file summary cache.",
    )


# ---------------------------------------------------------------------------
# Embedding sub-config
# ---------------------------------------------------------------------------


class EmbeddingConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEWIKI_EMBEDDING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = Field(
        default=False,
        description="Enable vector retrieval over snippet embeddings.",
    )
    store: Literal["faiss", "numpy"] = Field(
        default="faiss",
        description="Preferred vector backend (falls back gracefully if unavailable).",
    )


# ---------------------------------------------------------------------------
# Run / budget sub-config
# ---------------------------------------------------------------------------


class RunConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEWIKI_RUN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    concurrency: int = Field(default=8, gt=0, description="Max parallel LLM calls.")
    max_files: Optional[int] = Field(
        default=None,
        description="Cap on files processed (useful for dry-runs / staged runs).",
    )
    token_budget: Optional[int] = Field(
        default=None,
        description="Hard stop when total tokens exceed this.",
    )
    dry_run: bool = Field(
        default=False,
        description="Estimate cost/tokens only — do not write any files.",
    )
    cache_dir: Path = Field(
        default=Path(".codewiki_cache"),
        description="Directory for file-hash summarization cache.",
    )


# ---------------------------------------------------------------------------
# Root config — merged from yaml + env + CLI
# ---------------------------------------------------------------------------


class CodeWikiConfig(BaseSettings):
    """
    Root configuration object.

    Load via :func:`load_config` which applies yaml → env → CLI precedence.
    """

    model_config = SettingsConfigDict(
        env_prefix="CODEWIKI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    wiki: WikiConfig = Field(default_factory=WikiConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    run: RunConfig = Field(default_factory=RunConfig)

    @field_validator("llm", "ingest", "wiki", "generation", "embedding", "run", mode="before")
    @classmethod
    def _coerce_sub(cls, v: object) -> object:
        # Allow passing dicts (from YAML) — pydantic will construct the model
        return v


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

_DEFAULT_YAML = Path("codewiki.yaml")


def load_config(
    yaml_path: Path | None = None,
    overrides: dict | None = None,
) -> CodeWikiConfig:
    """
    Build a :class:`CodeWikiConfig` by merging sources in precedence order.

    Args:
        yaml_path:  Path to the YAML config file.  Defaults to ``codewiki.yaml``
                    in the current working directory (silently skipped if absent).
        overrides:  Dict of values applied last (from CLI flags).  Keys must
                    match the nested pydantic field names using ``__`` as
                    separator, e.g. ``{"llm__model": "gpt-4o"}``.

    Returns:
        A fully populated :class:`CodeWikiConfig`.
    """
    yaml_path = yaml_path or _DEFAULT_YAML
    yaml_data: dict = {}

    if yaml_path.exists():
        with yaml_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
            if isinstance(raw, dict):
                yaml_data = raw

    # Merge yaml_data + overrides into a flat init dict for pydantic-settings.
    # Sub-sections in YAML (e.g. {"llm": {"model": "x"}}) are passed as nested
    # dicts; pydantic will construct sub-models from them.
    init: dict = dict(yaml_data)
    if overrides:
        # Flatten double-underscore keys into nested dicts
        for key, val in overrides.items():
            parts = key.split("__")
            target = init
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = val

    return CodeWikiConfig(**init)
