"""Pluggable symbol extraction with Python AST and optional tree-sitter backends."""

from __future__ import annotations

import ast
import re
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

from codewiki.models import FileRecord, Symbol

try:  # pragma: no cover - optional runtime dependency
    from tree_sitter import Parser as TSParser
except Exception:  # pragma: no cover - optional runtime dependency
    TSParser = None  # type: ignore[assignment]

try:  # pragma: no cover - optional runtime dependency
    from tree_sitter_languages import get_language as _ts_bundle_get_language
except Exception:  # pragma: no cover - optional runtime dependency
    _ts_bundle_get_language = None  # type: ignore[assignment]

try:  # pragma: no cover - optional runtime dependency
    from tree_sitter_language_pack import get_language as _ts_pack_get_language
except Exception:  # pragma: no cover - optional runtime dependency
    _ts_pack_get_language = None  # type: ignore[assignment]


_JS_FUNC = re.compile(r"(?:export\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)")
_JS_CLASS = re.compile(r"(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)")
_JS_IMPORT = re.compile(r"^\s*import\s+.*?from\s+[\"']([^\"']+)[\"']", re.MULTILINE)
_QUOTED_IMPORT_RE = re.compile(r"[\"']([^\"']+)[\"']")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

_TS_FUNCTION_TYPES: dict[str, set[str]] = {
    "javascript": {
        "function_declaration",
        "method_definition",
    },
    "typescript": {
        "function_declaration",
        "method_definition",
    },
    "java": {
        "method_declaration",
        "constructor_declaration",
    },
    "go": {
        "function_declaration",
        "method_declaration",
    },
    "csharp": {
        "method_declaration",
        "constructor_declaration",
    },
}

_TS_CLASS_TYPES: dict[str, set[str]] = {
    "javascript": {"class_declaration"},
    "typescript": {"class_declaration", "interface_declaration"},
    "java": {"class_declaration", "interface_declaration", "enum_declaration"},
    "go": {"type_spec"},
    "csharp": {
        "class_declaration",
        "interface_declaration",
        "struct_declaration",
        "record_declaration",
    },
}

_TS_CALL_TYPES: dict[str, set[str]] = {
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
    "java": {"method_invocation", "object_creation_expression"},
    "go": {"call_expression"},
    "csharp": {"invocation_expression", "object_creation_expression"},
}


def _symbol_id(path: str, name: str, start_line: int) -> str:
    return f"{path}::{name}:{start_line}"


class LanguageParser(ABC):
    """Language parser contract for symbol extraction."""

    @abstractmethod
    def parse(self, file: FileRecord) -> list[Symbol]:
        """Return extracted symbols for one file."""


class PythonAstParser(LanguageParser):
    def parse(self, file: FileRecord) -> list[Symbol]:
        return _parse_python_ast(file)


class TreeSitterLanguageParser(LanguageParser):
    def __init__(self, language: str) -> None:
        self._language = language

    def parse(self, file: FileRecord) -> list[Symbol]:
        return _parse_tree_sitter(file, self._language)


class JsTsRegexParser(LanguageParser):
    def parse(self, file: FileRecord) -> list[Symbol]:
        return _parse_js_ts_regex(file)


def _parse_python_ast(file: FileRecord) -> list[Symbol]:
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
                    imports=_unique_preserve_order(imports),
                    calls=_unique_preserve_order(calls),
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
                    imports=_unique_preserve_order(imports),
                )
            )

    return symbols


def _parse_js_ts_regex(file: FileRecord) -> list[Symbol]:
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


def _parse_tree_sitter(file: FileRecord, language: str) -> list[Symbol]:
    parser = _get_tree_sitter_parser(language)
    if parser is None:
        return []

    source_bytes = file.text.encode("utf-8", errors="ignore")
    try:
        tree = parser.parse(source_bytes)
    except Exception:
        return []

    root = tree.root_node
    imports = _collect_tree_sitter_imports(root, source_bytes, language)
    symbols: list[Symbol] = []
    seen_ids: set[str] = set()

    function_types = _TS_FUNCTION_TYPES.get(language, set())
    class_types = _TS_CLASS_TYPES.get(language, set())

    for node in _walk_nodes(root):
        node_type = getattr(node, "type", "")
        if node_type in function_types:
            kind = "function"
        elif node_type in class_types:
            kind = "class"
        else:
            continue

        name = _node_name(node, source_bytes)
        if not name:
            continue

        start_line = int(getattr(node, "start_point", (0, 0))[0]) + 1
        end_line = int(getattr(node, "end_point", (0, 0))[0]) + 1
        node_id = _symbol_id(file.path, name, start_line)
        if node_id in seen_ids:
            continue

        seen_ids.add(node_id)
        signature = _node_signature(node, source_bytes)
        calls = _collect_tree_sitter_calls(node, source_bytes, language) if kind == "function" else []
        symbols.append(
            Symbol(
                id=node_id,
                kind=kind,
                name=name,
                path=file.path,
                start_line=start_line,
                end_line=end_line,
                signature=signature,
                imports=imports,
                calls=calls,
            )
        )

    return symbols


def _collect_tree_sitter_imports(root: Any, source: bytes, language: str) -> list[str]:
    imports: list[str] = []

    for node in _walk_nodes(root):
        node_type = getattr(node, "type", "")
        raw = _node_text(node, source)

        if language in {"javascript", "typescript"} and node_type == "import_statement":
            m = re.search(r"from\s+[\"']([^\"']+)[\"']", raw)
            if m:
                imports.append(m.group(1).strip())
                continue
            m = _QUOTED_IMPORT_RE.search(raw)
            if m:
                imports.append(m.group(1).strip())
                continue

        if language == "java" and node_type == "import_declaration":
            text = raw.strip().removeprefix("import").strip().removeprefix("static").strip()
            text = text.removesuffix(";").strip()
            if text:
                imports.append(text)

        if language == "go" and node_type == "import_spec":
            m = _QUOTED_IMPORT_RE.search(raw)
            if m:
                imports.append(m.group(1).strip())

        if language == "csharp" and node_type == "using_directive":
            text = raw.strip().removeprefix("using").strip().removeprefix("static").strip()
            text = text.removesuffix(";").strip()
            if text:
                imports.append(text)

    return _unique_preserve_order(imports)


def _collect_tree_sitter_calls(node: Any, source: bytes, language: str) -> list[str]:
    calls: list[str] = []
    call_types = _TS_CALL_TYPES.get(language, {"call_expression"})

    for child in _walk_nodes(node):
        if getattr(child, "type", "") not in call_types:
            continue

        target = None
        if hasattr(child, "child_by_field_name"):
            target = child.child_by_field_name("function")
            if target is None:
                target = child.child_by_field_name("name")

        if target is None:
            children = list(getattr(child, "children", []))
            target = children[0] if children else child

        call_name = _extract_call_name(_node_text(target, source))
        if call_name and call_name not in calls:
            calls.append(call_name)

    return calls


def _extract_call_name(raw: str) -> str:
    tokens = _IDENT_RE.findall(raw)
    for token in reversed(tokens):
        if token not in {"new", "await", "this", "super"}:
            return token
    return ""


def _node_name(node: Any, source: bytes) -> str:
    name_node = None
    if hasattr(node, "child_by_field_name"):
        name_node = node.child_by_field_name("name")

    if name_node is None:
        for child in getattr(node, "children", []):
            if getattr(child, "type", "") in {
                "identifier",
                "type_identifier",
                "property_identifier",
                "field_identifier",
            }:
                name_node = child
                break

    if name_node is None:
        return ""

    raw = _node_text(name_node, source).strip()
    tokens = _IDENT_RE.findall(raw)
    return tokens[-1] if tokens else ""


def _node_signature(node: Any, source: bytes) -> str:
    raw = _node_text(node, source).strip()
    if not raw:
        return ""
    return raw.splitlines()[0][:120]


def _node_text(node: Any, source: bytes) -> str:
    start_byte = int(getattr(node, "start_byte", 0))
    end_byte = int(getattr(node, "end_byte", 0))
    if end_byte <= start_byte:
        return ""
    return source[start_byte:end_byte].decode("utf-8", errors="ignore")


def _walk_nodes(root: Any):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        children = list(getattr(node, "children", []))
        stack.extend(reversed(children))


def _unique_preserve_order(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = value.strip()
        if text and text not in out:
            out.append(text)
    return out


@lru_cache(maxsize=8)
def _get_tree_sitter_parser(language: str):
    if TSParser is None:
        return None

    ts_language = _resolve_tree_sitter_language(language)
    if ts_language is None:
        return None

    parser = TSParser()
    try:
        parser.set_language(ts_language)
    except AttributeError:
        try:
            parser.language = ts_language
        except Exception:
            return None
    except Exception:
        return None

    return parser


def _resolve_tree_sitter_language(language: str):
    key_candidates = [language]
    if language == "csharp":
        key_candidates = ["c_sharp", "csharp"]

    getters = [_ts_bundle_get_language, _ts_pack_get_language]
    for getter in getters:
        if getter is None:
            continue

        for key in key_candidates:
            try:
                lang = getter(key)
            except Exception:
                continue
            if lang is not None:
                return lang

    return None


def parse_symbols(files: list[FileRecord], *, parser_backend: str = "auto") -> list[Symbol]:
    """Extract symbols across supported languages with graceful backend fallback."""
    out: list[Symbol] = []

    backend = parser_backend.strip().lower()
    if backend not in {"auto", "ast", "tree-sitter"}:
        backend = "auto"

    for file in files:
        parsers = _parser_chain_for_file(file.lang, backend)
        for parser in parsers:
            parsed = parser.parse(file)
            if parsed:
                out.extend(parsed)
                break

    return out


def _parser_chain_for_file(language: str, parser_backend: str) -> list[LanguageParser]:
    if language == "python":
        return [PythonAstParser()]

    if language in {"javascript", "typescript", "java", "go", "csharp"}:
        chain: list[LanguageParser] = []
        if parser_backend in {"auto", "tree-sitter"}:
            chain.append(TreeSitterLanguageParser(language))
        if language in {"javascript", "typescript"}:
            chain.append(JsTsRegexParser())
        return chain

    return []
