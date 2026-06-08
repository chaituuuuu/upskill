"""Top-level pipeline orchestrators used by CLI commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.index.chunker import chunk_symbols
from codewiki.index.store import IndexStore
from codewiki.ingest.parser import parse_symbols
from codewiki.ingest.repo_map import build_repo_map
from codewiki.ingest.source import resolve_source
from codewiki.ingest.walker import walk_source
from codewiki.llm.budget import Budget
from codewiki.lint.health import run_lint
from codewiki.query.chat import answer_question
from codewiki.signals.detectors import detect_signals
from codewiki.wiki.generator import generate_wiki
from codewiki.wiki.updater import update_wiki


@dataclass(slots=True)
class GenerateResult:
    source_root: Path
    wiki_root: Path
    files: int
    symbols: int
    signals: int
    pages: int


def run_generate(source: str, cfg: CodeWikiConfig) -> GenerateResult:
    source_root, cleanup = resolve_source(source, cfg)
    try:
        file_records = walk_source(source_root, cfg)
        symbols = parse_symbols(file_records)
        repo_map = build_repo_map(source_root, file_records, symbols)
        signals = detect_signals(file_records, symbols)
        snippets = chunk_symbols(file_records, symbols)

        index_path = cfg.run.cache_dir / "index"
        store = IndexStore(index_path)
        store.build(snippets)

        run_budget = Budget(token_limit=cfg.run.token_budget)
        pages = generate_wiki(
            source_root=source_root,
            cfg=cfg,
            files=file_records,
            symbols=symbols,
            repo_map=repo_map,
            signals=signals,
            budget=run_budget,
        )

        manifest_path = cfg.wiki.output_dir / ".codewiki_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps({"files": {f.path: f.hash for f in file_records}}, indent=2),
            encoding="utf-8",
        )

        return GenerateResult(
            source_root=source_root,
            wiki_root=cfg.wiki.output_dir,
            files=len(file_records),
            symbols=len(symbols),
            signals=len(signals),
            pages=pages,
        )
    finally:
        cleanup()


def run_update(source: str, cfg: CodeWikiConfig) -> dict:
    source_root, cleanup = resolve_source(source, cfg)
    try:
        return update_wiki(source_root, cfg)
    finally:
        cleanup()


def run_chat(question: str, cfg: CodeWikiConfig, file_back: bool = False) -> str:
    budget = Budget(token_limit=cfg.run.token_budget)
    return answer_question(question, cfg, file_back=file_back, budget=budget)


def run_lint_pipeline(cfg: CodeWikiConfig) -> dict:
    return run_lint(cfg.wiki.output_dir)
