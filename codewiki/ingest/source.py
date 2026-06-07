"""Resolve local paths or Git URLs into a readable source directory."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from git import Repo

from codewiki.config import CodeWikiConfig


def _is_git_url(source: str) -> bool:
    return source.startswith(("http://", "https://", "git@")) or source.endswith(".git")


def _apply_token(url: str, cfg: CodeWikiConfig) -> str:
    if not cfg.ingest.git_token_env:
        return url
    token = os.environ.get(cfg.ingest.git_token_env, "").strip()
    if not token or not url.startswith("https://"):
        return url
    return "https://" + token + "@" + url[len("https://") :]


def resolve_source(source: str, cfg: CodeWikiConfig) -> tuple[Path, Callable[[], None]]:
    """
    Resolve source into a local directory path.

    Returns:
        (root_path, cleanup_fn)
    """
    if not _is_git_url(source):
        root = Path(source).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Source directory not found: {root}")
        return root, (lambda: None)

    tmp = Path(tempfile.mkdtemp(prefix="codewiki_src_"))
    url = _apply_token(source, cfg)
    Repo.clone_from(url, tmp)

    def _cleanup() -> None:
        shutil.rmtree(tmp, ignore_errors=True)

    return tmp, _cleanup
