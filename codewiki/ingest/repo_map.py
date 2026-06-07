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


def build_repo_map(root: Path, files: list[FileRecord], symbols: list[Symbol]) -> RepoMap:
    language_stats: dict[str, int] = {}
    for f in files:
        language_stats[f.lang] = language_stats.get(f.lang, 0) + 1

    import_graph: dict[str, list[str]] = {}
    for s in symbols:
        import_graph.setdefault(s.path, [])
        for dep in s.imports:
            if dep and dep not in import_graph[s.path]:
                import_graph[s.path].append(dep)

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
