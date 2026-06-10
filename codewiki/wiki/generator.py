"""Generate the markdown wiki from ingested repo structures and summaries."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
import os
from pathlib import Path
import re

import yaml

from codewiki.config import CodeWikiConfig
from codewiki.graph.code_graph import CodeGraph
from codewiki.lenses.base import BaseLens
from codewiki.llm.budget import Budget, BudgetExceeded
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import safe_slug
from codewiki.wiki.diagrams import component_graph_diagram, data_model_er_diagram, system_context_diagram
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


def _related_links(from_page: str, targets: Iterable[tuple[str, str]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    from_dir = Path(from_page).parent.as_posix()
    for target, label in targets:
        to_page = str(target).strip()
        if not to_page or to_page == from_page:
            continue
        rel = os.path.relpath(to_page, start=from_dir if from_dir else ".").replace("\\", "/")
        item = f"[{label}]({rel})"
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _component_from_path(path: str) -> str:
    return path.split("/", 1)[0] if path else ""


def _components_from_citations(citations: Iterable[str]) -> list[str]:
    components: list[str] = []
    for path in _citation_paths(citations):
        component = _component_from_path(path)
        if component and component not in components:
            components.append(component)
    return components


def _capability_file_summaries(capability: str, bundle: SummaryBundle | None) -> list[FileSummary]:
    if bundle is None:
        return []

    cap_lower = capability.lower()
    matched: list[FileSummary] = []
    for file_summary in bundle.file_summaries:
        haystack = " ".join(
            [
                file_summary.business_relevance,
                file_summary.what_it_does,
                file_summary.responsibility,
            ]
        ).lower()
        if cap_lower in haystack:
            matched.append(file_summary)
    return matched


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


def _mermaid_node_id(text: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]", "_", text)
    return f"N_{clean}" if clean else "N_node"


def _component_public_interface(
    code_graph: CodeGraph | None,
    component_files: list[str],
    *,
    max_items: int = 20,
) -> list[str]:
    if code_graph is None or not component_files:
        return []

    component_file_ids = {f"file:{path}" for path in component_files}
    out: list[str] = []
    for from_id, to_id, edge_type in code_graph.backend.all_edges():
        if edge_type != "defines" or from_id not in component_file_ids:
            continue

        symbol_node = code_graph.backend.get_node(to_id)
        if symbol_node is None or symbol_node.kind != "symbol":
            continue

        symbol_kind = str(symbol_node.meta.get("symbol_kind", "")).strip().lower()
        if symbol_kind not in {"function", "class"}:
            continue

        symbol_name = str(symbol_node.meta.get("name", "")).strip() or symbol_node.label
        item = f"{symbol_name} ({symbol_kind})"
        if item not in out:
            out.append(item)
        if len(out) >= max_items:
            break
    return out


def _component_neighborhood_diagram(
    *,
    component: str,
    component_files: list[str],
    file_dependencies: dict[str, list[str]],
    file_dependents: dict[str, list[str]],
    max_nodes: int = 12,
) -> str:
    if not component_files:
        return "flowchart LR\n    A[No component files available]"

    selected: list[str] = []
    component_seed_limit = max(1, min(6, max_nodes))
    for path in component_files[:component_seed_limit]:
        if path not in selected:
            selected.append(path)

    for path in component_files:
        for dep in file_dependencies.get(path, []):
            if dep in selected or len(selected) >= max_nodes:
                continue
            selected.append(dep)
        for dependent in file_dependents.get(path, []):
            if dependent in selected or len(selected) >= max_nodes:
                continue
            selected.append(dependent)
        if len(selected) >= max_nodes:
            break

    selected_set = set(selected)
    component_set = set(component_files)
    lines = [
        "flowchart LR",
        f"    subgraph {_mermaid_node_id(component)}[{component}]",
    ]

    for path in selected:
        if path in component_set:
            lines.append(f"        {_mermaid_node_id(path)}[{path}]")
    lines.append("    end")

    for path in selected:
        if path not in component_set:
            lines.append(f"    {_mermaid_node_id(path)}[{path}]")

    edges: set[tuple[str, str]] = set()
    for source, deps in file_dependencies.items():
        if source not in selected_set:
            continue
        for dep in deps:
            if dep not in selected_set:
                continue
            edge = (source, dep)
            if edge in edges:
                continue
            edges.add(edge)
            lines.append(f"    {_mermaid_node_id(source)} --> {_mermaid_node_id(dep)}")

    for target, dependents in file_dependents.items():
        if target not in selected_set:
            continue
        for dependent in dependents:
            if dependent not in selected_set:
                continue
            edge = (dependent, target)
            if edge in edges:
                continue
            edges.add(edge)
            lines.append(f"    {_mermaid_node_id(dependent)} --> {_mermaid_node_id(target)}")

    all_neighbors: set[str] = set(component_files)
    for path in component_files:
        all_neighbors.update(file_dependencies.get(path, []))
        all_neighbors.update(file_dependents.get(path, []))

    omitted = max(0, len(all_neighbors) - len(selected_set))
    if omitted > 0:
        lines.append(f"    more[+{omitted} more neighbors]")

    return "\n".join(lines)


def _workflow_sequence_diagram(
    capability: str,
    triggers: list[str],
    components: list[str],
    integrations: list[str],
) -> str:
    lines = [
        "sequenceDiagram",
        "    autonumber",
        "    actor User",
        "    participant API as API Layer",
    ]

    component_participants: list[tuple[str, str]] = []
    for idx, component in enumerate(components[:4], start=1):
        participant = f"C{idx}"
        component_participants.append((participant, component))
        lines.append(f"    participant {participant} as {component}")

    integration_participants: list[tuple[str, str]] = []
    for idx, integration in enumerate(integrations[:3], start=1):
        participant = f"E{idx}"
        integration_participants.append((participant, integration))
        lines.append(f"    participant {participant} as {integration}")

    trigger_text = triggers[0] if triggers else f"Execute {capability} workflow"
    lines.append(f"    User->>API: {trigger_text}")

    if not component_participants:
        lines.append(f"    API->>API: Handle {capability} logic")
    for participant, component in component_participants:
        lines.append(f"    API->>{participant}: Dispatch to {component}")
        lines.append(f"    {participant}-->>API: Step result")

    for participant, integration in integration_participants:
        lines.append(f"    API->>{participant}: Call {integration}")
        lines.append(f"    {participant}-->>API: External response")

    lines.append("    API-->>User: Return workflow outcome")
    return "\n".join(lines)


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
            code_graph=code_graph,
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
    capability_groups = _capability_groups(signals)
    top_components = _top_components(files)
    component_to_capabilities: dict[str, list[str]] = defaultdict(list)
    for capability, cap_signals in capability_groups.items():
        for signal in cap_signals:
            for component in _components_from_citations(signal.evidence):
                if capability not in component_to_capabilities[component]:
                    component_to_capabilities[component].append(capability)

    lens_templates = lens.page_templates() if lens is not None else []
    lens_scores = lens.scoring(signals, code_graph) if lens is not None else {}

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
        related=_related_links(
            "00-overview/executive-summary.md",
            [
                ("00-overview/system-context.md", "System Context"),
                ("00-overview/tech-stack.md", "Tech Stack"),
                ("00-overview/component-graph.md", "Component Graph"),
                ("domain/glossary.md", "Domain Glossary"),
                ("domain/data-model.md", "Data Model"),
            ],
        ),
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
        related=_related_links(
            "00-overview/system-context.md",
            [
                ("00-overview/executive-summary.md", "Executive Summary"),
                ("00-overview/component-graph.md", "Component Graph"),
                ("integrations/external-systems.md", "External Integrations"),
            ],
        ),
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
        related=_related_links(
            "00-overview/tech-stack.md",
            [
                ("00-overview/executive-summary.md", "Executive Summary"),
                ("00-overview/component-graph.md", "Component Graph"),
                ("operations/setup.md", "Setup"),
            ],
        ),
        tags=["overview", "stack"],
    )

    _emit_page(
        "00-overview/component-graph.md",
        title="Component Graph",
        page_type="overview",
        audience="technical",
        summary="Import/dependency relationships among discovered modules.",
        sections=[("Diagram", f"```mermaid\n{component_graph_diagram(code_graph, repo_map)}\n```")],
        related=_related_links(
            "00-overview/component-graph.md",
            [
                ("00-overview/system-context.md", "System Context"),
                ("00-overview/tech-stack.md", "Tech Stack"),
                *[
                    (
                        f"components/{safe_slug(component)}.md",
                        f"Component: {component}",
                    )
                    for component, _ in top_components[:5]
                ],
            ],
        ),
        tags=["diagram", "architecture"],
    )

    for component, _ in top_components:
        comp_files = [f.path for f in files if f.path.startswith(component + "/") or f.path == component]
        comp_file_set = set(comp_files)
        comp_symbols = [s for s in symbols if s.path in comp_file_set]
        comp_sources: list[str] = []

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
            comp_sources = module_summary.citations[:20]
        else:
            summary_file = next(
                (file_summaries[path] for path in comp_files if path in file_summaries),
                None,
            )
            if summary_file is not None:
                summary_text = summary_file.responsibility or summary_text
                comp_sources = summary_file.citations[:20]

        if not comp_sources:
            comp_sources = _dedupe_keep_order(
                cite
                for path in comp_files
                for cite in (file_summaries[path].citations if path in file_summaries else [])
            )[:20]

        file_dependencies: dict[str, list[str]] = {}
        file_dependents: dict[str, list[str]] = {}
        if code_graph is not None:
            for path in comp_files:
                file_dependencies[path] = code_graph.get_file_dependencies(path)
                file_dependents[path] = code_graph.get_file_dependents(path)

        depends_on = _dedupe_keep_order(
            dep
            for path in comp_files
            for dep in file_dependencies.get(path, [])
            if dep not in comp_file_set
        )[:20]
        depended_by = _dedupe_keep_order(
            dependent
            for path in comp_files
            for dependent in file_dependents.get(path, [])
            if dependent not in comp_file_set
        )[:20]

        public_interface = _component_public_interface(code_graph, comp_files)
        neighborhood = _component_neighborhood_diagram(
            component=component,
            component_files=comp_files,
            file_dependencies=file_dependencies,
            file_dependents=file_dependents,
            max_nodes=12,
        )

        sections = [
            ("Responsibility", "\n".join(body)),
            (
                "Dependencies",
                "\n".join(f"- {path}" for path in depends_on)
                if depends_on
                else "No direct internal dependencies detected.",
            ),
            (
                "Dependents",
                "\n".join(f"- {path}" for path in depended_by)
                if depended_by
                else "No direct internal dependents detected.",
            ),
            (
                "Public Interface",
                "\n".join(f"- {item}" for item in public_interface)
                if public_interface
                else "No function/class interface symbols detected.",
            ),
            ("Neighborhood", f"```mermaid\n{neighborhood}\n```"),
        ]

        component_path = f"components/{safe_slug(component)}.md"
        component_related = _related_links(
            component_path,
            [
                ("00-overview/component-graph.md", "Component Graph"),
                *[
                    (
                        f"capabilities/{safe_slug(capability)}.md",
                        f"Capability: {capability.replace('-', ' ').title()}",
                    )
                    for capability in component_to_capabilities.get(component, [])[:6]
                ],
                ("operations/risk-register.md", "Risk Register"),
            ],
        )

        _emit_page(
            component_path,
            title=f"Component: {component}",
            page_type="component",
            audience="technical",
            summary=summary_text,
            sections=sections,
            sources=comp_sources,
            related=component_related,
            tags=["component", component],
            confidence=module_summary.confidence if module_summary is not None else None,
        )

    for capability, cap_signals in capability_groups.items():
        sources: list[str] = []
        for signal in cap_signals:
            sources.extend(signal.evidence)
        sources.extend(_capability_sources(capability, summary_input))
        sources = _dedupe_keep_order(sources)[:24]

        cap_file_summaries = _capability_file_summaries(capability, summary_input)
        if not cap_file_summaries and summary_input is not None:
            source_paths = set(_citation_paths(sources))
            cap_file_summaries = [
                item for item in summary_input.file_summaries if item.path in source_paths
            ]

        workflow_lines = [f"- {signal.name}" for signal in cap_signals if signal.type == "api_route"]
        if not workflow_lines:
            workflow_lines = ["- Capability inferred from repository structure and integrations."]

        key_behaviors = _dedupe_keep_order(
            behavior
            for summary in cap_file_summaries
            for behavior in summary.key_behaviors
        )[:12]
        behavior_lines = [f"- {item}" for item in key_behaviors] or [
            "- No explicit behavioral rules inferred from grounded file summaries.",
        ]

        data_entities = _dedupe_keep_order(
            entity
            for summary in cap_file_summaries
            for entity in summary.data_touched
        )[:12]
        data_lines = [f"- {item}" for item in data_entities] or [
            "- No specific data entities were confidently identified for this capability.",
        ]

        interfaces = _dedupe_keep_order(
            interface
            for summary in cap_file_summaries
            for interface in summary.interfaces
        )[:12]
        interface_lines = [f"- {item}" for item in interfaces] or [
            "- No explicit interface surface inferred from file summaries.",
        ]

        implementing_components = _dedupe_keep_order(
            _components_from_citations(sources)
            + [
                _component_from_path(summary.path)
                for summary in cap_file_summaries
                if _component_from_path(summary.path)
            ]
        )[:10]
        component_lines = [f"- {item}" for item in implementing_components] or [
            "- No concrete component mapping inferred.",
        ]

        trigger_lines = [
            f"- {signal.name}" for signal in cap_signals if signal.type == "api_route"
        ] or workflow_lines

        integration_matches = [
            signal
            for signal in signals
            if signal.type == "integration"
            and (
                not implementing_components
                or bool(
                    set(_components_from_citations(signal.evidence))
                    & set(implementing_components)
                )
            )
        ]
        integration_names = _dedupe_keep_order(
            signal.name for signal in integration_matches
        )[:6]

        process_lines = [
            f"- API trigger enters the {capability.replace('-', ' ')} capability flow.",
        ]
        process_lines.extend(
            f"- {component} executes a grounded step in the capability flow."
            for component in implementing_components
        )
        if not implementing_components:
            process_lines.append(
                "- Core platform modules handle this flow; explicit component ownership is limited."
            )
        if integration_names:
            process_lines.append("- External integrations are invoked for downstream processing.")

        integration_lines = [f"- {name}" for name in integration_names] or [
            "- No direct external touchpoints detected for this capability.",
        ]

        workflow_sources = _dedupe_keep_order(
            sources
            + [cite for signal in integration_matches for cite in signal.evidence]
        )[:30]
        capability_path = f"capabilities/{safe_slug(capability)}.md"
        workflow_path = f"workflows/{safe_slug(capability)}-flow.md"
        workflow_diagram = _workflow_sequence_diagram(
            capability=capability,
            triggers=[line.removeprefix("- ") for line in trigger_lines],
            components=implementing_components,
            integrations=integration_names,
        )
        capability_related = _related_links(
            capability_path,
            [
                (workflow_path, f"Workflow: {capability.replace('-', ' ').title()}"),
                *[
                    (
                        f"components/{safe_slug(component)}.md",
                        f"Component: {component}",
                    )
                    for component in implementing_components[:6]
                ],
                ("domain/data-model.md", "Data Model"),
                ("integrations/external-systems.md", "External Integrations"),
            ],
        )
        workflow_related = _related_links(
            workflow_path,
            [
                (capability_path, f"Capability: {capability.replace('-', ' ').title()}"),
                *[
                    (
                        f"components/{safe_slug(component)}.md",
                        f"Component: {component}",
                    )
                    for component in implementing_components[:6]
                ],
                ("integrations/external-systems.md", "External Integrations"),
            ],
        )

        _emit_page(
            capability_path,
            title=f"Capability: {capability.replace('-', ' ').title()}",
            page_type="capability",
            audience="business",
            summary=_capability_summary(capability, summary_input),
            sections=[
                ("Business Workflow", "\n".join(workflow_lines)),
                ("Trigger Surface", "\n".join(trigger_lines)),
                ("Operational Rules", "\n".join(behavior_lines)),
                ("Data Touched", "\n".join(data_lines)),
                ("Interfaces", "\n".join(interface_lines)),
                ("Implementing Components", "\n".join(component_lines)),
            ],
            sources=sources,
            related=capability_related,
            tags=["capability", capability],
            confidence=system_confidence,
        )

        _emit_page(
            workflow_path,
            title=f"Workflow: {capability.replace('-', ' ').title()}",
            page_type="workflow",
            audience="both",
            summary=(
                f"End-to-end flow for {capability.replace('-', ' ')} inferred from grounded "
                "routes, components, and integrations."
            ),
            sections=[
                ("Trigger", "\n".join(trigger_lines)),
                ("Process Steps", "\n".join(process_lines)),
                ("External Touchpoints", "\n".join(integration_lines)),
                ("Diagram", f"```mermaid\n{workflow_diagram}\n```"),
            ],
            sources=workflow_sources,
            related=workflow_related,
            tags=["workflow", capability],
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
        related=_related_links(
            "domain/glossary.md",
            [
                ("domain/data-model.md", "Data Model"),
                ("integrations/external-systems.md", "External Integrations"),
                *[
                    (
                        f"capabilities/{safe_slug(capability)}.md",
                        f"Capability: {capability.replace('-', ' ').title()}",
                    )
                    for capability in list(capability_groups.keys())[:6]
                ],
            ],
        ),
        tags=["domain", "glossary"],
    )

    data_entities = _dedupe_keep_order(
        [signal.name for signal in signals if signal.type == "data_model"]
        + [
            entity
            for file_summary in summary_input.file_summaries
            for entity in file_summary.data_touched
        ]
        if summary_input is not None
        else [signal.name for signal in signals if signal.type == "data_model"]
    )[:40]
    data_sources = _dedupe_keep_order(
        [cite for signal in signals if signal.type == "data_model" for cite in signal.evidence]
        + [
            cite
            for file_summary in summary_input.file_summaries
            if file_summary.data_touched
            for cite in file_summary.citations
        ]
        if summary_input is not None
        else [cite for signal in signals if signal.type == "data_model" for cite in signal.evidence]
    )[:40]
    data_lines = [f"- {entity}" for entity in data_entities] or [
        "- No explicit domain entities detected from current signals.",
    ]
    data_model_diagram = data_model_er_diagram(data_entities)

    _emit_page(
        "domain/data-model.md",
        title="Data Model",
        page_type="concept",
        audience="both",
        summary="Domain entities and relationship hints inferred from model and data-touch signals.",
        sections=[
            ("Entities", "\n".join(data_lines)),
            ("Relationship View", f"```mermaid\n{data_model_diagram}\n```"),
        ],
        sources=data_sources,
        related=_related_links(
            "domain/data-model.md",
            [
                ("domain/glossary.md", "Domain Glossary"),
                ("integrations/external-systems.md", "External Integrations"),
                *[
                    (
                        f"capabilities/{safe_slug(capability)}.md",
                        f"Capability: {capability.replace('-', ' ').title()}",
                    )
                    for capability in list(capability_groups.keys())[:6]
                ],
            ],
        ),
        tags=["domain", "data-model", "diagram"],
        confidence=system_confidence,
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
        related=_related_links(
            "integrations/external-systems.md",
            [
                ("domain/data-model.md", "Data Model"),
                ("domain/glossary.md", "Domain Glossary"),
                ("operations/risk-register.md", "Risk Register"),
                *[
                    (
                        f"capabilities/{safe_slug(capability)}.md",
                        f"Capability: {capability.replace('-', ' ').title()}",
                    )
                    for capability in list(capability_groups.keys())[:6]
                ],
            ],
        ),
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
        related=_related_links(
            "operations/risk-register.md",
            [
                ("integrations/external-systems.md", "External Integrations"),
                ("00-overview/component-graph.md", "Component Graph"),
                ("domain/data-model.md", "Data Model"),
            ],
        ),
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
        related=_related_links(
            "operations/setup.md",
            [
                ("00-overview/tech-stack.md", "Tech Stack"),
                ("00-overview/executive-summary.md", "Executive Summary"),
                ("operations/risk-register.md", "Risk Register"),
            ],
        ),
        tags=["operations", "setup"],
    )

    if lens is not None:
        emitted_lens_pages: set[str] = set()
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
                emitted_lens_pages.add(page.rel_path)
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

            # Ensure declared template paths exist even when a lens emits only scores/metadata.
            for template in lens_templates:
                if template in emitted_lens_pages:
                    continue
                template_title = Path(template).stem.replace("-", " ").replace("_", " ").title()
                _emit_page(
                    template,
                    title=template_title,
                    page_type="analysis",
                    audience="both",
                    summary=(
                        f"Template placeholder declared by lens '{lens.name}'. "
                        "No specialized content was generated for this run."
                    ),
                    sections=[
                        (
                            "Status",
                            "Lens template was declared but did not emit dedicated content this run.",
                        )
                    ],
                    tags=["lens", lens.name, "template"],
                    confidence="low",
                )

            if lens_templates or lens_scores:
                score_lines = (
                    [f"- {key}: {value:.2f}" for key, value in sorted(lens_scores.items())]
                    if lens_scores
                    else ["- No scores produced by this lens."]
                )
                template_lines = (
                    [f"- {item}" for item in lens_templates]
                    if lens_templates
                    else ["- No templates declared by this lens."]
                )
                _emit_page(
                    f"analysis/{safe_slug(lens.name)}-lens.md",
                    title=f"Lens Analysis: {lens.name.replace('_', ' ').title()}",
                    page_type="analysis",
                    audience="both",
                    summary="Lens metadata for this run, including template footprint and computed scores.",
                    sections=[
                        ("Declared Templates", "\n".join(template_lines)),
                        ("Scores", "\n".join(score_lines)),
                    ],
                    tags=["analysis", "lens", lens.name],
                    confidence="medium" if lens_scores else "low",
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
            f"signals={len(signals)} pages={pages_written} lens={lens.name if lens else 'business'} "
            f"lens_templates={len(lens_templates)} lens_scores={len(lens_scores)}"
        ),
    )

    if written_pages is not None:
        written_pages.clear()
        written_pages.update(emitted_pages)

    return pages_written