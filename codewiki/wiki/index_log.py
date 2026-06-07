"""Maintain index.md and log.md for generated wiki output."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def append_log(wiki_root: Path, event: str, detail: str) -> None:
    wiki_root.mkdir(parents=True, exist_ok=True)
    log_path = wiki_root / "log.md"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    entry = f"## [{stamp}] {event}\n- {detail}\n"
    with log_path.open("a", encoding="utf-8") as fh:
        if log_path.stat().st_size > 0:
            fh.write("\n")
        fh.write(entry)


def rebuild_index(wiki_root: Path) -> None:
    pages = sorted(
        p.relative_to(wiki_root).as_posix()
        for p in wiki_root.rglob("*.md")
        if p.name not in {"index.md", "log.md"}
    )

    by_folder: dict[str, list[str]] = {}
    for page in pages:
        folder = page.split("/", 1)[0] if "/" in page else "misc"
        by_folder.setdefault(folder, []).append(page)

    lines: list[str] = [
        "# CodeWiki Index",
        "",
        "Generated page catalog grouped by top-level folder.",
        "",
    ]

    for folder in sorted(by_folder):
        lines.append(f"## {folder}")
        for page in by_folder[folder]:
            title = page.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ").title()
            lines.append(f"- [{title}]({page})")
        lines.append("")

    (wiki_root / "index.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
