"""Code graph builder from symbols and repo structure."""

from __future__ import annotations

from pathlib import Path

from codewiki.graph.backend import GraphBackend, GraphNode, NetworkXBackend
from codewiki.models import FileRecord, RepoMap, Symbol


class CodeGraph:
    """High-level code graph with file, symbol, and external nodes."""

    def __init__(
        self,
        backend: GraphBackend | None = None,
        *,
        include_external: bool = True,
    ) -> None:
        self._backend = backend or NetworkXBackend()
        self._include_external = include_external
        self._internal_files: set[str] = set()
        self._module_to_file: dict[str, str] = {}

    @property
    def backend(self) -> GraphBackend:
        return self._backend

    def build_from_repo(
        self,
        files: list[FileRecord],
        symbols: list[Symbol],
        repo_map: RepoMap,
    ) -> None:
        """Populate graph from repository data."""
        self._internal_files = set(f.path for f in files)
        self._build_module_index(files)
        by_file_name, by_name = self._build_symbol_lookups(symbols)

        for file in files:
            self._backend.add_node(
                GraphNode(
                    id=f"file:{file.path}",
                    kind="file",
                    label=file.path,
                    meta={"lang": file.lang, "hash": file.hash},
                )
            )

        for symbol in symbols:
            self._backend.add_node(
                GraphNode(
                    id=symbol.id,
                    kind="symbol",
                    label=f"{symbol.name} ({symbol.kind})",
                    meta={"path": symbol.path, "name": symbol.name, "symbol_kind": symbol.kind},
                )
            )
            self._backend.add_edge(f"file:{symbol.path}", symbol.id, "defines")

        # Prefer resolved file-level dependencies from repo_map when available.
        import_edges = repo_map.import_graph if repo_map.import_graph else {}
        for source_path, deps in import_edges.items():
            for dep in deps:
                if dep in self._internal_files:
                    self._backend.add_edge(f"file:{source_path}", f"file:{dep}", "imports")
                elif self._include_external:
                    ext_id = f"external:{dep}"
                    if not self._backend.has_node(ext_id):
                        self._backend.add_node(
                            GraphNode(id=ext_id, kind="external", label=dep, meta={"module": dep})
                        )
                    self._backend.add_edge(f"file:{source_path}", ext_id, "imports")

        # Fallback path if repo_map is missing imports.
        if not import_edges:
            for symbol in symbols:
                for imp in symbol.imports:
                    target_file = self._resolve_import(imp, symbol.path)
                    if target_file:
                        if target_file in self._internal_files:
                            self._backend.add_edge(f"file:{symbol.path}", f"file:{target_file}", "imports")
                        elif self._include_external:
                            ext_id = f"external:{imp}"
                            if not self._backend.has_node(ext_id):
                                self._backend.add_node(
                                    GraphNode(id=ext_id, kind="external", label=imp, meta={"module": imp})
                                )
                            self._backend.add_edge(f"file:{symbol.path}", ext_id, "imports")

        for symbol in symbols:
            for call_target in symbol.calls:
                target_id = self._resolve_call_target(call_target, symbol.path, by_file_name, by_name)
                if target_id and target_id != symbol.id:
                    self._backend.add_edge(symbol.id, target_id, "calls")

    def _build_symbol_lookups(
        self,
        symbols: list[Symbol],
    ) -> tuple[dict[tuple[str, str], list[str]], dict[str, list[str]]]:
        by_file_name: dict[tuple[str, str], list[str]] = {}
        by_name: dict[str, list[str]] = {}
        for symbol in symbols:
            by_file_name.setdefault((symbol.path, symbol.name), []).append(symbol.id)
            by_name.setdefault(symbol.name, []).append(symbol.id)
        return by_file_name, by_name

    def _resolve_call_target(
        self,
        call_target: str,
        source_path: str,
        by_file_name: dict[tuple[str, str], list[str]],
        by_name: dict[str, list[str]],
    ) -> str | None:
        target = call_target.strip()
        if not target:
            return None

        if self._backend.has_node(target):
            return target

        local_matches = by_file_name.get((source_path, target), [])
        if len(local_matches) == 1:
            return local_matches[0]

        global_matches = by_name.get(target, [])
        if len(global_matches) == 1:
            return global_matches[0]

        return None

    def _build_module_index(self, files: list[FileRecord]) -> None:
        """Build module name → file path mapping."""
        for file in files:
            if file.lang == "python":
                module = file.path.replace("/", ".").replace(".py", "")
                self._module_to_file[module] = file.path
                
                parts = module.split(".")
                for i in range(1, len(parts) + 1):
                    partial = ".".join(parts[:i])
                    if partial not in self._module_to_file:
                        self._module_to_file[partial] = file.path

            elif file.lang in {"javascript", "typescript"}:
                clean_path = file.path.replace(".js", "").replace(".ts", "").replace(".jsx", "").replace(".tsx", "")
                self._module_to_file[clean_path] = file.path
                self._module_to_file["./" + clean_path] = file.path
                self._module_to_file["../" + clean_path] = file.path

    def _resolve_import(self, module_name: str, source_file: str) -> str | None:
        """Resolve import to internal file path or mark as external."""
        if module_name in self._module_to_file:
            return self._module_to_file[module_name]

        if module_name.startswith("."):
            source_dir = str(Path(source_file).parent)
            if module_name.startswith("./"):
                candidate = str(Path(source_dir) / module_name[2:])
            elif module_name.startswith("../"):
                candidate = str(Path(source_dir).parent / module_name[3:])
            else:
                candidate = str(Path(source_dir) / module_name[1:])

            for ext in ["", ".py", ".js", ".ts", "/index.js", "/index.ts"]:
                check = candidate + ext
                if check in self._internal_files:
                    return check

        return None

    def get_internal_nodes(self) -> list[GraphNode]:
        """Get all file and symbol nodes (excluding external)."""
        nodes = []
        for edge in self._backend.all_edges():
            for node_id in [edge[0], edge[1]]:
                node = self._backend.get_node(node_id)
                if node and node.kind in {"file", "symbol"} and node not in nodes:
                    nodes.append(node)
        return nodes

    def get_external_nodes(self) -> list[GraphNode]:
        """Get all external dependency nodes."""
        nodes = []
        for edge in self._backend.all_edges():
            for node_id in [edge[0], edge[1]]:
                node = self._backend.get_node(node_id)
                if node and node.kind == "external" and node not in nodes:
                    nodes.append(node)
        return nodes

    def get_file_dependencies(self, file_path: str) -> list[str]:
        """Get files that this file imports."""
        file_id = f"file:{file_path}"
        deps = []
        for neighbor_id in self._backend.neighbors(file_id):
            if neighbor_id.startswith("file:"):
                deps.append(neighbor_id.replace("file:", ""))
        return deps

    def cycles(self) -> list[list[str]]:
        """Find cycles in the import graph."""
        return self._backend.cycles()

    def impact_analysis(self, target: str) -> set[str]:
        """Find all nodes that transitively depend on the target."""
        if target.startswith("file:"):
            node_id = target
        elif "::" in target:
            node_id = target
        else:
            node_id = f"file:{target}"
        
        if not self._backend.has_node(node_id):
            return set()
        
        return self._backend.ancestors(node_id)
