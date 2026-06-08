"""Compliance lens focused on data handling and egress surfaces."""

from __future__ import annotations

import re
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.graph.code_graph import CodeGraph
from codewiki.lenses.base import BaseLens, LensPage, SignalDetector
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import make_citation
from codewiki.wiki.summarizer import SummaryBundle


_PII_RE = re.compile(
    r"\b(email|ssn|social security|dob|phone|address|customer[_ ]?id|pii)\b",
    re.IGNORECASE,
)
_PCI_RE = re.compile(r"\b(card|cvv|pan|pci|payment|iban|routing|account number)\b", re.IGNORECASE)
_LOG_RE = re.compile(r"\b(log|logger|audit)\b", re.IGNORECASE)
_EGRESS_RE = re.compile(r"\b(httpx?|requests\.|fetch\(|axios|s3|kafka|pubsub|smtp)\b", re.IGNORECASE)


def _line(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _detect_pattern(
    files: list[FileRecord],
    pattern: re.Pattern[str],
    signal_type: str,
    name: str,
) -> list[Signal]:
    evidence: list[str] = []
    for file in files:
        text = file.text
        for match in pattern.finditer(text):
            cite = make_citation(file.path, _line(text, match.start()), _line(text, match.start()))
            if cite not in evidence:
                evidence.append(cite)
            if len(evidence) >= 40:
                break
    if not evidence:
        return []
    return [Signal(type=signal_type, name=name, evidence=evidence)]


def detect_compliance_signals(files: list[FileRecord], symbols: list[Symbol]) -> list[Signal]:
    del symbols
    out: list[Signal] = []
    out.extend(_detect_pattern(files, _PII_RE, "compliance_pii", "PII Data Touchpoints"))
    out.extend(_detect_pattern(files, _PCI_RE, "compliance_pci", "PCI/Payment Data Touchpoints"))
    out.extend(_detect_pattern(files, _LOG_RE, "compliance_log", "Logging/Audit Touchpoints"))
    out.extend(_detect_pattern(files, _EGRESS_RE, "compliance_egress", "External Egress Touchpoints"))
    return out


class ComplianceLens(BaseLens):
    name = "compliance"

    def system_prompt_addendum(self) -> str:
        return (
            "Bias summaries toward compliance evidence: what data is read, stored, logged, "
            "and sent externally, with citations for each claim."
        )

    def extra_signal_detectors(self) -> list[SignalDetector]:
        return [detect_compliance_signals]

    def page_templates(self) -> list[str]:
        return ["compliance/data-flow-map.md"]

    def scoring(self, signals: list[Signal], graph: CodeGraph | None) -> dict[str, float]:
        del graph
        return {
            signal.type: float(len(signal.evidence))
            for signal in signals
            if signal.type.startswith("compliance_")
        }

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
        del source_root, cfg, files, symbols, repo_map, code_graph, summary_bundle

        compliance = [signal for signal in signals if signal.type.startswith("compliance_")]
        counts = {signal.name: len(signal.evidence) for signal in compliance}

        flow_lines = []
        sources: list[str] = []
        for signal in compliance:
            flow_lines.append(f"- {signal.name}: {len(signal.evidence)} observed touchpoints")
            for cite in signal.evidence:
                if cite not in sources:
                    sources.append(cite)

        controls = [
            "- Confirm retention policies for PII/PCI-bearing stores and logs.",
            "- Verify masking/redaction in logging paths and telemetry sinks.",
            "- Review egress paths for lawful basis, consent, and contractual controls.",
        ]

        return [
            LensPage(
                rel_path="compliance/data-flow-map.md",
                title="Compliance Data-Flow Map",
                page_type="compliance",
                audience="both",
                summary="PII/PCI data handling map inferred from code touchpoints and egress signals.",
                sections=[
                    (
                        "Detected Data Classes",
                        "\n".join(f"- {name}: {count}" for name, count in sorted(counts.items()))
                        or "- No compliance-specific touchpoints were detected.",
                    ),
                    ("Observed Flow Surface", "\n".join(flow_lines) or "- No flow surface inferred."),
                    ("Control Checklist", "\n".join(controls)),
                ],
                sources=sources[:60],
                tags=["lens", "compliance", "governance"],
                confidence="medium" if sources else "low",
            )
        ]
