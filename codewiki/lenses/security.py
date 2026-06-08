"""Security lens with auth/secret/OWASP-oriented signal extensions."""

from __future__ import annotations

import re
from pathlib import Path

from codewiki.config import CodeWikiConfig
from codewiki.graph.code_graph import CodeGraph
from codewiki.lenses.base import BaseLens, LensPage, SignalDetector
from codewiki.models import FileRecord, RepoMap, Signal, Symbol
from codewiki.utils import make_citation
from codewiki.wiki.summarizer import SummaryBundle


_AUTH_RE = re.compile(r"\b(auth|oauth|jwt|token|login|session|rbac|acl)\b", re.IGNORECASE)
_SECRET_RE = re.compile(r"\b(api[_-]?key|secret|password|token)\b\s*[:=]", re.IGNORECASE)
_OWASP_RE = re.compile(
    r"\b(sql|xss|csrf|ssrf|deserializ|path traversal|command injection|unsafe)\b",
    re.IGNORECASE,
)


def _line(text: str, index: int) -> int:
    return text[:index].count("\n") + 1


def _scan(files: list[FileRecord], pattern: re.Pattern[str], sig_type: str, name: str) -> list[Signal]:
    evidence: list[str] = []
    for file in files:
        text = file.text
        for match in pattern.finditer(text):
            line = _line(text, match.start())
            cite = make_citation(file.path, line, line)
            if cite not in evidence:
                evidence.append(cite)
            if len(evidence) >= 40:
                break
    if not evidence:
        return []
    return [Signal(type=sig_type, name=name, evidence=evidence)]


def detect_security_signals(files: list[FileRecord], symbols: list[Symbol]) -> list[Signal]:
    del symbols
    out: list[Signal] = []
    out.extend(_scan(files, _AUTH_RE, "security_auth", "Authentication/Authorization Flow"))
    out.extend(_scan(files, _SECRET_RE, "security_secret", "Secrets Handling Surface"))
    out.extend(_scan(files, _OWASP_RE, "security_owasp", "OWASP Touchpoints"))
    return out


class SecurityLens(BaseLens):
    name = "security"

    def system_prompt_addendum(self) -> str:
        return (
            "Highlight authentication flows, secret handling, and OWASP-relevant touchpoints. "
            "Prefer concrete control language with direct citations."
        )

    def extra_signal_detectors(self) -> list[SignalDetector]:
        return [detect_security_signals]

    def page_templates(self) -> list[str]:
        return ["security/security-posture.md"]

    def scoring(self, signals: list[Signal], graph: CodeGraph | None) -> dict[str, float]:
        del graph
        return {
            signal.type: float(len(signal.evidence))
            for signal in signals
            if signal.type.startswith("security_")
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

        sec_signals = [signal for signal in signals if signal.type.startswith("security_")]
        scores = self.scoring(signals, None)

        sources: list[str] = []
        findings: list[str] = []
        for signal in sec_signals:
            findings.append(f"- {signal.name}: {len(signal.evidence)} matching touchpoints")
            for cite in signal.evidence:
                if cite not in sources:
                    sources.append(cite)

        controls = [
            "- Validate auth boundaries and role checks on externally reachable handlers.",
            "- Remove or rotate hardcoded secrets and prefer secret-manager injection.",
            "- Prioritize review of OWASP-marked paths in pre-release threat modeling.",
        ]

        return [
            LensPage(
                rel_path="security/security-posture.md",
                title="Security Posture Overview",
                page_type="security",
                audience="both",
                summary="Auth, secrets, and OWASP touchpoints inferred from code signals.",
                sections=[
                    ("Findings", "\n".join(findings) or "- No security-specific signals detected."),
                    (
                        "Signal Scores",
                        "\n".join(f"- {key}: {value:.1f}" for key, value in sorted(scores.items()))
                        or "- No scores available.",
                    ),
                    ("Recommended Controls", "\n".join(controls)),
                ],
                sources=sources[:60],
                tags=["lens", "security", "governance"],
                confidence="medium" if sources else "low",
            )
        ]
