"""Top-level pipeline orchestrators used by CLI commands."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.graph.code_graph import CodeGraph
from codewiki.index.chunker import chunk_symbols
from codewiki.index.store import IndexStore
from codewiki.ingest.parser import parse_symbols
from codewiki.ingest.repo_map import build_repo_map
from codewiki.ingest.source import resolve_source
from codewiki.ingest.walker import walk_source
from codewiki.llm.budget import Budget
from codewiki.lenses import get_lens
from codewiki.lint.health import run_lint
from codewiki.query.chat import answer_question
from codewiki.query.impact import impact as _impact
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


def _chat_source_from_manifest(cfg: CodeWikiConfig) -> str | None:
    manifest_path = cfg.wiki.output_dir / ".codewiki_manifest.json"
    if not manifest_path.exists():
        return None

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    source_root = str(payload.get("source_root", "")).strip() if isinstance(payload, dict) else ""
    if not source_root:
        return None

    source_path = Path(source_root)
    if not source_path.exists() or not source_path.is_dir():
        return None
    return source_root


def run_generate(source: str, cfg: CodeWikiConfig) -> GenerateResult:
    source_root, cleanup = resolve_source(source, cfg)
    try:
        lens = get_lens(cfg.generation.lens)
        file_records = walk_source(source_root, cfg)
        symbols = parse_symbols(file_records, parser_backend=cfg.ingest.parser_backend)
        repo_map = build_repo_map(source_root, file_records, symbols)
        signals = detect_signals(
            file_records,
            symbols,
            extra_detectors=lens.extra_signal_detectors(),
        )
        snippets = chunk_symbols(file_records, symbols)

        index_path = cfg.run.cache_dir / "index"
        store = IndexStore(index_path)
        store.build(snippets)

        code_graph = CodeGraph()
        code_graph.build_from_repo(file_records, symbols, repo_map)

        run_budget = Budget(token_limit=cfg.run.token_budget)
        pages = generate_wiki(
            source_root=source_root,
            cfg=cfg,
            files=file_records,
            symbols=symbols,
            repo_map=repo_map,
            signals=signals,
            code_graph=code_graph,
            budget=run_budget,
            lens=lens,
        )

        manifest_path = cfg.wiki.output_dir / ".codewiki_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "source_root": str(source_root),
                    "files": {f.path: f.hash for f in file_records},
                },
                indent=2,
            ),
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


def run_chat(
    question: str,
    cfg: CodeWikiConfig,
    file_back: bool = False,
    source: str | None = None,
) -> str:
    budget = Budget(token_limit=cfg.run.token_budget)
    chat_source = source or _chat_source_from_manifest(cfg)
    if not chat_source:
        return answer_question(question, cfg, file_back=file_back, budget=budget)

    source_root, cleanup = resolve_source(chat_source, cfg)
    try:
        file_records = walk_source(source_root, cfg)
        symbols = parse_symbols(file_records, parser_backend=cfg.ingest.parser_backend)
        repo_map = build_repo_map(source_root, file_records, symbols)
        code_graph = CodeGraph()
        code_graph.build_from_repo(file_records, symbols, repo_map)

        return answer_question(
            question,
            cfg,
            file_back=file_back,
            budget=budget,
            code_graph=code_graph,
        )
    finally:
        cleanup()


def run_lint_pipeline(cfg: CodeWikiConfig, source_root: Path | None = None) -> dict:
    return run_lint(cfg.wiki.output_dir, source_root=source_root)


def run_impact(target: str, source: str, cfg: CodeWikiConfig) -> dict:
    """Ingest source, build graph, and return impact analysis for *target*."""
    source_path, cleanup = resolve_source(source, cfg)
    try:
        file_records = walk_source(source_path, cfg)
        symbols = parse_symbols(file_records, parser_backend=cfg.ingest.parser_backend)
        repo_map = build_repo_map(source_path, file_records, symbols)
        code_graph = CodeGraph()
        code_graph.build_from_repo(file_records, symbols, repo_map)
        return _impact(target, code_graph, wiki_root=cfg.wiki.output_dir)
    finally:
        cleanup()
