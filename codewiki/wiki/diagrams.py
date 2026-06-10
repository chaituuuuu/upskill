"""Mermaid diagram builders from code graph and detected signals."""

from __future__ import annotations

import re

from codewiki.graph.code_graph import CodeGraph
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


def component_graph_diagram(code_graph: CodeGraph | None, repo_map: RepoMap | None = None) -> str:
    lines = ["flowchart TD"]
    added: set[tuple[str, str]] = set()
    
    if code_graph is None:
        if repo_map is None:
            lines.append("    A[No graph available]")
            return "\n".join(lines)
        
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
        return "\n".join(lines)
    
    internal_nodes = code_graph.get_internal_nodes()
    external_nodes = code_graph.get_external_nodes()
    
    max_nodes = 30
    displayed_internal = internal_nodes[:max_nodes]
    
    node_ids = {node.id for node in displayed_internal}
    
    for node in displayed_internal:
        if node.kind == "file":
            safe_id = node.id.replace(":", "_").replace("/", "_").replace(".", "_")
            lines.append(f"    {safe_id}[{node.label}]")
    
    for from_id, to_id, edge_type in code_graph.backend.all_edges():
        if from_id in node_ids and to_id in node_ids:
            safe_from = from_id.replace(":", "_").replace("/", "_").replace(".", "_")
            safe_to = to_id.replace(":", "_").replace("/", "_").replace(".", "_")
            edge = (safe_from, safe_to)
            if edge not in added and edge_type == "imports":
                added.add(edge)
                lines.append(f"    {safe_from} --> {safe_to}")
    
    if external_nodes:
        ext_count = min(5, len(external_nodes))
        for i, node in enumerate(external_nodes[:ext_count]):
            safe_id = node.id.replace(":", "_").replace("/", "_").replace(".", "_").replace("-", "_")
            lines.append(f"    {safe_id}[{node.label}]:::external")
        
        if len(external_nodes) > ext_count:
            lines.append(f"    more_ext[+{len(external_nodes) - ext_count} more external]:::external")
    
    if len(internal_nodes) > max_nodes:
        lines.append(f"    more[+{len(internal_nodes) - max_nodes} more]")
    
    lines.append("    classDef external fill:#f9f,stroke:#333,stroke-width:2px")
    
    if len(lines) == 1:
        lines.append("    A[No import graph available]")
    return "\n".join(lines)


def _er_entity_id(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", name.strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned.upper() or "ENTITY"


def data_model_er_diagram(entities: list[str]) -> str:
    names: list[str] = []
    for entity in entities:
        item = str(entity).strip()
        if item and item not in names:
            names.append(item)

    lines = ["erDiagram"]
    if not names:
        lines.append("    DOMAIN_ENTITY {")
        lines.append("        string id")
        lines.append("    }")
        lines.append("    DOMAIN_ENTITY ||--o{ DOMAIN_ENTITY : inferred_relation")
        return "\n".join(lines)

    ids = [_er_entity_id(name) for name in names[:24]]
    for entity_id in ids:
        lines.append(f"    {entity_id} {{")
        lines.append("        string id")
        lines.append("    }")

    if len(ids) == 1:
        lines.append(f"    {ids[0]} ||--o{{ {ids[0]} : self_reference")
        return "\n".join(lines)

    for idx in range(len(ids) - 1):
        lines.append(f"    {ids[idx]} ||--o{{ {ids[idx + 1]} : related_to")

    return "\n".join(lines)
