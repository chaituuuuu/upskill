---
name: CodeWiki Python Standards
description: Use when editing Python modules in codewiki package, CLI wiring, pipeline logic, parsing, indexing, or wiki generation.
applyTo: "codewiki/**/*.py"
---

# CodeWiki Python Standards

- Keep module responsibilities clear and avoid circular imports.
- Preserve typed function signatures and dataclass contracts.
- For pipeline logic, prefer explicit data flow over hidden global state.
- If changing config behavior, ensure precedence remains predictable.
- For retrieval and generation paths, preserve citation-first grounding behavior.
