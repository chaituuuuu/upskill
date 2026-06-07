"""Lint checks for citation/link integrity and wiki graph health."""

from __future__ import annotations

import re
from pathlib import Path

_CITE_RE = re.compile(r"([A-Za-z0-9_./-]+:L\d+-L\d+)")
_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")


def run_lint(wiki_root: Path) -> dict:
    pages = sorted(p for p in wiki_root.rglob("*.md") if p.is_file())
    if not pages:
        return {
            "pages": 0,
            "broken_links": [],
            "missing_citations": [],
            "orphans": [],
            "summary": "No wiki pages found.",
        }

    broken_links: list[str] = []
    missing_citations: list[str] = []
    inbound: dict[str, int] = {}
    all_page_keys = {p.relative_to(wiki_root).as_posix() for p in pages}

    for page in pages:
        rel = page.relative_to(wiki_root).as_posix()
        text = page.read_text(encoding="utf-8", errors="ignore")

        cites = _CITE_RE.findall(text)
        if "## Sources" in text and not cites:
            missing_citations.append(rel)

        for link in _LINK_RE.findall(text):
            link_key = (Path(rel).parent / link).as_posix()
            link_key = str(Path(link_key)).replace("\\", "/")
            if link_key not in all_page_keys and link not in all_page_keys:
                broken_links.append(f"{rel} -> {link}")
            else:
                target = link if link in all_page_keys else link_key
                inbound[target] = inbound.get(target, 0) + 1

    # index/log are allowed to be roots without inbound refs
    orphans = [
        p for p in sorted(all_page_keys)
        if p not in {"index.md", "log.md"} and inbound.get(p, 0) == 0 and p != "00-overview/executive-summary.md"
    ]

    return {
        "pages": len(pages),
        "broken_links": sorted(set(broken_links)),
        "missing_citations": sorted(set(missing_citations)),
        "orphans": orphans,
        "summary": (
            f"pages={len(pages)} broken_links={len(set(broken_links))} "
            f"missing_citations={len(set(missing_citations))} orphans={len(orphans)}"
        ),
    }
