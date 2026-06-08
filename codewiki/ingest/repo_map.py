"""Repository-level structural summaries derived from files and symbols."""

from __future__ import annotations

from pathlib import Path

from codewiki.models import FileRecord, RepoMap, Symbol


_FRAMEWORK_HINTS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "express": "Express",
    "next": "Next.js",
    "react": "React",
    "spring": "Spring",
}

_ENTRYPOINT_NAMES = {
    "main.py",
    "app.py",
    "server.py",
    "manage.py",
    "index.ts",
    "index.js",
}


def _build_module_index(files: list[FileRecord]) -> dict[str, str]:
    index: dict[str, str] = {}
    for file in files:
        path = file.path
        if file.lang == "python":
            module = path.replace("/", ".")
            if module.endswith(".py"):
                module = module[:-3]

            index[module] = path
            if module.endswith(".__init__"):
                index[module[: -len(".__init__")]] = path
        elif file.lang in {"javascript", "typescript"}:
            clean = (
                path.replace(".js", "")
                .replace(".ts", "")
                .replace(".jsx", "")
                .replace(".tsx", "")
            )
            index[clean] = path
            index["./" + clean] = path
    return index


def _resolve_import(
    module_name: str,
    source_file: str,
    module_index: dict[str, str],
    internal_files: set[str],
) -> str | None:
    if not module_name:
        return None

    if module_name in module_index:
        return module_index[module_name]

    if module_name.startswith("."):
        source_dir = Path(source_file).parent
        if module_name.startswith("./"):
            base = source_dir / module_name[2:]
        elif module_name.startswith("../"):
            base = source_dir.parent / module_name[3:]
        else:
            base = source_dir / module_name[1:]

        base_str = base.as_posix()
        for suffix in [
            "",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            "/__init__.py",
            "/index.js",
            "/index.ts",
            "/index.jsx",
            "/index.tsx",
        ]:
            candidate = base_str + suffix
            if candidate in internal_files:
                return candidate

    return None


def build_repo_map(root: Path, files: list[FileRecord], symbols: list[Symbol]) -> RepoMap:
    language_stats: dict[str, int] = {}
    for f in files:
        language_stats[f.lang] = language_stats.get(f.lang, 0) + 1

    internal_files = {f.path for f in files}
    module_index = _build_module_index(files)

    import_graph: dict[str, list[str]] = {}
    for s in symbols:
        import_graph.setdefault(s.path, [])
        for dep in s.imports:
            resolved = _resolve_import(dep, s.path, module_index, internal_files)
            entry = resolved or dep
            if entry and entry not in import_graph[s.path]:
                import_graph[s.path].append(entry)

    joined = "\n".join(f.text[:2000].lower() for f in files)
    frameworks: list[str] = []
    for key, label in _FRAMEWORK_HINTS.items():
        if key in joined and label not in frameworks:
            frameworks.append(label)

    entrypoints: list[str] = []
    for f in files:
        name = Path(f.path).name
        if name in _ENTRYPOINT_NAMES and f.path not in entrypoints:
            entrypoints.append(f.path)
        if f.lang == "python" and "if __name__ == \"__main__\"" in f.text:
            if f.path not in entrypoints:
                entrypoints.append(f.path)

    return RepoMap(
        root=root,
        file_tree=sorted(f.path for f in files),
        language_stats=language_stats,
        import_graph=import_graph,
        frameworks=sorted(frameworks),
        entrypoints=sorted(entrypoints),
    )
