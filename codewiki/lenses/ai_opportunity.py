"""AI Opportunity lens: detect automation candidates and emit ranked register."""

from __future__ import annotations

import re
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.graph.code_graph import CodeGraph
from codewiki.lenses.base import BaseLens, LensPage, SignalDetector
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import make_citation
from codewiki.wiki.summarizer import SummaryBundle


_CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    "rule_engine_density": re.compile(r"\b(if|elif|else if|switch|case|rule[s]?)\b", re.IGNORECASE),
    "manual_review_queue": re.compile(
        r"\b(manual|review|approval|approve|queue|triage)\b",
        re.IGNORECASE,
    ),
    "batch_reconciliation": re.compile(
        r"\b(batch|reconcile|reconciliation|settlement|ledger)\b",
        re.IGNORECASE,
    ),
    "regex_classification": re.compile(
        r"\b(regex|re\.compile|pattern|keyword classification|keyword)\b",
        re.IGNORECASE,
    ),
    "hardcoded_thresholds": re.compile(
        r"\b(threshold|limit|max|min|cutoff|score)\b|(?:>=|<=|>|<)\s*\d+",
        re.IGNORECASE,
    ),
    "high_exception_flows": re.compile(
        r"\b(except|catch|raise|throw|retry|fallback|error)\b",
        re.IGNORECASE,
    ),
    "ocr_manual_data_entry": re.compile(
        r"\b(ocr|scan|scanned|manual entry|data entry|form extraction)\b",
        re.IGNORECASE,
    ),
}

_OPPORTUNITY_PLAYBOOK: dict[str, dict[str, str]] = {
    "rule_engine_density": {
        "capability": "Decisioning and policy application",
        "lever": "Policy copilot or learned decision service",
        "value": "High",
        "effort": "Medium",
        "explainability": "High",
        "model_risk": "Medium",
        "pii": "Medium",
    },
    "manual_review_queue": {
        "capability": "Manual review and approval queue",
        "lever": "Case triage assistant with recommendation summaries",
        "value": "High",
        "effort": "Low",
        "explainability": "High",
        "model_risk": "Low",
        "pii": "Medium",
    },
    "batch_reconciliation": {
        "capability": "Batch reconciliation and exceptions",
        "lever": "Anomaly detection plus auto-resolution suggestions",
        "value": "High",
        "effort": "Medium",
        "explainability": "Medium",
        "model_risk": "Medium",
        "pii": "Medium",
    },
    "regex_classification": {
        "capability": "Regex or keyword classification",
        "lever": "Embedding/classifier-based intent and class prediction",
        "value": "Medium",
        "effort": "Low",
        "explainability": "Medium",
        "model_risk": "Low",
        "pii": "Low",
    },
    "hardcoded_thresholds": {
        "capability": "Hardcoded threshold decisions",
        "lever": "Adaptive thresholding with feedback loop",
        "value": "Medium",
        "effort": "Medium",
        "explainability": "Medium",
        "model_risk": "Medium",
        "pii": "Low",
    },
    "high_exception_flows": {
        "capability": "Exception-heavy operational flow",
        "lever": "Exception prediction and assisted remediation",
        "value": "Medium",
        "effort": "Medium",
        "explainability": "Medium",
        "model_risk": "Medium",
        "pii": "Low",
    },
    "ocr_manual_data_entry": {
        "capability": "OCR or manual data entry pipeline",
        "lever": "Document AI extraction with human-in-the-loop",
        "value": "High",
        "effort": "High",
        "explainability": "Medium",
        "model_risk": "High",
        "pii": "High",
    },
}

_PRIORITY_KEYS = [
    "manual_review_queue",
    "rule_engine_density",
    "batch_reconciliation",
    "regex_classification",
    "hardcoded_thresholds",
    "high_exception_flows",
    "ocr_manual_data_entry",
]


def _line(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in out:
            out.append(item)
    return out


def detect_ai_opportunity_signals(files: list[FileRecord], symbols: list[Symbol]) -> list[Signal]:
    del symbols

    evidence_by_category: dict[str, list[str]] = {key: [] for key in _CATEGORY_PATTERNS}

    for file in files:
        text = file.text
        for key, pattern in _CATEGORY_PATTERNS.items():
            local_hits = 0
            for match in pattern.finditer(text):
                line = _line(text, match.start())
                cite = make_citation(file.path, line, line)
                if cite not in evidence_by_category[key]:
                    evidence_by_category[key].append(cite)
                local_hits += 1
                if local_hits >= 4:
                    break

    out: list[Signal] = []
    for category, evidence in evidence_by_category.items():
        if evidence:
            out.append(Signal(type="ai_opportunity_hint", name=category, evidence=evidence))

    return out


class AIOpportunityLens(BaseLens):
    name = "ai_opportunity"

    def system_prompt_addendum(self) -> str:
        return (
            "Look for AI automation opportunities in business workflows. Prioritize decisioning, "
            "manual review queues, reconciliation, and exception-heavy paths with explicit citations. "
            "Include governance implications: explainability, model risk, and PII exposure."
        )

    def extra_signal_detectors(self) -> list[SignalDetector]:
        return [detect_ai_opportunity_signals]

    def page_templates(self) -> list[str]:
        return ["opportunities/opportunity-register.md"]

    def scoring(self, signals: list[Signal], graph: CodeGraph | None) -> dict[str, float]:
        by_key = {
            signal.name: signal
            for signal in signals
            if signal.type == "ai_opportunity_hint"
        }

        graph_bonus = 0.0
        if graph is not None:
            try:
                graph_bonus = min(1.5, len(graph.backend.all_edges()) / 250.0)
            except Exception:
                graph_bonus = 0.0

        scores: dict[str, float] = {}
        for key in _PRIORITY_KEYS:
            signal = by_key.get(key)
            if signal is None:
                continue
            evidence_count = len(signal.evidence)
            weight = 1.0
            if key in {"manual_review_queue", "rule_engine_density", "batch_reconciliation"}:
                weight = 1.25
            scores[key] = round((evidence_count * 2.0 * weight) + graph_bonus, 2)

        return scores

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
        del source_root, cfg, files, symbols, repo_map, summary_bundle

        hints = {
            signal.name: signal
            for signal in signals
            if signal.type == "ai_opportunity_hint"
        }
        scores = self.scoring(signals, code_graph)

        ranked: list[tuple[float, str, list[str]]] = []
        for key in _PRIORITY_KEYS:
            signal = hints.get(key)
            if signal is None:
                continue
            ranked.append((scores.get(key, 0.0), key, signal.evidence))

        fallback_evidence = [e for signal in signals for e in signal.evidence if e]
        fallback_evidence = _dedupe(fallback_evidence)

        idx = 0
        while len(ranked) < 3 and idx < len(_PRIORITY_KEYS):
            key = _PRIORITY_KEYS[idx]
            idx += 1
            if any(existing_key == key for _, existing_key, _ in ranked):
                continue
            if not fallback_evidence:
                break
            ranked.append((0.5, key, [fallback_evidence[(idx - 1) % len(fallback_evidence)]]))

        ranked.sort(key=lambda item: item[0], reverse=True)

        rows: list[str] = []
        all_sources: list[str] = []
        for i, (score, key, evidence) in enumerate(ranked[:8], start=1):
            profile = _OPPORTUNITY_PLAYBOOK[key]
            evidence_cell = "; ".join(evidence[:2]) if evidence else "(none)"
            rows.append(
                "| "
                f"{i} | {profile['capability']} | {profile['lever']} | {profile['value']} | "
                f"{profile['effort']} | {score:.2f} | {profile['explainability']} | "
                f"{profile['model_risk']} | {profile['pii']} | {evidence_cell} |"
            )
            for cite in evidence:
                if cite not in all_sources:
                    all_sources.append(cite)

        table = [
            "| Rank | Capability | AI Lever | Value | Effort | Score | Explainability | Model Risk | PII | Evidence |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            *rows,
        ]

        governance = [
            "- Explainability: prioritize human-auditable outputs for high-impact decisions.",
            "- Model risk: route medium/high risk opportunities through validation and monitoring gates.",
            "- PII: enforce data minimization and redaction before model invocation.",
        ]

        confidence = "high" if len(ranked) >= 3 else "medium"

        return [
            LensPage(
                rel_path="opportunities/opportunity-register.md",
                title="Opportunity Register",
                page_type="opportunity",
                audience="business",
                summary=(
                    "Ranked AI opportunities derived from code evidence, scored by opportunity density "
                    "and repository structure."
                ),
                sections=[
                    ("Ranked Opportunities", "\n".join(table)),
                    ("Governance Fields", "\n".join(governance)),
                ],
                sources=all_sources[:80],
                tags=["lens", "ai_opportunity", "governance"],
                confidence=confidence,
            )
        ]
