"""Registry for pluggable CodeWiki generation lenses."""

from __future__ import annotations

from codewiki.lenses.ai_opportunity import AIOpportunityLens
from codewiki.lenses.base import BaseLens, BusinessLens
from codewiki.lenses.compliance import ComplianceLens
from codewiki.lenses.onboarding import OnboardingLens
from codewiki.lenses.security import SecurityLens


_LENS_REGISTRY: dict[str, type[BaseLens]] = {
    "business": BusinessLens,
    "onboarding": OnboardingLens,
    "compliance": ComplianceLens,
    "security": SecurityLens,
    "ai_opportunity": AIOpportunityLens,
}


def available_lenses() -> list[str]:
    return sorted(_LENS_REGISTRY)


def get_lens(name: str | None) -> BaseLens:
    key = (name or "business").strip().lower()
    lens_cls = _LENS_REGISTRY.get(key)
    if lens_cls is None:
        choices = ", ".join(available_lenses())
        raise ValueError(f"Unknown lens '{name}'. Choose one of: {choices}")
    return lens_cls()


__all__ = ["BaseLens", "available_lenses", "get_lens"]
