"""Graph backend interface and NetworkX implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import networkx as nx


@dataclass(slots=True)
class GraphNode:
    """Node in the code graph."""

    id: str
    kind: str
    label: str
    meta: dict[str, str] = field(default_factory=dict)


class GraphBackend(ABC):
    """Abstract interface for graph storage backends."""

    @abstractmethod
    def add_node(self, node: GraphNode) -> None:
        """Add a node to the graph."""

    @abstractmethod
    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """Add a directed edge between two nodes."""

    @abstractmethod
    def has_node(self, node_id: str) -> bool:
        """Check if a node exists."""

    @abstractmethod
    def neighbors(self, node_id: str) -> list[str]:
        """Get immediate neighbors (successors) of a node."""

    @abstractmethod
    def ancestors(self, node_id: str) -> set[str]:
        """Get all ancestors (nodes that lead to this node)."""

    @abstractmethod
    def descendants(self, node_id: str) -> set[str]:
        """Get all descendants (nodes reachable from this node)."""

    @abstractmethod
    def cycles(self) -> list[list[str]]:
        """Find all cycles in the graph."""

    @abstractmethod
    def to_subgraph(self, node_ids: set[str]) -> dict[str, Any]:
        """Extract a subgraph containing only specified nodes."""

    @abstractmethod
    def get_node(self, node_id: str) -> GraphNode | None:
        """Retrieve a node by ID."""

    @abstractmethod
    def all_edges(self) -> list[tuple[str, str, str]]:
        """Return all edges as (from_id, to_id, edge_type) tuples."""


class NetworkXBackend(GraphBackend):
    """NetworkX-based in-memory graph backend."""

    def __init__(self) -> None:
        self._graph = nx.MultiDiGraph()
        self._nodes: dict[str, GraphNode] = {}

    def add_node(self, node: GraphNode) -> None:
        self._nodes[node.id] = node
        self._graph.add_node(node.id, kind=node.kind, label=node.label, **node.meta)

    def add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        if not self.has_node(from_id) or not self.has_node(to_id):
            return
        self._graph.add_edge(from_id, to_id, edge_type=edge_type)

    def has_node(self, node_id: str) -> bool:
        return node_id in self._nodes

    def get_node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def neighbors(self, node_id: str) -> list[str]:
        if not self.has_node(node_id):
            return []
        return list(self._graph.successors(node_id))

    def ancestors(self, node_id: str) -> set[str]:
        if not self.has_node(node_id):
            return set()
        return nx.ancestors(self._graph, node_id)

    def descendants(self, node_id: str) -> set[str]:
        if not self.has_node(node_id):
            return set()
        return nx.descendants(self._graph, node_id)

    def cycles(self) -> list[list[str]]:
        try:
            return list(nx.simple_cycles(self._graph))
        except Exception:
            return []

    def to_subgraph(self, node_ids: set[str]) -> dict[str, Any]:
        valid_ids = {nid for nid in node_ids if self.has_node(nid)}
        subgraph = self._graph.subgraph(valid_ids)
        return {
            "nodes": [self._nodes[nid] for nid in valid_ids if nid in self._nodes],
            "edges": [
                (u, v, data.get("edge_type", "unknown"))
                for u, v, data in subgraph.edges(data=True)
            ],
        }

    def all_edges(self) -> list[tuple[str, str, str]]:
        return [
            (u, v, data.get("edge_type", "unknown"))
            for u, v, data in self._graph.edges(data=True)
        ]
