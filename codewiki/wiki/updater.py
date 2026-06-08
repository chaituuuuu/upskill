"""Incremental update support using source hash manifests and pagemap targeting."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import yaml

from codewiki.config import CodeWikiConfig
from codewiki.ingest.parser import parse_symbols
from codewiki.ingest.repo_map import build_repo_map
from codewiki.ingest.walker import walk_source
from codewiki.signals.detectors import detect_signals
from codewiki.utils import safe_slug
from codewiki.wiki.generator import generate_wiki
from codewiki.wiki.index_log import append_contradiction, append_log
from codewiki.wiki.pagemap import load_pagemap, resolve_affected_pages


_MANIFEST = ".codewiki_manifest.json"


def _manifest_path(wiki_root: Path) -> Path:
    return wiki_root / _MANIFEST


def _build_manifest(files: list) -> dict:
    return {
        "files": {f.path: f.hash for f in files},
    }


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"files": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"files": {}}


def _diff(old: dict, new: dict) -> dict:
    old_files = old.get("files", {})
    new_files = new.get("files", {})
    old_keys = set(old_files)
    new_keys = set(new_files)

    added = sorted(new_keys - old_keys)
    removed = sorted(old_keys - new_keys)
    changed = sorted(k for k in old_keys & new_keys if old_files[k] != new_files[k])
    return {"added": added, "removed": removed, "changed": changed}


def _capability_slug(signal_name: str) -> str:
    segment = signal_name.split(" ", 1)[-1].split("/")
    capability = segment[1] if len(segment) > 1 and segment[1] else "general"
    return safe_slug(capability)


def _predict_pages(
    changed_files: set[str],
    delta: dict,
    signals,
) -> set[str]:
    pages: set[str] = set()

    for source_file in changed_files:
        if not source_file:
            continue
        component = source_file.split("/", 1)[0]
        pages.add(f"components/{safe_slug(component)}.md")

    changed_or_added = set(delta.get("changed", [])) | set(delta.get("added", []))
    signal_types_for_glossary = {"data_model", "integration", "config", "messaging"}
    needs_glossary = False
    needs_integrations = False
    for signal in signals:
        evidence_paths = {
            cite.split(":L", 1)[0]
            for cite in signal.evidence
            if ":L" in cite
        }
        if not evidence_paths.intersection(changed_or_added):
            continue
        if signal.type == "api_route":
            pages.add(f"capabilities/{_capability_slug(signal.name)}.md")
        if signal.type in signal_types_for_glossary:
            needs_glossary = True
        if signal.type == "integration":
            needs_integrations = True

    if needs_glossary:
        pages.add("domain/glossary.md")
    if needs_integrations:
        pages.add("integrations/external-systems.md")

    if delta.get("added") or delta.get("removed"):
        pages.update(
            {
                "00-overview/tech-stack.md",
                "00-overview/component-graph.md",
                "00-overview/executive-summary.md",
            }
        )

    return pages


def _extract_summary(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    in_summary = False
    collected: list[str] = []
    for line in lines:
        if line.strip() == "## Summary":
            in_summary = True
            continue
        if in_summary and line.startswith("## "):
            break
        if in_summary:
            collected.append(line.strip())
    return " ".join(" ".join(collected).split()).strip().lower()


def _summary_changed(before: str, after: str) -> bool:
    old_summary = _extract_summary(before)
    new_summary = _extract_summary(after)
    if not old_summary or not new_summary:
        return False
    return old_summary != new_summary


def _is_locked(page_path: Path, human_edited: bool) -> bool:
    if human_edited:
        return True
    if not page_path.exists():
        return False
    try:
        text = page_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return "<!-- codewiki:locked -->" in text


def _mark_contradiction(page_path: Path) -> None:
    if not page_path.exists():
        return
    try:
        text = page_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    if not text.startswith("---\n"):
        return
    try:
        _, raw_frontmatter, body = text.split("---\n", 2)
    except ValueError:
        return

    frontmatter = yaml.safe_load(raw_frontmatter)
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    if frontmatter.get("contradiction") is True:
        return

    frontmatter["contradiction"] = True
    rebuilt = "---\n" + yaml.safe_dump(frontmatter, sort_keys=False).strip() + "\n---\n" + body
    page_path.write_text(rebuilt, encoding="utf-8")


def _proposed_page_path(page_path: Path) -> Path:
    return page_path.with_name(f"{page_path.stem}.proposed.md")


def _write_proposed_pages(
    *,
    source_root: Path,
    cfg: CodeWikiConfig,
    files,
    symbols,
    repo_map,
    signals,
    locked_pages: set[str],
) -> dict[str, str]:
    if not locked_pages:
        return {}

    with tempfile.TemporaryDirectory(prefix="codewiki-proposed-") as tmp_dir:
        temp_cfg = cfg.model_copy(deep=True)
        temp_cfg.wiki.output_dir = Path(tmp_dir)
        generate_wiki(
            source_root=source_root,
            cfg=temp_cfg,
            files=files,
            symbols=symbols,
            repo_map=repo_map,
            signals=signals,
            only_pages=locked_pages,
        )

        written: dict[str, str] = {}
        for page in sorted(locked_pages):
            generated_page = temp_cfg.wiki.output_dir / page
            if not generated_page.exists():
                continue

            real_page = cfg.wiki.output_dir / page
            proposed_path = _proposed_page_path(real_page)
            proposed_path.parent.mkdir(parents=True, exist_ok=True)
            proposed_path.write_text(
                generated_page.read_text(encoding="utf-8", errors="ignore"),
                encoding="utf-8",
            )
            written[page] = proposed_path.relative_to(cfg.wiki.output_dir).as_posix()

        return written


def update_wiki(source_root: Path, cfg: CodeWikiConfig) -> dict:
    """Diff source manifest and regenerate only affected wiki pages."""
    wiki_root = cfg.wiki.output_dir
    wiki_root.mkdir(parents=True, exist_ok=True)

    files = walk_source(source_root, cfg)
    new_manifest = _build_manifest(files)

    manifest_path = _manifest_path(wiki_root)
    old_manifest = _load_manifest(manifest_path)
    delta = _diff(old_manifest, new_manifest)
    changed_count = len(delta["added"]) + len(delta["removed"]) + len(delta["changed"])

    if changed_count == 0:
        append_log(wiki_root, "update", "no changes detected")
        return {"updated": False, "changes": delta, "pages": 0}

    symbols = parse_symbols(files)
    repo_map = build_repo_map(source_root, files, symbols)
    signals = detect_signals(files, symbols)

    old_pagemap = load_pagemap(wiki_root)
    changed_files = set(delta["added"]) | set(delta["removed"]) | set(delta["changed"])
    affected_pages = resolve_affected_pages(old_pagemap, changed_files)
    affected_pages.update(_predict_pages(changed_files, delta, signals))

    if not old_pagemap:
        pages = generate_wiki(
            source_root=source_root,
            cfg=cfg,
            files=files,
            symbols=symbols,
            repo_map=repo_map,
            signals=signals,
        )
        manifest_path.write_text(json.dumps(new_manifest, indent=2), encoding="utf-8")
        append_log(
            wiki_root,
            "update",
            "pagemap missing; performed full regeneration",
        )
        return {
            "updated": True,
            "changes": delta,
            "pages": pages,
            "affected_pages": [],
            "regenerated_pages": [],
            "skipped_locked": [],
            "proposed_pages": [],
            "contradictions": [],
        }

    locked_pages: set[str] = set()
    regen_pages: set[str] = set()
    before_text: dict[str, str] = {}
    for page in sorted(affected_pages):
        page_path = wiki_root / page
        existing_record = old_pagemap.get(page)
        locked = _is_locked(page_path, existing_record.human_edited if existing_record else False)
        if locked:
            locked_pages.add(page)
            continue

        regen_pages.add(page)
        if page_path.exists():
            before_text[page] = page_path.read_text(encoding="utf-8", errors="ignore")
        else:
            before_text[page] = ""

    pages = 0
    emitted_pages: set[str] = set()
    if regen_pages:
        pages = generate_wiki(
            source_root=source_root,
            cfg=cfg,
            files=files,
            symbols=symbols,
            repo_map=repo_map,
            signals=signals,
            only_pages=regen_pages,
            written_pages=emitted_pages,
        )

    stale_removed: list[str] = []
    for page in sorted(regen_pages - emitted_pages):
        page_path = wiki_root / page
        if page_path.exists():
            page_path.unlink()
            stale_removed.append(page)

    proposed_pages_map = _write_proposed_pages(
        source_root=source_root,
        cfg=cfg,
        files=files,
        symbols=symbols,
        repo_map=repo_map,
        signals=signals,
        locked_pages=locked_pages,
    )

    contradictions: list[str] = []
    for page in sorted(regen_pages):
        page_path = wiki_root / page
        if not page_path.exists():
            continue
        after_text = page_path.read_text(encoding="utf-8", errors="ignore")
        if _summary_changed(before_text.get(page, ""), after_text):
            _mark_contradiction(page_path)
            append_contradiction(wiki_root, page)
            contradictions.append(page)

    for page in sorted(proposed_pages_map):
        page_path = wiki_root / page
        proposed_rel = proposed_pages_map[page]
        proposed_path = wiki_root / proposed_rel
        if not page_path.exists() or not proposed_path.exists():
            continue

        current_text = page_path.read_text(encoding="utf-8", errors="ignore")
        proposed_text = proposed_path.read_text(encoding="utf-8", errors="ignore")
        if _summary_changed(current_text, proposed_text):
            _mark_contradiction(proposed_path)
            append_contradiction(wiki_root, page)
            if page not in contradictions:
                contradictions.append(page)

    manifest_path.write_text(json.dumps(new_manifest, indent=2), encoding="utf-8")
    append_log(
        wiki_root,
        "update",
        (
            f"changes={changed_count} added={len(delta['added'])} removed={len(delta['removed'])} "
            f"changed={len(delta['changed'])} affected_pages={len(affected_pages)} "
            f"regenerated={len(regen_pages)} locked_skipped={len(locked_pages)} "
            f"stale_removed={len(stale_removed)} "
            f"proposed={len(proposed_pages_map)} "
            f"contradictions={len(contradictions)}"
        ),
    )

    return {
        "updated": True,
        "changes": delta,
        "pages": pages,
        "affected_pages": sorted(affected_pages),
        "regenerated_pages": sorted(regen_pages),
        "stale_removed_pages": stale_removed,
        "skipped_locked": sorted(locked_pages),
        "proposed_pages": sorted(proposed_pages_map.values()),
        "contradictions": contradictions,
    }
