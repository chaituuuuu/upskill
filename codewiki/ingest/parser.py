"""Lightweight symbol extraction for Python and JS/TS with graceful fallback."""

from __future__ import annotations

import ast
import re

from codewiki.models import FileRecord, Symbol


_JS_FUNC = re.compile(r"(?:export\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)")
_JS_CLASS = re.compile(r"(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)")
_JS_IMPORT = re.compile(r"^\s*import\s+.*?from\s+[\"']([^\"']+)[\"']", re.MULTILINE)


def _symbol_id(path: str, name: str, start_line: int) -> str:
    return f"{path}::{name}:{start_line}"


def _parse_python(file: FileRecord) -> list[Symbol]:
    symbols: list[Symbol] = []
    try:
        tree = ast.parse(file.text)
    except SyntaxError:
        return symbols

    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * max(0, getattr(node, "level", 0))
            base = (node.module or "").strip()

            if base:
                imports.append(prefix + base)

            for alias in node.names:
                name = alias.name.strip()
                if not name or name == "*":
                    continue
                if base:
                    imports.append(f"{prefix}{base}.{name}")
                else:
                    imports.append(prefix + name)

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            
            calls: list[str] = []
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    if isinstance(child.func, ast.Name):
                        calls.append(child.func.id)
                    elif isinstance(child.func, ast.Attribute):
                        calls.append(child.func.attr)
            
            symbols.append(
                Symbol(
                    id=_symbol_id(file.path, node.name, start),
                    kind="function",
                    name=node.name,
                    path=file.path,
                    start_line=start,
                    end_line=end,
                    signature=f"def {node.name}(...)",
                    docstring=ast.get_docstring(node) or "",
                    imports=imports,
                    calls=calls,
                )
            )
        elif isinstance(node, ast.ClassDef):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            symbols.append(
                Symbol(
                    id=_symbol_id(file.path, node.name, start),
                    kind="class",
                    name=node.name,
                    path=file.path,
                    start_line=start,
                    end_line=end,
                    signature=f"class {node.name}",
                    docstring=ast.get_docstring(node) or "",
                    imports=imports,
                )
            )

    return symbols


def _parse_js_ts(file: FileRecord) -> list[Symbol]:
    symbols: list[Symbol] = []
    imports = _JS_IMPORT.findall(file.text)
    lines = file.text.splitlines()

    for i, line in enumerate(lines, start=1):
        fm = _JS_FUNC.search(line)
        if fm:
            name = fm.group(1)
            symbols.append(
                Symbol(
                    id=_symbol_id(file.path, name, i),
                    kind="function",
                    name=name,
                    path=file.path,
                    start_line=i,
                    end_line=i,
                    signature=f"function {name}(...)",
                    imports=imports,
                )
            )
        cm = _JS_CLASS.search(line)
        if cm:
            name = cm.group(1)
            symbols.append(
                Symbol(
                    id=_symbol_id(file.path, name, i),
                    kind="class",
                    name=name,
                    path=file.path,
                    start_line=i,
                    end_line=i,
                    signature=f"class {name}",
                    imports=imports,
                )
            )

    return symbols


def parse_symbols(files: list[FileRecord]) -> list[Symbol]:
    """Extract symbols across supported languages."""
    out: list[Symbol] = []

    for file in files:
        if file.lang == "python":
            out.extend(_parse_python(file))
        elif file.lang in {"javascript", "typescript"}:
            out.extend(_parse_js_ts(file))

    return out
