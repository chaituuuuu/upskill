"""LLM-backed repository summarization used by wiki generation."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import asdict, dataclass

from codewiki.config import CodeWikiConfig
from codewiki.llm.budget import Budget
from codewiki.llm.client import LLMClient
from codewiki.llm.retry import with_retry
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import make_citation
from codewiki.wiki.cache import SummaryCache


@dataclass(slots=True)
class FileSummary:
    path: str
    responsibility: str
    business_relevance: str
    key_symbols: list[str]
    citations: list[str]
    confidence: str
    source_hash: str


@dataclass(slots=True)
class ModuleSummary:
    name: str
    responsibility: str
    capabilities: list[str]
    files: list[str]
    citations: list[str]
    confidence: str


@dataclass(slots=True)
class SystemSummary:
    executive_summary: str
    capabilities: list[str]
    audiences: dict[str, str]
    confidence: str


@dataclass(slots=True)
class SummaryBundle:
    file_summaries: list[FileSummary]
    module_summaries: list[ModuleSummary]
    system_summary: SystemSummary


def summarize_repository(
    *,
    cfg: CodeWikiConfig,
    files: list[FileRecord],
    symbols: list[Symbol],
    repo_map: RepoMap,
    signals: list[Signal],
    budget: Budget | None = None,
    use_cache: bool = True,
) -> SummaryBundle:
    """Run file map stage and module/system reduce stages for wiki generation."""
    run_budget = budget or Budget(token_limit=cfg.run.token_budget)
    cache_enabled = use_cache and cfg.generation.summary_cache
    cache = SummaryCache(cfg.run.cache_dir, enabled=cache_enabled)

    file_summaries = asyncio.run(
        _summarize_files_map(
            cfg=cfg,
            files=files,
            symbols=symbols,
            signals=signals,
            cache=cache,
            budget=run_budget,
        )
    )
    module_summaries = _reduce_modules(file_summaries, signals)
    system_summary = _reduce_system(module_summaries, repo_map, files, symbols)
    return SummaryBundle(
        file_summaries=sorted(file_summaries, key=lambda item: item.path),
        module_summaries=sorted(module_summaries, key=lambda item: item.name),
        system_summary=system_summary,
    )


async def _summarize_files_map(
    *,
    cfg: CodeWikiConfig,
    files: list[FileRecord],
    symbols: list[Symbol],
    signals: list[Signal],
    cache: SummaryCache,
    budget: Budget,
) -> list[FileSummary]:
    by_path = _symbols_by_path(symbols)
    signal_by_path = _signal_names_by_path(signals)
    map_limit = min(cfg.run.concurrency, cfg.generation.map_reduce_concurrency)
    sem = asyncio.Semaphore(max(1, map_limit))

    async with LLMClient(cfg.llm) as client:
        tasks = [
            asyncio.create_task(
                _summarize_one_file(
                    file=file,
                    file_symbols=by_path.get(file.path, []),
                    file_signal_names=signal_by_path.get(file.path, []),
                    cache=cache,
                    client=client,
                    sem=sem,
                    budget=budget,
                )
            )
            for file in files
        ]
        return await asyncio.gather(*tasks)


async def _summarize_one_file(
    *,
    file: FileRecord,
    file_symbols: list[Symbol],
    file_signal_names: list[str],
    cache: SummaryCache,
    client: LLMClient,
    sem: asyncio.Semaphore,
    budget: Budget,
) -> FileSummary:
    cached = cache.load(file.hash)
    if cached is not None:
        return _coerce_file_summary(file, cached, file_symbols)

    fallback = _fallback_file_summary(file, file_symbols)
    messages = _file_summary_prompt(file, file_symbols, file_signal_names)

    try:
        async with sem:
            result = await with_retry(client.chat, messages, retries=2)
        budget.record_from_response(result.usage)
        payload = _extract_json_object(result.text)
        if payload is None:
            payload = await _repair_json_payload(client, messages, result.text, sem, budget)
        if payload is None:
            return fallback

        summary = _coerce_file_summary(file, payload, file_symbols)
        cache.save(file.hash, asdict(summary))
        return summary
    except Exception:
        return fallback


async def _repair_json_payload(
    client: LLMClient,
    original_messages: list[dict[str, str]],
    invalid_text: str,
    sem: asyncio.Semaphore,
    budget: Budget,
) -> dict | None:
    repair_messages = [
        *original_messages,
        {"role": "assistant", "content": invalid_text},
        {
            "role": "user",
            "content": (
                "Return valid JSON only with exactly these keys: "
                "responsibility, business_relevance, key_symbols, citations, confidence."
            ),
        },
    ]
    try:
        async with sem:
            repaired = await with_retry(client.chat, repair_messages, retries=1)
        budget.record_from_response(repaired.usage)
    except Exception:
        return None
    return _extract_json_object(repaired.text)


def _file_summary_prompt(
    file: FileRecord,
    file_symbols: list[Symbol],
    file_signal_names: list[str],
) -> list[dict[str, str]]:
    symbol_names = [sym.name for sym in file_symbols[:12]]
    preview = file.text[:12000]

    user_prompt = (
        "Summarize this source file for CodeWiki. "
        "Return JSON only with keys responsibility, business_relevance, key_symbols, citations, confidence.\n"
        "Rules:\n"
        "- key_symbols is an array of symbol names\n"
        "- citations is an array of path:Lx-Ly values\n"
        "- confidence must be high, medium, or low\n"
        "- ground claims in the provided file content\n\n"
        f"File path: {file.path}\n"
        f"Language: {file.lang}\n"
        f"Known symbols: {', '.join(symbol_names) if symbol_names else '(none)'}\n"
        f"Detected signals: {', '.join(file_signal_names) if file_signal_names else '(none)'}\n\n"
        "File content:\n"
        f"{preview}"
    )

    return [
        {
            "role": "system",
            "content": (
                "You produce grounded software documentation summaries with explicit citations."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def _extract_json_object(text: str) -> dict | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        parsed = json.loads(raw[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_file_summary(
    file: FileRecord,
    payload: dict,
    file_symbols: list[Symbol],
) -> FileSummary:
    fallback = _fallback_file_summary(file, file_symbols)
    responsibility = str(payload.get("responsibility", "")).strip() or fallback.responsibility
    business_relevance = (
        str(payload.get("business_relevance", "")).strip() or fallback.business_relevance
    )
    key_symbols = _coerce_string_list(payload.get("key_symbols")) or fallback.key_symbols
    citations = _coerce_citations(payload.get("citations"), file.path) or fallback.citations
    confidence = _coerce_confidence(payload.get("confidence"))

    return FileSummary(
        path=file.path,
        responsibility=responsibility,
        business_relevance=business_relevance,
        key_symbols=key_symbols,
        citations=citations,
        confidence=confidence,
        source_hash=file.hash,
    )


def _fallback_file_summary(file: FileRecord, file_symbols: list[Symbol]) -> FileSummary:
    symbol_names = [sym.name for sym in file_symbols[:8]]
    return FileSummary(
        path=file.path,
        responsibility="Core implementation details for this file.",
        business_relevance=(
            "Contributes to repository capabilities inferred from structure and code evidence."
        ),
        key_symbols=symbol_names,
        citations=[make_citation(file.path, 1, 1)],
        confidence="low",
        source_hash=file.hash,
    )


def _coerce_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in out:
            out.append(text)
    return out


def _coerce_citations(value: object, path: str) -> list[str]:
    values = _coerce_string_list(value)
    if not values:
        return []

    out: list[str] = []
    for item in values:
        if ":L" not in item:
            continue
        if item not in out:
            out.append(item)

    if not out:
        return [make_citation(path, 1, 1)]
    return out


def _coerce_confidence(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"high", "medium", "low"}:
        return raw
    return "medium"


def _symbols_by_path(symbols: list[Symbol]) -> dict[str, list[Symbol]]:
    out: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in symbols:
        out[symbol.path].append(symbol)
    return out


def _signal_names_by_path(signals: list[Signal]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for signal in signals:
        for cite in signal.evidence:
            path = cite.split(":L", 1)[0]
            if signal.name not in out[path]:
                out[path].append(signal.name)
    return out


def _reduce_modules(file_summaries: list[FileSummary], signals: list[Signal]) -> list[ModuleSummary]:
    by_module: dict[str, list[FileSummary]] = defaultdict(list)
    for summary in file_summaries:
        module = summary.path.split("/", 1)[0]
        by_module[module].append(summary)

    module_signal_capabilities = _capabilities_by_module(signals)
    out: list[ModuleSummary] = []
    for module_name, items in by_module.items():
        capabilities = sorted(set(module_signal_capabilities.get(module_name, [])))
        if not capabilities:
            capabilities = ["core repository behavior"]

        citations = _unique_preserve_order(
            cite for item in items for cite in item.citations
        )[:24]
        confidence = _merge_confidence([item.confidence for item in items])
        responsibility = _merge_responsibility(items)

        out.append(
            ModuleSummary(
                name=module_name,
                responsibility=responsibility,
                capabilities=capabilities,
                files=sorted(item.path for item in items),
                citations=citations,
                confidence=confidence,
            )
        )
    return out


def _reduce_system(
    module_summaries: list[ModuleSummary],
    repo_map: RepoMap,
    files: list[FileRecord],
    symbols: list[Symbol],
) -> SystemSummary:
    capabilities = _unique_preserve_order(
        capability
        for module in module_summaries
        for capability in module.capabilities
    )
    if not capabilities:
        capabilities = ["platform operations"]

    confidence = _merge_confidence([module.confidence for module in module_summaries])
    executive_summary = (
        f"Repository analysis covered {len(files)} files across {len(module_summaries)} "
        f"top-level components with {len(symbols)} symbols. "
        "Summaries are grounded with code citations and focused on business capabilities."
    )
    frameworks = ", ".join(repo_map.frameworks[:5]) if repo_map.frameworks else "none detected"
    business_view = (
        "Key capabilities include "
        f"{', '.join(capabilities[:6])}. "
        "These were inferred from API routes, integrations, and data/domain patterns."
    )
    technical_view = (
        f"Framework signals: {frameworks}. "
        f"Entrypoints discovered: {len(repo_map.entrypoints)}. "
        "Module pages capture responsibilities, symbols, and supporting citations."
    )

    return SystemSummary(
        executive_summary=executive_summary,
        capabilities=capabilities,
        audiences={"business": business_view, "technical": technical_view},
        confidence=confidence,
    )


def _capabilities_by_module(signals: list[Signal]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for signal in signals:
        label = signal.name.strip()
        if signal.type == "api_route":
            seg = label.split(" ", 1)[-1].split("/")
            if len(seg) > 1 and seg[1]:
                label = seg[1].replace("-", " ").replace("_", " ").strip()
            else:
                label = "general api"

        for cite in signal.evidence:
            path = cite.split(":L", 1)[0]
            module = path.split("/", 1)[0]
            if label and label not in out[module]:
                out[module].append(label)
    return out


def _merge_responsibility(items: list[FileSummary]) -> str:
    snippets = _unique_preserve_order(
        item.responsibility.strip()
        for item in items
        if item.responsibility.strip()
    )
    if not snippets:
        return "Technical responsibilities inferred from grouped files."
    if len(snippets) == 1:
        return snippets[0]
    return f"{snippets[0]} Also includes: {snippets[1]}"


def _merge_confidence(values: list[str]) -> str:
    normalized = [val for val in values if val in {"high", "medium", "low"}]
    if not normalized:
        return "medium"
    if "low" in normalized:
        return "low"
    if "medium" in normalized:
        return "medium"
    return "high"


def _unique_preserve_order(values) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
    return out