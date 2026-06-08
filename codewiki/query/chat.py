"""Grounded Q&A over wiki pages and local retrieval index."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.index.retriever import retrieve
from codewiki.llm.budget import Budget, BudgetExceeded
from codewiki.llm.client import LLMClient
from codewiki.llm.retry import with_retry
from codewiki.utils import safe_slug


def _load_wiki_context(wiki_root: Path, limit: int = 6) -> list[tuple[str, str]]:
    pages = sorted(p for p in wiki_root.rglob("*.md") if p.name not in {"index.md", "log.md"})
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
) -> str:
    """Answer a question using local retrieval + optional LLM synthesis."""
    index_dir = cfg.run.cache_dir / "index"
    snippets = retrieve(index_dir, question, top_k=8)
    wiki_context = _load_wiki_context(cfg.wiki.output_dir)
    run_budget = budget or Budget(token_limit=cfg.run.token_budget)

    answer = _fallback_answer(question, snippets)

    try:
        llm_text = asyncio.run(_llm_answer(cfg, question, snippets, wiki_context, run_budget))
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
