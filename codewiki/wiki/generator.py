"""Generate the markdown wiki from ingested repo structures and summaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
import re

import yaml

from codewiki.config import CodeWikiConfig
from codewiki.graph.code_graph import CodeGraph
from codewiki.lenses.base import BaseLens
from codewiki.llm.budget import Budget, BudgetExceeded
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import safe_slug
from codewiki.wiki.diagrams import component_graph_diagram, system_context_diagram
from codewiki.wiki.index_log import append_log, rebuild_index
from codewiki.wiki.pagemap import PageRecord, load_pagemap, save_pagemap
from codewiki.wiki.pages import write_page
from codewiki.wiki.summarizer import FileSummary, ModuleSummary, SummaryBundle, summarize_repository


_CITE_RE = re.compile(r"^([A-Za-z0-9_./-]+):L(\d+)-L(\d+)$")


def _top_components(files: list[FileRecord], max_items: int = 12) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter()
    for file in files:
        top = file.path.split("/", 1)[0]
        counter[top] += 1
    return counter.most_common(max_items)


def _capability_groups(signals: list[Signal]) -> dict[str, list[Signal]]:
    out: dict[str, list[Signal]] = defaultdict(list)
    for signal in signals:
        if signal.type == "api_route":
            segment = signal.name.split(" ", 1)[-1].split("/")
            cap = segment[1] if len(segment) > 1 and segment[1] else "general"
            out[cap].append(signal)
    if not out:
        out["platform"] = [
            Signal(type="capability", name="Platform Core", evidence=[]),
        ]
    return out


def _dedupe_keep_order(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in out:
            out.append(item)
    return out


def _fallback_business_summary() -> str:
    return (
        "This system appears to provide a set of business capabilities derived from its "
        "current code implementation. The wiki translates technical modules into business "
        "outcomes, operational touchpoints, and integration dependencies."
    )


def _fallback_technical_summary(files: list[FileRecord], symbols: list[Symbol], signals: list[Signal]) -> str:
    return (
        f"Repository has {len(files)} files, {len(symbols)} extracted symbols, and "
        f"{len(signals)} detected operational/business signals."
    )


def _module_index(bundle: SummaryBundle | None) -> dict[str, ModuleSummary]:
    if bundle is None:
        return {}
    return {module.name: module for module in bundle.module_summaries}


def _file_summary_index(bundle: SummaryBundle | None) -> dict[str, FileSummary]:
    if bundle is None:
        return {}
    return {item.path: item for item in bundle.file_summaries}


def _capability_summary(capability: str, bundle: SummaryBundle | None) -> str:
    if bundle is None:
        return "Business-facing capability synthesized from APIs and domain signals."

    cap_lower = capability.lower()
    for file_summary in bundle.file_summaries:
        if cap_lower in file_summary.business_relevance.lower():
            return file_summary.business_relevance

    return bundle.system_summary.audiences.get(
        "business",
        "Business-facing capability synthesized from APIs and domain signals.",
    )


def _capability_sources(capability: str, bundle: SummaryBundle | None) -> list[str]:
    if bundle is None:
        return []

    cap_lower = capability.lower()
    citations: list[str] = []
    for file_summary in bundle.file_summaries:
        if cap_lower in file_summary.business_relevance.lower():
            citations.extend(file_summary.citations)
    return _dedupe_keep_order(citations)


def _citation_paths(citations: Iterable[str]) -> list[str]:
    paths: list[str] = []
    for cite in citations:
        text = str(cite).strip()
        if not text or ":L" not in text:
            continue
        path = text.split(":L", 1)[0]
        if path and path not in paths:
            paths.append(path)
    return paths


def _citation_ranges(citations: Iterable[str]) -> list[tuple[str, int, int]]:
    ranges: list[tuple[str, int, int]] = []
    for cite in citations:
        text = str(cite).strip()
        m = _CITE_RE.match(text)
        if not m:
            continue
        ranges.append((m.group(1), int(m.group(2)), int(m.group(3))))
    return ranges


def _source_symbols(
    citations: Iterable[str],
    symbols_by_path: dict[str, list[Symbol]],
) -> list[str]:
    out: list[str] = []
    for path, start, end in _citation_ranges(citations):
        for symbol in symbols_by_path.get(path, []):
            if symbol.start_line <= end and symbol.end_line >= start:
                if symbol.id not in out:
                    out.append(symbol.id)
    return out


def _page_frontmatter(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}

    if not text.startswith("---\n"):
        return {}
    try:
        _, raw_frontmatter, _ = text.split("---\n", 2)
    except ValueError:
        return {}

    parsed = yaml.safe_load(raw_frontmatter)
    return parsed if isinstance(parsed, dict) else {}


def _refresh_pagemap(
    wiki_root: Path,
    files: list[FileRecord],
    symbols: list[Symbol],
) -> None:
    hash_by_path = {item.path: item.hash for item in files}
    symbols_by_path: dict[str, list[Symbol]] = defaultdict(list)
    for symbol in symbols:
        symbols_by_path[symbol.path].append(symbol)

    existing = load_pagemap(wiki_root)
    records: dict[str, PageRecord] = {}

    pages = sorted(
        page for page in wiki_root.rglob("*.md")
        if page.name not in {"index.md", "log.md"} and not page.name.endswith(".proposed.md")
    )

    for page in pages:
        rel = page.relative_to(wiki_root).as_posix()
        frontmatter = _page_frontmatter(page)
        raw_sources = frontmatter.get("sources", []) if isinstance(frontmatter, dict) else []
        citations = [str(item).strip() for item in raw_sources if str(item).strip()]
        source_files = _citation_paths(citations)
        source_symbols = _source_symbols(citations, symbols_by_path)
        file_hashes = {
            source_file: hash_by_path[source_file]
            for source_file in source_files
            if source_file in hash_by_path
        }

        previous = existing.get(rel)
        try:
            page_text = page.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            page_text = ""

        records[rel] = PageRecord(
            page=rel,
            source_files=source_files,
            source_symbols=source_symbols or (previous.source_symbols if previous else []),
            file_hashes=file_hashes,
            human_edited=(previous.human_edited if previous else False)
            or "<!-- codewiki:locked -->" in page_text,
        )

    save_pagemap(wiki_root, records)


def generate_wiki(
    *,
    source_root: Path,
    cfg: CodeWikiConfig,
    files: list[FileRecord],
    symbols: list[Symbol],
    repo_map: RepoMap,
    signals: list[Signal],
    code_graph: CodeGraph | None = None,
    budget: Budget | None = None,
    lens: BaseLens | None = None,
    only_pages: set[str] | None = None,
    written_pages: set[str] | None = None,
) -> int:
    """Generate a complete wiki from extracted repository knowledge."""
    wiki_root = cfg.wiki.output_dir
    wiki_root.mkdir(parents=True, exist_ok=True)

    pages_written = 0
    emitted_pages: set[str] = set()
    summary_bundle: SummaryBundle | None = None

    def _emit_page(rel_path: str, **kwargs: object) -> None:
        nonlocal pages_written
        if only_pages is not None and rel_path not in only_pages:
            return
        write_page(wiki_root, rel_path, **kwargs)
        pages_written += 1
        emitted_pages.add(rel_path)

    try:
        summary_bundle = summarize_repository(
            cfg=cfg,
            files=files,
            symbols=symbols,
            repo_map=repo_map,
            signals=signals,
            budget=budget,
            prompt_addendum=(lens.system_prompt_addendum() if lens is not None else ""),
        )
    except BudgetExceeded:
        raise
    except Exception:
        # Preserve current offline behavior if summarization cannot be completed.
        summary_bundle = None

    summary_input = summary_bundle
    if (
        cfg.wiki.strict_grounding
        and summary_bundle is not None
        and summary_bundle.system_summary.confidence == "low"
    ):
        # In strict mode, avoid low-confidence synthesized claims.
        summary_input = None

    module_summaries = _module_index(summary_input)
    file_summaries = _file_summary_index(summary_input)

    business_summary = _fallback_business_summary()
    technical_summary = _fallback_technical_summary(files, symbols, signals)
    if summary_input is not None:
        business_summary = summary_input.system_summary.executive_summary or business_summary
        technical_summary = (
            summary_input.system_summary.audiences.get("technical") or technical_summary
        )

    system_confidence = summary_bundle.system_summary.confidence if summary_bundle else None
    _emit_page(
        "00-overview/executive-summary.md",
        title="Executive Summary",
        page_type="overview",
        audience="business",
        summary=business_summary,
        sections=[
            ("System Shape", technical_summary),
            (
                "Business-Relevant Highlights",
                "- Capability pages map to route and workflow evidence\n"
                "- Integrations list external dependencies and risk touchpoints\n"
                "- Operations pages summarize setup and architecture hotspots",
            ),
        ],
        tags=["overview", "business"],
        confidence=system_confidence,
    )

    _emit_page(
        "00-overview/system-context.md",
        title="System Context",
        page_type="overview",
        audience="both",
        summary="Context diagram of the codebase and connected systems inferred from code evidence.",
        sections=[("Diagram", f"```mermaid\n{system_context_diagram(repo_map, signals)}\n```")],
        tags=["diagram", "context"],
    )

    lang_lines = [f"- {lang}: {count} files" for lang, count in sorted(repo_map.language_stats.items())]
    _emit_page(
        "00-overview/tech-stack.md",
        title="Tech Stack",
        page_type="overview",
        audience="technical",
        summary="Languages and frameworks detected from code and dependency hints.",
        sections=[
            ("Languages", "\n".join(lang_lines) if lang_lines else "No language stats found."),
            (
                "Frameworks",
                "\n".join(f"- {framework}" for framework in repo_map.frameworks)
                if repo_map.frameworks
                else "No frameworks detected.",
            ),
            (
                "Entrypoints",
                "\n".join(f"- {entry}" for entry in repo_map.entrypoints)
                if repo_map.entrypoints
                else "No common entrypoints detected.",
            ),
        ],
        tags=["overview", "stack"],
    )

    _emit_page(
        "00-overview/component-graph.md",
        title="Component Graph",
        page_type="overview",
        audience="technical",
        summary="Import/dependency relationships among discovered modules.",
        sections=[("Diagram", f"```mermaid\n{component_graph_diagram(code_graph, repo_map)}\n```")],
        tags=["diagram", "architecture"],
    )

    for component, _ in _top_components(files):
        comp_files = [f.path for f in files if f.path.startswith(component + "/") or f.path == component]
        comp_symbols = [s for s in symbols if s.path in comp_files]
        comp_sources = [f"{path}:L1-L1" for path in comp_files[:12]]

        summary_text = f"Technical ownership and responsibilities for {component}."
        body = [
            f"- Files: {len(comp_files)}",
            f"- Symbols: {len(comp_symbols)}",
        ]

        module_summary = module_summaries.get(component)
        if module_summary is not None:
            summary_text = module_summary.responsibility or summary_text
            body.append(f"- Confidence: {module_summary.confidence}")
            if module_summary.capabilities:
                body.append(f"- Capabilities: {', '.join(module_summary.capabilities[:6])}")
            comp_sources = module_summary.citations[:20] or comp_sources
        else:
            summary_file = next(
                (file_summaries[path] for path in comp_files if path in file_summaries),
                None,
            )
            if summary_file is not None:
                summary_text = summary_file.responsibility or summary_text
                comp_sources = summary_file.citations[:20] or comp_sources

        _emit_page(
            f"components/{safe_slug(component)}.md",
            title=f"Component: {component}",
            page_type="component",
            audience="technical",
            summary=summary_text,
            sections=[("Responsibility", "\n".join(body))],
            sources=comp_sources,
            tags=["component", component],
            confidence=module_summary.confidence if module_summary is not None else None,
        )

    for capability, cap_signals in _capability_groups(signals).items():
        sources: list[str] = []
        for signal in cap_signals:
            sources.extend(signal.evidence)
        sources.extend(_capability_sources(capability, summary_input))
        sources = _dedupe_keep_order(sources)[:24]

        workflow_lines = [f"- {signal.name}" for signal in cap_signals if signal.type == "api_route"]
        if not workflow_lines:
            workflow_lines = ["- Capability inferred from repository structure and integrations."]

        _emit_page(
            f"capabilities/{safe_slug(capability)}.md",
            title=f"Capability: {capability.replace('-', ' ').title()}",
            page_type="capability",
            audience="business",
            summary=_capability_summary(capability, summary_input),
            sections=[("Business Workflow", "\n".join(workflow_lines))],
            sources=sources,
            tags=["capability", capability],
            confidence=system_confidence,
        )

    glossary_lines = [
        f"- **{signal.name}** ({signal.type})"
        for signal in signals
        if signal.type in {"data_model", "integration", "config", "messaging"}
    ]
    if summary_input is not None:
        glossary_terms = _dedupe_keep_order(
            symbol
            for file_summary in summary_input.file_summaries
            for symbol in file_summary.key_symbols
        )[:60]
        for term in glossary_terms:
            glossary_lines.append(f"- **{term}** (symbol)")

    glossary_sources = [signal.evidence[0] for signal in signals if signal.evidence][:30]
    if summary_input is not None:
        glossary_sources.extend(
            cite for file_summary in summary_input.file_summaries for cite in file_summary.citations
        )
        glossary_sources = _dedupe_keep_order(glossary_sources)[:40]

    _emit_page(
        "domain/glossary.md",
        title="Domain Glossary",
        page_type="concept",
        audience="business",
        summary="Domain and operational terms extracted from code signals.",
        sections=[("Terms", "\n".join(glossary_lines) if glossary_lines else "No terms detected.")],
        sources=glossary_sources,
        tags=["domain", "glossary"],
    )

    integration_lines = [f"- {signal.name}" for signal in signals if signal.type == "integration"]
    _emit_page(
        "integrations/external-systems.md",
        title="External Integrations",
        page_type="integration",
        audience="both",
        summary="Third-party and external platform touchpoints detected in source code.",
        sections=[
            (
                "Systems",
                "\n".join(sorted(set(integration_lines))) if integration_lines else "No integrations found.",
            )
        ],
        sources=[signal.evidence[0] for signal in signals if signal.type == "integration" and signal.evidence],
        tags=["integration"],
    )

    risk_lines = [
        "- Verify strict citation grounding during human review.",
        "- Review areas with sparse symbols or broad fallback chunks.",
        "- Track external integrations for compliance/security ownership.",
    ]
    _emit_page(
        "operations/risk-register.md",
        title="Risk Register",
        page_type="overview",
        audience="both",
        summary="Initial risk sweep inferred from structure and integrations.",
        sections=[("Risks", "\n".join(risk_lines))],
        tags=["operations", "risk"],
    )

    setup_lines = [
        "- Install dependencies: pip install -e .[dev]",
        "- Validate LLM endpoint: codewiki ping",
        "- Build wiki: codewiki generate --source <path-or-url>",
    ]
    _emit_page(
        "operations/setup.md",
        title="Setup",
        page_type="overview",
        audience="technical",
        summary="How to run this repository and generate the wiki artifact.",
        sections=[("Commands", "\n".join(setup_lines))],
        tags=["operations", "setup"],
    )

    if lens is not None:
        try:
            for page in lens.extra_pages(
                source_root=source_root,
                cfg=cfg,
                files=files,
                symbols=symbols,
                repo_map=repo_map,
                signals=signals,
                code_graph=code_graph,
                summary_bundle=summary_input,
            ):
                _emit_page(
                    page.rel_path,
                    title=page.title,
                    page_type=page.page_type,
                    audience=page.audience,
                    summary=page.summary,
                    sections=page.sections,
                    sources=page.sources,
                    related=page.related,
                    tags=page.tags,
                    confidence=page.confidence,
                )
        except Exception:
            # Lens extras should never block base wiki generation.
            pass

    if only_pages is None or "AGENTS.md" in only_pages:
        (wiki_root / "AGENTS.md").write_text(
            "# AGENTS\n\n"
            "This wiki is generated and maintained by CodeWiki.\n\n"
            "Rules:\n"
            "- Keep claims grounded in code citations.\n"
            "- Update pages incrementally when source changes.\n"
            "- Record run events in log.md and regenerate index.md each run.\n",
            encoding="utf-8",
        )
        pages_written += 1
        emitted_pages.add("AGENTS.md")

    rebuild_index(wiki_root)
    _refresh_pagemap(wiki_root, files, symbols)
    append_log(
        wiki_root,
        "ingest",
        (
            f"source={source_root} files={len(files)} symbols={len(symbols)} "
            f"signals={len(signals)} pages={pages_written} lens={lens.name if lens else 'business'}"
        ),
    )

    if written_pages is not None:
        written_pages.clear()
        written_pages.update(emitted_pages)

    return pages_written