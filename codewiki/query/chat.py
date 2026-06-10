"""Grounded Q&A over wiki pages and local retrieval index."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
import re
from typing import TYPE_CHECKING

from codewiki.config import CodeWikiConfig
from codewiki.index.retriever import retrieve
from codewiki.llm.budget import Budget, BudgetExceeded
from codewiki.llm.client import LLMClient
from codewiki.llm.retry import with_retry
from codewiki.utils import safe_slug

if TYPE_CHECKING:
    from codewiki.graph.code_graph import CodeGraph


_INDEX_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")


def _indexed_pages(wiki_root: Path) -> list[Path]:
    index_path = wiki_root / "index.md"
    if not index_path.exists():
        return []

    try:
        text = index_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    pages: list[Path] = []
    seen: set[str] = set()
    for link in _INDEX_LINK_RE.findall(text):
        rel = str(Path(link).as_posix())
        if rel in {"index.md", "log.md"} or rel.endswith(".proposed.md"):
            continue
        if rel in seen:
            continue

        target = wiki_root / rel
        if target.exists() and target.is_file():
            pages.append(target)
            seen.add(rel)

    return pages


def _load_wiki_context(wiki_root: Path, limit: int = 6) -> list[tuple[str, str]]:
    pages = _indexed_pages(wiki_root)
    if not pages:
        pages = sorted(
            p
            for p in wiki_root.rglob("*.md")
            if p.name not in {"index.md", "log.md"} and not p.name.endswith(".proposed.md")
        )

    out: list[tuple[str, str]] = []
    for page in pages[:limit]:
        rel = page.relative_to(wiki_root).as_posix()
        text = page.read_text(encoding="utf-8", errors="ignore")
        out.append((rel, text[:3000]))
    return out


def _fallback_answer(question: str, snippets) -> str:
    lines = [
        f"## Question\n{question}",
        "",
        "## Grounded Answer",
        "Based on indexed code snippets, the most relevant implementation evidence is listed below.",
        "",
        "## Evidence",
    ]
    if not snippets:
        lines.append("- No relevant snippets were found in the current local index.")
    for snip in snippets:
        preview = " ".join(snip.text.strip().split())[:240]
        lines.append(f"- {snip.cite}: {preview}")
    return "\n".join(lines)


async def _llm_answer(
    cfg: CodeWikiConfig,
    question: str,
    snippets,
    wiki_context,
    budget: Budget | None = None,
) -> str:
    messages = [
        {
            "role": "system",
            "content": (
                "You are CodeWiki's grounded assistant. Answer using only provided context and "
                "include citations exactly as given (path:Lx-Ly). If uncertain, say so."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question:\n{question}\n\n"
                f"Code snippets:\n{chr(10).join(f'- {s.cite}\n{s.text[:700]}' for s in snippets)}\n\n"
                f"Wiki context:\n{chr(10).join(f'## {p}\n{t[:600]}' for p, t in wiki_context)}"
            ),
        },
    ]
    async with LLMClient(cfg.llm) as client:
        result = await with_retry(client.chat, messages, retries=2)
        if budget is not None:
            budget.record_from_response(result.usage)
        return result.text


def answer_question(
    question: str,
    cfg: CodeWikiConfig,
    file_back: bool = False,
    budget: Budget | None = None,
    code_graph: CodeGraph | None = None,
) -> str:
    return asyncio.run(
        _answer_question_async(
            question,
            cfg,
            file_back=file_back,
            budget=budget,
            code_graph=code_graph,
        )
    )


async def _answer_question_async(
    question: str,
    cfg: CodeWikiConfig,
    *,
    file_back: bool = False,
    budget: Budget | None = None,
    code_graph: CodeGraph | None = None,
) -> str:
    """Answer a question using local retrieval + optional LLM synthesis."""
    index_dir = cfg.run.cache_dir / "index"
    run_budget = budget or Budget(token_limit=cfg.run.token_budget)
    snippets = await retrieve(
        index_dir,
        question,
        cfg=cfg,
        top_k=8,
        budget=run_budget,
        code_graph=code_graph,
    )
    wiki_context = _load_wiki_context(cfg.wiki.output_dir)

    answer = _fallback_answer(question, snippets)

    try:
        llm_text = await _llm_answer(cfg, question, snippets, wiki_context, run_budget)
        if llm_text.strip():
            answer = llm_text.strip()
    except BudgetExceeded:
        raise
    except Exception:
        # Keep fallback answer when the model endpoint is unavailable.
        pass

    if file_back:
        dst = cfg.wiki.output_dir / "workflows" / f"qa-{safe_slug(question)[:48]}.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        dst.write_text(
            f"# Filed Answer\n\nGenerated at {stamp} UTC\n\n{answer}\n",
            encoding="utf-8",
        )

    return answer
