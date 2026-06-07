"""Markdown page writer with frontmatter and related/source sections."""

from __future__ import annotations

from pathlib import Path

import yaml


def write_page(
    wiki_root: Path,
    rel_path: str,
    *,
    title: str,
    page_type: str,
    audience: str,
    summary: str,
    sections: list[tuple[str, str]],
    sources: list[str] | None = None,
    related: list[str] | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Create or replace a wiki page with standard metadata and sections."""
    path = wiki_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = {
        "title": title,
        "type": page_type,
        "audience": audience,
        "sources": sources or [],
        "tags": tags or [],
    }

    lines: list[str] = []
    lines.append("---")
    lines.append(yaml.safe_dump(frontmatter, sort_keys=False).strip())
    lines.append("---")
    lines.append("")
    lines.append("## Summary")
    lines.append(summary.strip())

    for heading, body in sections:
        lines.append("")
        lines.append(f"## {heading}")
        lines.append(body.strip())

    if related:
        lines.append("")
        lines.append("## Related")
        for item in related:
            lines.append(f"- {item}")

    if sources:
        lines.append("")
        lines.append("## Sources")
        for source in sources:
            lines.append(f"- {source}")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path
