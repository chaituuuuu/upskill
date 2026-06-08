"""Onboarding lens pages and ordering hints."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.graph.code_graph import CodeGraph
from codewiki.lenses.base import BaseLens, LensPage
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.wiki.summarizer import SummaryBundle


class OnboardingLens(BaseLens):
    name = "onboarding"

    def system_prompt_addendum(self) -> str:
        return (
            "Prioritize newcomer ramp-up: explain ownership boundaries, first workflows, "
            "and practical reading order before deep internals."
        )

    def page_templates(self) -> list[str]:
        return ["onboarding/ramp-plan.md"]

    def scoring(self, signals: list[Signal], graph: CodeGraph | None) -> dict[str, float]:
        score = {
            "api_routes": float(sum(1 for signal in signals if signal.type == "api_route")),
            "integrations": float(sum(1 for signal in signals if signal.type == "integration")),
        }
        if graph is not None:
            try:
                score["graph_edges"] = float(len(graph.backend.all_edges()))
            except Exception:
                score["graph_edges"] = 0.0
        return score

    def extra_pages(
        self,
        *,
        source_root: Path,
        cfg: CodeWikiConfig,
        files: list[FileRecord],
        symbols: list[Symbol],
        repo_map: RepoMap,
        signals: list[Signal],
        code_graph: CodeGraph | None,
        summary_bundle: SummaryBundle | None,
    ) -> list[LensPage]:
        component_counts: Counter[str] = Counter(file.path.split("/", 1)[0] for file in files if file.path)
        top_components = [name for name, _ in component_counts.most_common(5)]

        ramp_lines = [
            "- Week 1: read 00-overview pages and run the service locally.",
            "- Week 2: trace one capability end-to-end through components and integrations.",
            "- Week 3+: own one workflow and one reliability concern (retry/error handling).",
        ]
        if top_components:
            ramp_lines.append(f"- Suggested component order: {', '.join(top_components)}")

        entrypoint_lines = [f"- {item}" for item in repo_map.entrypoints] or ["- No explicit entrypoint detected."]
        source_lines = [signal.evidence[0] for signal in signals if signal.evidence][:24]

        return [
            LensPage(
                rel_path="onboarding/ramp-plan.md",
                title="Onboarding Ramp Plan",
                page_type="onboarding",
                audience="both",
                summary="Ramp-oriented reading order and ownership map for new contributors.",
                sections=[
                    ("30-60-90 Plan", "\n".join(ramp_lines)),
                    ("Entrypoints To Start With", "\n".join(entrypoint_lines)),
                    (
                        "Primary Capabilities",
                        "\n".join(f"- {signal.name}" for signal in signals if signal.type == "api_route")
                        or "- No API routes detected.",
                    ),
                ],
                sources=source_lines,
                tags=["lens", "onboarding"],
                confidence="medium",
            )
        ]
