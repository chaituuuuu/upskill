"""Incremental update support using source hash manifests."""

from __future__ import annotations

import json
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.ingest.parser import parse_symbols
from codewiki.ingest.repo_map import build_repo_map
from codewiki.ingest.walker import walk_source
from codewiki.signals.detectors import detect_signals
from codewiki.wiki.generator import generate_wiki
from codewiki.wiki.index_log import append_log


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


def update_wiki(source_root: Path, cfg: CodeWikiConfig) -> dict:
    """Diff source manifest and regenerate wiki if anything changed."""
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
        f"changes={changed_count} added={len(delta['added'])} removed={len(delta['removed'])} changed={len(delta['changed'])}",
    )
    return {"updated": True, "changes": delta, "pages": pages}
