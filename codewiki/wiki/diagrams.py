"""Mermaid diagram builders from repo map and detected signals."""

from __future__ import annotations

from codewiki.models import RepoMap, Signal


def system_context_diagram(repo_map: RepoMap, signals: list[Signal]) -> str:
    services = sorted({s.name for s in signals if s.type in {"integration", "api_route"}})[:8]

    lines = [
        "flowchart LR",
        "    User((Business User)) --> System[Codebase]",
    ]
    for i, name in enumerate(services, start=1):
        node = f"N{i}"
        lines.append(f"    System --> {node}[{name}]")
    if repo_map.frameworks:
        lines.append(f"    System --> Stack[{', '.join(repo_map.frameworks[:4])}]")
    return "\n".join(lines)


def component_graph_diagram(repo_map: RepoMap) -> str:
    lines = ["flowchart TD"]
    added: set[tuple[str, str]] = set()

    for path, deps in repo_map.import_graph.items():
        a = path.replace("/", "_").replace(".", "_")
        lines.append(f"    {a}[{path}]")
        for dep in deps[:8]:
            b = dep.replace("/", "_").replace(".", "_").replace("-", "_")
            edge = (a, b)
            if edge in added:
                continue
            added.add(edge)
            lines.append(f"    {a} --> {b}[{dep}]")

    if len(lines) == 1:
        lines.append("    A[No import graph available]")
    return "\n".join(lines)
