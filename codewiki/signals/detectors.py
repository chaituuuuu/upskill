"""Heuristic business-signal detection from code files and symbols."""

from __future__ import annotations

import re
from collections.abc import Callable

from codewiki.models import FileRecord, Signal, Symbol
from codewiki.utils import make_citation


_ROUTE_RE = re.compile(r"@(app|router)\.(get|post|put|delete|patch)\([\"']([^\"']+)")
_SQL_MODEL_RE = re.compile(r"class\s+([A-Za-z_][A-Za-z0-9_]*)\(.*(Model|Base)\)")
_QUEUE_RE = re.compile(r"(kafka|rabbitmq|sqs|pubsub|topic|queue)", re.IGNORECASE)
_CRON_RE = re.compile(r"(cron|schedule|celery beat|apscheduler)", re.IGNORECASE)
_ENV_RE = re.compile(r"(os\.environ\[|process\.env\.)")
_INT_RE = re.compile(r"(stripe|salesforce|slack|twilio|snowflake|s3|bigquery)", re.IGNORECASE)

SignalPack = Callable[[list[FileRecord], list[Symbol]], list[Signal]]


def _path_cite(file: FileRecord, line: int = 1) -> str:
    return make_citation(file.path, line, line)


def _detect_frameworks(files: list[FileRecord]) -> set[str]:
    joined = "\n".join(file.text.lower()[:4000] for file in files)
    frameworks: set[str] = set()

    if any(token in joined for token in {"springframework", "@restcontroller", "@requestmapping"}):
        frameworks.add("spring")
    if "fastapi" in joined:
        frameworks.add("fastapi")
    if "flask" in joined:
        frameworks.add("flask")
    if "express" in joined:
        frameworks.add("express")

    return frameworks


def _load_pack_detectors(frameworks: set[str]) -> list[SignalPack]:
    packs: list[SignalPack] = []

    if "spring" in frameworks:
        from codewiki.signals.packs.spring import detect_spring_signals

        packs.append(detect_spring_signals)

    return packs


def detect_signals(
    files: list[FileRecord],
    symbols: list[Symbol],
    *,
    extra_detectors: list[SignalPack] | None = None,
) -> list[Signal]:
    """Detect coarse business signals useful for capability pages."""
    out: list[Signal] = []

    for file in files:
        text = file.text

        for m in _ROUTE_RE.finditer(text):
            name = f"{m.group(2).upper()} {m.group(3)}"
            line = text[: m.start()].count("\n") + 1
            out.append(Signal(type="api_route", name=name, evidence=[_path_cite(file, line)]))

        for m in _SQL_MODEL_RE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            out.append(Signal(type="data_model", name=m.group(1), evidence=[_path_cite(file, line)]))

        if _QUEUE_RE.search(text):
            out.append(
                Signal(type="messaging", name="Queue/Topic Integration", evidence=[_path_cite(file)])
            )
        if _CRON_RE.search(text):
            out.append(Signal(type="scheduled_job", name="Scheduled Job", evidence=[_path_cite(file)]))
        if _ENV_RE.search(text):
            out.append(Signal(type="config", name="Environment Configuration", evidence=[_path_cite(file)]))

        integ = _INT_RE.findall(text)
        for match in sorted(set(integ)):
            out.append(
                Signal(
                    type="integration",
                    name=match.title(),
                    evidence=[_path_cite(file)],
                )
            )

    frameworks = _detect_frameworks(files)
    for detector in _load_pack_detectors(frameworks):
        try:
            out.extend(detector(files, symbols))
        except Exception:
            continue

    for detector in extra_detectors or []:
        try:
            out.extend(detector(files, symbols))
        except Exception:
            continue

    # Deduplicate by (type, name)
    dedup: dict[tuple[str, str], Signal] = {}
    for signal in out:
        key = (signal.type, signal.name)
        if key not in dedup:
            dedup[key] = signal
        else:
            for e in signal.evidence:
                if e not in dedup[key].evidence:
                    dedup[key].evidence.append(e)

    return sorted(dedup.values(), key=lambda s: (s.type, s.name))
