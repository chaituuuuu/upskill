"""Framework-specific signal packs and registry helpers."""

from __future__ import annotations

from collections.abc import Callable

from codewiki.models import FileRecord, Signal, Symbol
from codewiki.signals.packs.express import detect_express_signals
from codewiki.signals.packs.fastapi import detect_fastapi_signals
from codewiki.signals.packs.flask import detect_flask_signals
from codewiki.signals.packs.spring import detect_spring_signals

SignalPack = Callable[[list[FileRecord], list[Symbol]], list[Signal]]

PACK_REGISTRY: dict[str, SignalPack] = {
    "spring": detect_spring_signals,
    "fastapi": detect_fastapi_signals,
    "flask": detect_flask_signals,
    "express": detect_express_signals,
}


def get_pack_detectors(frameworks: set[str]) -> list[SignalPack]:
    """Resolve pack detector callables from detected framework names."""
    out: list[SignalPack] = []
    for framework in sorted(frameworks):
        detector = PACK_REGISTRY.get(framework)
        if detector is not None:
            out.append(detector)
    return out


__all__ = [
    "PACK_REGISTRY",
    "SignalPack",
    "detect_spring_signals",
    "get_pack_detectors",
]
