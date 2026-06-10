"""LLM-backed repository summarization used by wiki generation."""

from __future__ import annotations

import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from codewiki.config import CodeWikiConfig
from codewiki.llm.budget import Budget
from codewiki.llm.client import LLMClient
from codewiki.llm.retry import with_retry
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import make_citation
from codewiki.wiki.cache import SummaryCache

if TYPE_CHECKING:
    from codewiki.graph.code_graph import CodeGraph

# Bump when the cached summary schema changes so stale entries are re-summarized.
_SUMMARY_SCHEMA_VERSION = 2


@dataclass(slots=True)
class FileSummary:
    path: str
    responsibility: str
    business_relevance: str
    key_symbols: list[str]
    citations: list[str]
    confidence: str
    source_hash: str
    what_it_does: str = ""
    interfaces: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    data_touched: list[str] = field(default_factory=list)
    key_behaviors: list[str] = field(default_factory=list)
    schema_version: int = _SUMMARY_SCHEMA_VERSION


@dataclass(slots=True)
class ModuleSummary:
    name: str
    responsibility: str
    capabilities: list[str]
    files: list[str]
    citations: list[str]
    confidence: str
    what_it_does: str = ""
    interfaces: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    owned_data: list[str] = field(default_factory=list)
    architecture_notes: str = ""
    risks: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SystemSummary:
    executive_summary: str
    capabilities: list[str]
    audiences: dict[str, str]
    confidence: str
    what_the_service_does: str = ""
    primary_users: list[str] = field(default_factory=list)
    business_outcomes: list[str] = field(default_factory=list)
    key_workflows: list[str] = field(default_factory=list)
    external_systems: list[str] = field(default_factory=list)
    architecture_overview: str = ""


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
    code_graph: CodeGraph | None = None,
    budget: Budget | None = None,
    use_cache: bool = True,
    prompt_addendum: str = "",
) -> SummaryBundle:
    """Run file map stage and module/system reduce stages for wiki generation."""
    run_budget = budget or Budget(token_limit=cfg.run.token_budget)
    cache_enabled = use_cache and cfg.generation.summary_cache
    cache = SummaryCache(cfg.run.cache_dir, enabled=cache_enabled)
    neighbor_context_by_path = _graph_neighbor_context_by_file(
        files=files,
        code_graph=code_graph,
        max_tokens=600,
    )

    file_summaries = asyncio.run(
        _summarize_files_map(
            cfg=cfg,
            files=files,
            symbols=symbols,
            signals=signals,
            cache=cache,
            budget=run_budget,
            prompt_addendum=prompt_addendum,
            neighbor_context_by_path=neighbor_context_by_path,
        )
    )

    fallback_module_summaries = _reduce_modules(file_summaries, signals)
    module_summaries = asyncio.run(
        _reduce_modules_llm(
            cfg=cfg,
            file_summaries=file_summaries,
            fallback_module_summaries=fallback_module_summaries,
            signals=signals,
            budget=run_budget,
            prompt_addendum=prompt_addendum,
        )
    )

    fallback_system_summary = _reduce_system(module_summaries, repo_map, files, symbols)
    system_summary = asyncio.run(
        _reduce_system_llm(
            cfg=cfg,
            module_summaries=module_summaries,
            fallback_system_summary=fallback_system_summary,
            repo_map=repo_map,
            files=files,
            symbols=symbols,
            budget=run_budget,
            prompt_addendum=prompt_addendum,
        )
    )

    return SummaryBundle(
        file_summaries=sorted(file_summaries, key=lambda item: item.path),
        module_summaries=sorted(module_summaries, key=lambda item: item.name),
        system_summary=system_summary,
    )


def estimate_repository_tokens(
    *,
    cfg: CodeWikiConfig,
    files: list[FileRecord],
    symbols: list[Symbol],
    repo_map: RepoMap,
    signals: list[Signal],
    code_graph: CodeGraph | None = None,
    use_cache: bool = True,
    prompt_addendum: str = "",
) -> int:
    """Estimate generation tokens from the exact prompt builders used by summarization."""
    cache_enabled = use_cache and cfg.generation.summary_cache
    cache = SummaryCache(cfg.run.cache_dir, enabled=cache_enabled)

    by_path = _symbols_by_path(symbols)
    signal_by_path = _signal_names_by_path(signals)
    neighbor_context_by_path = _graph_neighbor_context_by_file(
        files=files,
        code_graph=code_graph,
        max_tokens=600,
    )

    estimated = 0
    file_summaries_for_reduce: list[FileSummary] = []
    for file in files:
        file_symbols = by_path.get(file.path, [])
        cached = _load_valid_cache(cache, file.hash)
        if cached is not None:
            file_summaries_for_reduce.append(_coerce_file_summary(file, cached, file_symbols))
            continue

        messages = _file_summary_prompt(
            file,
            file_symbols,
            signal_by_path.get(file.path, []),
            prompt_addendum,
            neighbor_context_by_path.get(file.path, ""),
        )
        estimated += _estimate_messages_tokens(messages)
        file_summaries_for_reduce.append(_fallback_file_summary(file, file_symbols))

    module_fallbacks = _reduce_modules(file_summaries_for_reduce, signals)
    by_module = _group_file_summaries_by_module(file_summaries_for_reduce)
    capability_by_module = _capabilities_by_module(signals)

    for module in module_fallbacks:
        module_messages = _module_summary_prompt(
            module_name=module.name,
            file_summaries=by_module.get(module.name, []),
            capabilities=capability_by_module.get(module.name, module.capabilities),
            prompt_addendum=prompt_addendum,
        )
        estimated += _estimate_messages_tokens(module_messages)

    system_messages = _system_summary_prompt(
        module_summaries=module_fallbacks,
        repo_map=repo_map,
        files=files,
        symbols=symbols,
        prompt_addendum=prompt_addendum,
    )
    estimated += _estimate_messages_tokens(system_messages)
    return estimated


async def _summarize_files_map(
    *,
    cfg: CodeWikiConfig,
    files: list[FileRecord],
    symbols: list[Symbol],
    signals: list[Signal],
    cache: SummaryCache,
    budget: Budget,
    prompt_addendum: str,
    neighbor_context_by_path: dict[str, str],
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
                    prompt_addendum=prompt_addendum,
                    neighbor_context=neighbor_context_by_path.get(file.path, ""),
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
    prompt_addendum: str,
    neighbor_context: str,
) -> FileSummary:
    cached = _load_valid_cache(cache, file.hash)
    if cached is not None:
        return _coerce_file_summary(file, cached, file_symbols)

    fallback = _fallback_file_summary(file, file_symbols)
    messages = _file_summary_prompt(
        file,
        file_symbols,
        file_signal_names,
        prompt_addendum,
        neighbor_context,
    )

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
    required_keys: str = (
        "responsibility, what_it_does, business_relevance, interfaces, dependencies, "
        "data_touched, key_behaviors, key_symbols, citations, confidence"
    ),
) -> dict | None:
    repair_messages = [
        *original_messages,
        {"role": "assistant", "content": invalid_text},
        {
            "role": "user",
            "content": (
                "Return valid JSON only with exactly these keys: "
                f"{required_keys}."
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
    prompt_addendum: str,
    neighbor_context: str,
) -> list[dict[str, str]]:
    symbol_names = [sym.name for sym in file_symbols[:12]]
    preview = file.text[:12000]

    user_prompt = (
        "Summarize this source file for CodeWiki, a grounded code knowledge base.\n"
        "Return JSON only with keys: responsibility, what_it_does, business_relevance, "
        "interfaces, dependencies, data_touched, key_behaviors, key_symbols, citations, confidence.\n"
        "Field rules:\n"
        "- responsibility: one sentence on the file's technical role\n"
        "- what_it_does: 1-2 plain-language sentences a non-engineer can understand\n"
        "- business_relevance: why this matters to the product or business, or 'unknown'\n"
        "- interfaces: array of public functions/classes/routes this file exposes\n"
        "- dependencies: array of notable modules/services/libraries it relies on\n"
        "- data_touched: array of data entities/models/tables/queues it reads or writes\n"
        "- key_behaviors: array of notable behaviors (auth, validation, retry, error handling, side effects)\n"
        "- key_symbols: array of symbol names\n"
        "- citations: array of path:Lx-Ly values that ground the claims\n"
        "- confidence: high, medium, or low\n"
        "Grounding rules:\n"
        "- Use only the provided file content and neighbor context; never invent paths or symbols\n"
        "- If a field cannot be grounded, return an empty array or the string 'unknown'\n\n"
        f"File path: {file.path}\n"
        f"Language: {file.lang}\n"
        f"Known symbols: {', '.join(symbol_names) if symbol_names else '(none)'}\n"
        f"Detected signals: {', '.join(file_signal_names) if file_signal_names else '(none)'}\n"
        f"{neighbor_context + chr(10) if neighbor_context else ''}\n"
        "File content:\n"
        f"{preview}"
    )

    system_prompt = (
        "You are CodeWiki's code analyst. You produce grounded, dual-layer documentation: a "
        "plain-language business view and a precise technical view (interfaces, dependencies, "
        "data, behaviors). Every non-trivial claim must be supported by the provided code and "
        "carry a path:Lx-Ly citation. Never fabricate files, symbols, or behavior; if evidence "
        "is missing, say 'unknown'. Respond with valid JSON only."
    )
    if prompt_addendum.strip():
        system_prompt = f"{system_prompt}\nLens guidance: {prompt_addendum.strip()}"

    return [
        {
            "role": "system",
            "content": system_prompt,
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
        what_it_does=_coerce_text(payload.get("what_it_does"), fallback.what_it_does),
        interfaces=_coerce_string_list(payload.get("interfaces")) or fallback.interfaces,
        dependencies=_coerce_string_list(payload.get("dependencies")) or fallback.dependencies,
        data_touched=_coerce_string_list(payload.get("data_touched")) or fallback.data_touched,
        key_behaviors=_coerce_string_list(payload.get("key_behaviors")) or fallback.key_behaviors,
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


def _coerce_text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def _load_valid_cache(cache: SummaryCache, source_hash: str) -> dict | None:
    """Return a cached payload only when it matches the current summary schema."""
    cached = cache.load(source_hash)
    if cached is None:
        return None
    if cached.get("schema_version") != _SUMMARY_SCHEMA_VERSION:
        return None
    return cached


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


def _graph_neighbor_context_by_file(
    *,
    files: list[FileRecord],
    code_graph: CodeGraph | None,
    max_tokens: int,
) -> dict[str, str]:
    if code_graph is None:
        return {}

    imports_by_file: dict[str, set[str]] = defaultdict(set)
    dependents_by_file: dict[str, set[str]] = defaultdict(set)
    calls_by_file: dict[str, Counter[str]] = defaultdict(Counter)

    for from_id, to_id, edge_type in code_graph.backend.all_edges():
        if edge_type == "imports" and from_id.startswith("file:") and to_id.startswith("file:"):
            src = from_id.removeprefix("file:")
            dst = to_id.removeprefix("file:")
            imports_by_file[src].add(dst)
            dependents_by_file[dst].add(src)
            continue

        if edge_type != "calls":
            continue

        from_node = code_graph.backend.get_node(from_id)
        if from_node is None or from_node.kind != "symbol":
            continue

        source_file = str(from_node.meta.get("path", "")).strip()
        if not source_file:
            continue

        target_node = code_graph.backend.get_node(to_id)
        if target_node is None:
            target_name = to_id
        else:
            target_name = str(target_node.meta.get("name") or target_node.label or to_id)
        calls_by_file[source_file][target_name] += 1

    context_by_file: dict[str, str] = {}
    for file in files:
        imports = sorted(imports_by_file.get(file.path, set()))
        dependents = sorted(dependents_by_file.get(file.path, set()))
        top_calls = calls_by_file.get(file.path, Counter()).most_common(8)
        context = _render_graph_neighbor_context(
            imports=imports,
            dependents=dependents,
            top_calls=top_calls,
            max_tokens=max_tokens,
        )
        if context:
            context_by_file[file.path] = context

    return context_by_file


def _render_graph_neighbor_context(
    *,
    imports: list[str],
    dependents: list[str],
    top_calls: list[tuple[str, int]],
    max_tokens: int,
) -> str:
    if not imports and not dependents and not top_calls:
        return ""

    import_caps = [12, 8, 5, 3, 2]
    dependent_caps = [12, 8, 5, 3, 2]
    call_caps = [8, 6, 4, 3, 2]

    for i_cap in import_caps:
        for d_cap in dependent_caps:
            for c_cap in call_caps:
                import_text = ", ".join(imports[:i_cap]) if imports else "(none)"
                dependent_text = ", ".join(dependents[:d_cap]) if dependents else "(none)"
                call_text = (
                    ", ".join(f"{name} ({count})" for name, count in top_calls[:c_cap])
                    if top_calls
                    else "(none)"
                )

                block = (
                    "Graph neighbors:\n"
                    f"- Imports: {import_text}\n"
                    f"- Depended on by (1-hop): {dependent_text}\n"
                    f"- Calls (top symbols): {call_text}"
                )
                if _estimate_messages_tokens([{"role": "user", "content": block}]) <= max_tokens:
                    return block

    return (
        "Graph neighbors:\n"
        "- Imports: (trimmed)\n"
        "- Depended on by (1-hop): (trimmed)\n"
        "- Calls (top symbols): (trimmed)"
    )


def _group_file_summaries_by_module(
    file_summaries: list[FileSummary],
) -> dict[str, list[FileSummary]]:
    grouped: dict[str, list[FileSummary]] = defaultdict(list)
    for summary in file_summaries:
        grouped[summary.path.split("/", 1)[0]].append(summary)
    return grouped


def _estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    total = 0
    for message in messages:
        role = str(message.get("role", "")).strip()
        content = str(message.get("content", ""))
        total += Budget.estimate(f"{role}\n{content}")
    return total


async def _reduce_modules_llm(
    *,
    cfg: CodeWikiConfig,
    file_summaries: list[FileSummary],
    fallback_module_summaries: list[ModuleSummary],
    signals: list[Signal],
    budget: Budget,
    prompt_addendum: str,
) -> list[ModuleSummary]:
    if not fallback_module_summaries:
        return []

    grouped = _group_file_summaries_by_module(file_summaries)
    capability_by_module = _capabilities_by_module(signals)
    reduce_limit = min(cfg.run.concurrency, cfg.generation.map_reduce_concurrency)
    sem = asyncio.Semaphore(max(1, reduce_limit))

    async with LLMClient(cfg.llm) as client:
        tasks = [
            asyncio.create_task(
                _summarize_one_module_reduce(
                    client=client,
                    sem=sem,
                    budget=budget,
                    fallback_module=module,
                    file_summaries=grouped.get(module.name, []),
                    capabilities=capability_by_module.get(module.name, module.capabilities),
                    prompt_addendum=prompt_addendum,
                )
            )
            for module in fallback_module_summaries
        ]
        return await asyncio.gather(*tasks)


async def _summarize_one_module_reduce(
    *,
    client: LLMClient,
    sem: asyncio.Semaphore,
    budget: Budget,
    fallback_module: ModuleSummary,
    file_summaries: list[FileSummary],
    capabilities: list[str],
    prompt_addendum: str,
) -> ModuleSummary:
    messages = _module_summary_prompt(
        module_name=fallback_module.name,
        file_summaries=file_summaries,
        capabilities=capabilities,
        prompt_addendum=prompt_addendum,
    )

    try:
        async with sem:
            result = await with_retry(client.chat, messages, retries=2)
        budget.record_from_response(result.usage)
        payload = _extract_json_object(result.text)
        if payload is None:
            payload = await _repair_json_payload(
                client,
                messages,
                result.text,
                sem,
                budget,
                required_keys=(
                    "responsibility, what_it_does, capabilities, interfaces, dependencies, "
                    "owned_data, architecture_notes, risks, citations, confidence"
                ),
            )
        if payload is None:
            return fallback_module

        return _coerce_module_summary(payload, fallback_module)
    except Exception:
        return fallback_module


async def _reduce_system_llm(
    *,
    cfg: CodeWikiConfig,
    module_summaries: list[ModuleSummary],
    fallback_system_summary: SystemSummary,
    repo_map: RepoMap,
    files: list[FileRecord],
    symbols: list[Symbol],
    budget: Budget,
    prompt_addendum: str,
) -> SystemSummary:
    if not module_summaries:
        return fallback_system_summary

    messages = _system_summary_prompt(
        module_summaries=module_summaries,
        repo_map=repo_map,
        files=files,
        symbols=symbols,
        prompt_addendum=prompt_addendum,
    )

    sem = asyncio.Semaphore(1)
    try:
        async with LLMClient(cfg.llm) as client:
            async with sem:
                result = await with_retry(client.chat, messages, retries=2)
            budget.record_from_response(result.usage)
            payload = _extract_json_object(result.text)
            if payload is None:
                payload = await _repair_json_payload(
                    client,
                    messages,
                    result.text,
                    sem,
                    budget,
                    required_keys=(
                        "executive_summary, what_the_service_does, primary_users, "
                        "business_outcomes, key_workflows, external_systems, "
                        "architecture_overview, capabilities, audiences, confidence"
                    ),
                )
        if payload is None:
            return fallback_system_summary
        return _coerce_system_summary(payload, fallback_system_summary)
    except Exception:
        return fallback_system_summary


def _module_summary_prompt(
    *,
    module_name: str,
    file_summaries: list[FileSummary],
    capabilities: list[str],
    prompt_addendum: str,
) -> list[dict[str, str]]:
    file_lines: list[str] = []
    for item in file_summaries[:14]:
        citations = ", ".join(item.citations[:3]) if item.citations else "(none)"
        what = item.what_it_does or item.responsibility
        data = ", ".join(item.data_touched[:4]) if item.data_touched else "(none)"
        file_lines.append(
            f"- {item.path} | does={what} | business={item.business_relevance} "
            f"| data={data} | cites={citations}"
        )
    if not file_lines:
        file_lines = ["- No file summaries available."]

    cap_text = ", ".join(capabilities[:10]) if capabilities else "(none)"
    user_prompt = (
        "Create a grounded module/component summary for CodeWiki.\n"
        "Return JSON only with keys: responsibility, what_it_does, capabilities, interfaces, "
        "dependencies, owned_data, architecture_notes, risks, citations, confidence.\n"
        "Field rules:\n"
        "- responsibility: the component's technical responsibility in one sentence\n"
        "- what_it_does: 1-2 plain-language sentences for a non-engineer\n"
        "- capabilities: array of business/technical capability labels this module provides\n"
        "- interfaces: array of entrypoints/APIs/public surface the module exposes\n"
        "- dependencies: array of internal/external systems the module relies on\n"
        "- owned_data: array of data entities/models/stores this module owns or manages\n"
        "- architecture_notes: short note on structure or patterns used, or 'unknown'\n"
        "- risks: array of coupling/complexity/reliability/security risks, or empty\n"
        "- citations: array of path:Lx-Ly values\n"
        "- confidence: high, medium, or low\n"
        "Grounding rules:\n"
        "- Base every claim on the file summaries below; never invent file paths\n"
        "- If a field cannot be grounded, use an empty array or 'unknown'\n\n"
        f"Module: {module_name}\n"
        f"Capability seeds: {cap_text}\n"
        "File summaries:\n"
        f"{chr(10).join(file_lines)}"
    )

    system_prompt = (
        "You are CodeWiki's architecture analyst. You synthesize file-level findings into a "
        "grounded component picture with both a business view and a technical view (interfaces, "
        "dependencies, data ownership, risks). Cite code as path:Lx-Ly, never fabricate paths, "
        "and state 'unknown' when evidence is missing. Respond with valid JSON only."
    )
    if prompt_addendum.strip():
        system_prompt = f"{system_prompt}\nLens guidance: {prompt_addendum.strip()}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _system_summary_prompt(
    *,
    module_summaries: list[ModuleSummary],
    repo_map: RepoMap,
    files: list[FileRecord],
    symbols: list[Symbol],
    prompt_addendum: str,
) -> list[dict[str, str]]:
    module_lines: list[str] = []
    for module in module_summaries[:16]:
        cites = ", ".join(module.citations[:2]) if module.citations else "(none)"
        caps = ", ".join(module.capabilities[:5]) if module.capabilities else "(none)"
        what = module.what_it_does or module.responsibility
        arch = module.architecture_notes or "(none)"
        module_lines.append(
            f"- {module.name} | does={what} | capabilities={caps} | arch={arch} | cites={cites}"
        )
    if not module_lines:
        module_lines = ["- No module summaries available."]

    user_prompt = (
        "Produce a grounded system-level summary for CodeWiki. Lead with business purpose, "
        "then technical architecture.\n"
        "Return JSON only with keys: executive_summary, what_the_service_does, primary_users, "
        "business_outcomes, key_workflows, external_systems, architecture_overview, capabilities, "
        "audiences, confidence.\n"
        "Field rules:\n"
        "- executive_summary: 2-4 sentences leadership can read; what the system is and why it exists\n"
        "- what_the_service_does: plain-language description of the service's actual function\n"
        "- primary_users: array of who uses or depends on this system\n"
        "- business_outcomes: array of business outcomes/value the system delivers\n"
        "- key_workflows: array of the main end-to-end workflows the system supports\n"
        "- external_systems: array of external services/integrations the system depends on\n"
        "- architecture_overview: short technical description of how the system is structured\n"
        "- capabilities: array of top capabilities\n"
        "- audiences: object with keys business and technical, each a short paragraph\n"
        "- confidence: high, medium, or low\n"
        "Grounding rules:\n"
        "- Base claims on the module summaries below; never invent components\n"
        "- If a field cannot be grounded, use an empty array or 'unknown'\n\n"
        f"Repository files: {len(files)}\n"
        f"Repository symbols: {len(symbols)}\n"
        f"Frameworks: {', '.join(repo_map.frameworks[:6]) if repo_map.frameworks else '(none)'}\n"
        f"Entrypoints: {', '.join(repo_map.entrypoints[:8]) if repo_map.entrypoints else '(none)'}\n"
        "Module summaries:\n"
        f"{chr(10).join(module_lines)}"
    )

    system_prompt = (
        "You are CodeWiki's principal analyst. You explain what a whole system does for the "
        "business and how it is built, grounded only in the provided module evidence. Lead with "
        "business purpose, then technical architecture. Never fabricate; state 'unknown' when "
        "evidence is missing. Respond with valid JSON only."
    )
    if prompt_addendum.strip():
        system_prompt = f"{system_prompt}\nLens guidance: {prompt_addendum.strip()}"

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _coerce_module_summary(payload: dict, fallback: ModuleSummary) -> ModuleSummary:
    responsibility = str(payload.get("responsibility", "")).strip() or fallback.responsibility
    capabilities = _coerce_string_list(payload.get("capabilities")) or fallback.capabilities
    fallback_path = fallback.files[0] if fallback.files else fallback.name.replace(".", "/")
    citations = _coerce_citations(payload.get("citations"), fallback_path) or fallback.citations

    raw_confidence = payload.get("confidence")
    confidence = (
        _coerce_confidence(raw_confidence)
        if raw_confidence is not None
        else fallback.confidence
    )

    return ModuleSummary(
        name=fallback.name,
        responsibility=responsibility,
        capabilities=capabilities,
        files=fallback.files,
        citations=citations,
        confidence=confidence,
        what_it_does=_coerce_text(payload.get("what_it_does"), fallback.what_it_does),
        interfaces=_coerce_string_list(payload.get("interfaces")) or fallback.interfaces,
        dependencies=_coerce_string_list(payload.get("dependencies")) or fallback.dependencies,
        owned_data=_coerce_string_list(payload.get("owned_data")) or fallback.owned_data,
        architecture_notes=_coerce_text(
            payload.get("architecture_notes"), fallback.architecture_notes
        ),
        risks=_coerce_string_list(payload.get("risks")) or fallback.risks,
    )


def _coerce_system_summary(payload: dict, fallback: SystemSummary) -> SystemSummary:
    executive_summary = (
        str(payload.get("executive_summary", "")).strip() or fallback.executive_summary
    )
    capabilities = _coerce_string_list(payload.get("capabilities")) or fallback.capabilities

    audiences = dict(fallback.audiences)
    raw_audiences = payload.get("audiences")
    if isinstance(raw_audiences, dict):
        business = str(raw_audiences.get("business", "")).strip()
        technical = str(raw_audiences.get("technical", "")).strip()
        if business:
            audiences["business"] = business
        if technical:
            audiences["technical"] = technical

    raw_confidence = payload.get("confidence")
    confidence = (
        _coerce_confidence(raw_confidence)
        if raw_confidence is not None
        else fallback.confidence
    )

    return SystemSummary(
        executive_summary=executive_summary,
        capabilities=capabilities,
        audiences=audiences,
        confidence=confidence,
        what_the_service_does=_coerce_text(
            payload.get("what_the_service_does"), fallback.what_the_service_does
        ),
        primary_users=_coerce_string_list(payload.get("primary_users")) or fallback.primary_users,
        business_outcomes=(
            _coerce_string_list(payload.get("business_outcomes")) or fallback.business_outcomes
        ),
        key_workflows=_coerce_string_list(payload.get("key_workflows")) or fallback.key_workflows,
        external_systems=(
            _coerce_string_list(payload.get("external_systems")) or fallback.external_systems
        ),
        architecture_overview=_coerce_text(
            payload.get("architecture_overview"), fallback.architecture_overview
        ),
    )


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
        interfaces = _unique_preserve_order(
            iface for item in items for iface in item.interfaces
        )[:24]
        dependencies = _unique_preserve_order(
            dep for item in items for dep in item.dependencies
        )[:24]
        owned_data = _unique_preserve_order(
            entity for item in items for entity in item.data_touched
        )[:24]

        out.append(
            ModuleSummary(
                name=module_name,
                responsibility=responsibility,
                capabilities=capabilities,
                files=sorted(item.path for item in items),
                citations=citations,
                confidence=confidence,
                interfaces=interfaces,
                dependencies=dependencies,
                owned_data=owned_data,
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
        architecture_overview=technical_view,
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