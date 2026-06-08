"""Spring framework signal pack (routes, entities, jobs, messaging)."""

from __future__ import annotations

import re

from codewiki.models import FileRecord, Signal, Symbol
from codewiki.utils import make_citation


_MAPPING_RE = re.compile(
    r"@(RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\s*(?:\((.*?)\))?",
    re.DOTALL,
)
_ENTITY_RE = re.compile(r"@Entity\b")
_CLASS_RE = re.compile(r"class\s+([A-Za-z_][A-Za-z0-9_]*)")
_SCHEDULED_RE = re.compile(r"@Scheduled\s*\(")
_KAFKA_RE = re.compile(r"@KafkaListener\s*\((.*?)\)", re.DOTALL)
_PATH_RE = re.compile(r"(?:value|path)\s*=\s*[\"']([^\"']+)[\"']")
_QUOTED_RE = re.compile(r"[\"']([^\"']+)[\"']")

_METHOD_MAP = {
    "RequestMapping": "ANY",
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}


def _line(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _extract_path(args: str) -> str:
    if not args:
        return "/"

    m = _PATH_RE.search(args)
    if m:
        return m.group(1).strip() or "/"

    m = _QUOTED_RE.search(args)
    if m:
        return m.group(1).strip() or "/"

    return "/"


def _next_class_name(text: str, start_index: int) -> str:
    m = _CLASS_RE.search(text, pos=start_index)
    return m.group(1) if m else "Entity"


def _cite(file: FileRecord, line: int) -> str:
    return make_citation(file.path, line, line)


def detect_spring_signals(files: list[FileRecord], symbols: list[Symbol]) -> list[Signal]:
    """Detect Spring-specific business signals from Java sources."""
    out: list[Signal] = []

    for file in files:
        if file.lang != "java":
            continue

        text = file.text
        if "spring" not in text.lower() and "@RestController" not in text and "@RequestMapping" not in text:
            continue

        for m in _MAPPING_RE.finditer(text):
            annotation = m.group(1)
            args = m.group(2) or ""
            method = _METHOD_MAP.get(annotation, "ANY")
            path = _extract_path(args)
            out.append(
                Signal(
                    type="api_route",
                    name=f"{method} {path}",
                    evidence=[_cite(file, _line(text, m.start()))],
                )
            )

        for m in _ENTITY_RE.finditer(text):
            class_name = _next_class_name(text, m.end())
            out.append(
                Signal(
                    type="data_model",
                    name=class_name,
                    evidence=[_cite(file, _line(text, m.start()))],
                )
            )

        for m in _SCHEDULED_RE.finditer(text):
            out.append(
                Signal(
                    type="scheduled_job",
                    name="Spring Scheduled Job",
                    evidence=[_cite(file, _line(text, m.start()))],
                )
            )

        for m in _KAFKA_RE.finditer(text):
            args = m.group(1) or ""
            topic_match = _QUOTED_RE.search(args)
            topic = topic_match.group(1).strip() if topic_match else "Kafka Listener"
            out.append(
                Signal(
                    type="messaging",
                    name=f"Kafka {topic}",
                    evidence=[_cite(file, _line(text, m.start()))],
                )
            )

    dedup: dict[tuple[str, str], Signal] = {}
    for signal in out:
        key = (signal.type, signal.name)
        if key not in dedup:
            dedup[key] = signal
        else:
            for evidence in signal.evidence:
                if evidence not in dedup[key].evidence:
                    dedup[key].evidence.append(evidence)

    return sorted(dedup.values(), key=lambda item: (item.type, item.name))
