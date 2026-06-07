"""Generate the markdown wiki from ingested repo structures and signals."""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import safe_slug
from codewiki.wiki.diagrams import component_graph_diagram, system_context_diagram
from codewiki.wiki.index_log import append_log, rebuild_index
from codewiki.wiki.pages import write_page


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


def generate_wiki(
    *,
    source_root: Path,
    cfg: CodeWikiConfig,
    files: list[FileRecord],
    symbols: list[Symbol],
    repo_map: RepoMap,
    signals: list[Signal],
) -> int:
    """Generate a complete baseline wiki from extracted repository knowledge."""
    wiki_root = cfg.wiki.output_dir
    wiki_root.mkdir(parents=True, exist_ok=True)

    pages_written = 0

    business_summary = (
        "This system appears to provide a set of business capabilities derived from its "
        "current code implementation. The wiki translates technical modules into business "
        "outcomes, operational touchpoints, and integration dependencies."
    )
    technical_summary = (
        f"Repository has {len(files)} files, {len(symbols)} extracted symbols, and "
        f"{len(signals)} detected operational/business signals."
    )

    write_page(
        wiki_root,
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
    )
    pages_written += 1

    write_page(
        wiki_root,
        "00-overview/system-context.md",
        title="System Context",
        page_type="overview",
        audience="both",
        summary="Context diagram of the codebase and connected systems inferred from code evidence.",
        sections=[("Diagram", f"```mermaid\n{system_context_diagram(repo_map, signals)}\n```")],
        tags=["diagram", "context"],
    )
    pages_written += 1

    lang_lines = [f"- {lang}: {count} files" for lang, count in sorted(repo_map.language_stats.items())]
    write_page(
        wiki_root,
        "00-overview/tech-stack.md",
        title="Tech Stack",
        page_type="overview",
        audience="technical",
        summary="Languages and frameworks detected from code and dependency hints.",
        sections=[
            ("Languages", "\n".join(lang_lines) if lang_lines else "No language stats found."),
            (
                "Frameworks",
                "\n".join(f"- {f}" for f in repo_map.frameworks)
                if repo_map.frameworks
                else "No frameworks detected.",
            ),
            (
                "Entrypoints",
                "\n".join(f"- {e}" for e in repo_map.entrypoints)
                if repo_map.entrypoints
                else "No common entrypoints detected.",
            ),
        ],
        tags=["overview", "stack"],
    )
    pages_written += 1

    write_page(
        wiki_root,
        "00-overview/component-graph.md",
        title="Component Graph",
        page_type="overview",
        audience="technical",
        summary="Import/dependency relationships among discovered modules.",
        sections=[("Diagram", f"```mermaid\n{component_graph_diagram(repo_map)}\n```")],
        tags=["diagram", "architecture"],
    )
    pages_written += 1

    for component, count in _top_components(files):
        comp_files = [f.path for f in files if f.path.startswith(component + "/") or f.path == component]
        comp_symbols = [s for s in symbols if s.path in comp_files]
        sources = [f"{p}:L1-L1" for p in comp_files[:12]]
        body = [
            f"- Files: {len(comp_files)}",
            f"- Symbols: {len(comp_symbols)}",
        ]
        write_page(
            wiki_root,
            f"components/{safe_slug(component)}.md",
            title=f"Component: {component}",
            page_type="component",
            audience="technical",
            summary=f"Technical ownership and responsibilities for {component}.",
            sections=[("Responsibility", "\n".join(body))],
            sources=sources,
            tags=["component", component],
        )
        pages_written += 1

    for cap, cap_signals in _capability_groups(signals).items():
        sources: list[str] = []
        for s in cap_signals:
            sources.extend(s.evidence)
        sources = sorted(set(sources))[:20]

        workflow_lines = [f"- {s.name}" for s in cap_signals if s.type == "api_route"]
        if not workflow_lines:
            workflow_lines = ["- Capability inferred from repository structure and integrations."]

        write_page(
            wiki_root,
            f"capabilities/{safe_slug(cap)}.md",
            title=f"Capability: {cap.replace('-', ' ').title()}",
            page_type="capability",
            audience="business",
            summary="Business-facing capability synthesized from APIs and domain signals.",
            sections=[("Business Workflow", "\n".join(workflow_lines))],
            sources=sources,
            tags=["capability", cap],
        )
        pages_written += 1

    glossary_lines = [
        f"- **{s.name}** ({s.type})"
        for s in signals
        if s.type in {"data_model", "integration", "config", "messaging"}
    ]
    write_page(
        wiki_root,
        "domain/glossary.md",
        title="Domain Glossary",
        page_type="concept",
        audience="business",
        summary="Domain and operational terms extracted from code signals.",
        sections=[("Terms", "\n".join(glossary_lines) if glossary_lines else "No terms detected.")],
        sources=[s.evidence[0] for s in signals if s.evidence][:30],
        tags=["domain", "glossary"],
    )
    pages_written += 1

    integration_lines = [f"- {s.name}" for s in signals if s.type == "integration"]
    write_page(
        wiki_root,
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
        sources=[s.evidence[0] for s in signals if s.type == "integration" and s.evidence],
        tags=["integration"],
    )
    pages_written += 1

    risk_lines = [
        "- Verify strict citation grounding during human review.",
        "- Review areas with sparse symbols or broad fallback chunks.",
        "- Track external integrations for compliance/security ownership.",
    ]
    write_page(
        wiki_root,
        "operations/risk-register.md",
        title="Risk Register",
        page_type="overview",
        audience="both",
        summary="Initial risk sweep inferred from structure and integrations.",
        sections=[("Risks", "\n".join(risk_lines))],
        tags=["operations", "risk"],
    )
    pages_written += 1

    setup_lines = [
        "- Install dependencies: pip install -e .[dev]",
        "- Validate LLM endpoint: codewiki ping",
        "- Build wiki: codewiki generate --source <path-or-url>",
    ]
    write_page(
        wiki_root,
        "operations/setup.md",
        title="Setup",
        page_type="overview",
        audience="technical",
        summary="How to run this repository and generate the wiki artifact.",
        sections=[("Commands", "\n".join(setup_lines))],
        tags=["operations", "setup"],
    )
    pages_written += 1

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

    rebuild_index(wiki_root)
    append_log(
        wiki_root,
        "ingest",
        f"source={source_root} files={len(files)} symbols={len(symbols)} signals={len(signals)} pages={pages_written}",
    )
    return pages_written
