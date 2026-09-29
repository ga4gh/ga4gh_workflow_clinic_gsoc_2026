"""Evaluation framework and metrics for measuring AICriticAgent output quality and reproducibility."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from workflow_clinic.models.diagnosis import Finding, Remediation


@dataclass
class GoldenFinding:
    """Golden benchmark dataset case defining diagnostic input and expected quality constraints."""

    id: str
    finding: Finding
    required_concepts: list[str] = field(default_factory=list)
    required_any_of: list[list[str]] = field(default_factory=list)
    forbidden_concepts: list[str] = field(default_factory=list)
    workflow_type: str = "nextflow"
    description: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GoldenFinding:
        """Construct GoldenFinding from serialized dictionary."""
        finding_data = data["finding"]
        finding = Finding.model_validate(finding_data)
        return cls(
            id=data["id"],
            finding=finding,
            required_concepts=[c.lower() for c in data.get("required_concepts", [])],
            required_any_of=[
                [c.lower() for c in group] for group in data.get("required_any_of", [])
            ],
            forbidden_concepts=[c.lower() for c in data.get("forbidden_concepts", [])],
            workflow_type=data.get("workflow_type", "nextflow"),
            description=data.get("description", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize GoldenFinding to dictionary."""
        return {
            "id": self.id,
            "description": self.description,
            "workflow_type": self.workflow_type,
            "required_concepts": self.required_concepts,
            "required_any_of": self.required_any_of,
            "forbidden_concepts": self.forbidden_concepts,
            "finding": self.finding.model_dump(mode="json"),
        }


@dataclass
class EvaluationResult:
    """Evaluation result for a single benchmark iteration."""

    iteration: int
    golden_id: str
    schema_valid: bool
    remediation: Remediation | None = None
    raw_output: str = ""
    concepts_found: list[str] = field(default_factory=list)
    missing_concepts: list[str] = field(default_factory=list)
    forbidden_found: list[str] = field(default_factory=list)
    concept_valid: bool = False
    is_pass: bool = False
    latency_ms: float = 0.0
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize EvaluationResult to dictionary."""
        return {
            "iteration": self.iteration,
            "golden_id": self.golden_id,
            "schema_valid": self.schema_valid,
            "concept_valid": self.concept_valid,
            "is_pass": self.is_pass,
            "latency_ms": round(self.latency_ms, 2),
            "concepts_found": self.concepts_found,
            "missing_concepts": self.missing_concepts,
            "forbidden_found": self.forbidden_found,
            "error_message": self.error_message,
            "remediation": self.remediation.model_dump(mode="json")
            if self.remediation
            else None,
        }


@dataclass
class FindingSummaryMetrics:
    """Aggregated benchmark metrics across iterations for a single golden finding."""

    golden_id: str
    iterations: int
    schema_pass_count: int
    schema_pass_rate: float
    concept_pass_count: int
    concept_pass_rate: float
    overall_pass_count: int
    overall_pass_rate: float
    mean_latency_ms: float
    passed_thresholds: bool

    def to_dict(self) -> dict[str, Any]:
        """Serialize FindingSummaryMetrics to dictionary."""
        return asdict(self)


@dataclass
class BenchmarkReport:
    """Comprehensive evaluation suite benchmark report."""

    timestamp: str
    model_name: str
    total_findings: int
    total_iterations: int
    overall_schema_pass_rate: float
    overall_concept_pass_rate: float
    overall_pass_rate: float
    mean_latency_ms: float
    acceptance_criteria_met: bool
    findings_summary: dict[str, FindingSummaryMetrics]
    iterations: list[EvaluationResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize full report to dictionary."""
        return {
            "timestamp": self.timestamp,
            "model_name": self.model_name,
            "total_findings": self.total_findings,
            "total_iterations": self.total_iterations,
            "overall_schema_pass_rate": round(self.overall_schema_pass_rate, 4),
            "overall_concept_pass_rate": round(self.overall_concept_pass_rate, 4),
            "overall_pass_rate": round(self.overall_pass_rate, 4),
            "mean_latency_ms": round(self.mean_latency_ms, 2),
            "acceptance_criteria_met": self.acceptance_criteria_met,
            "findings_summary": {
                k: v.to_dict() for k, v in self.findings_summary.items()
            },
            "iterations": [i.to_dict() for i in self.iterations],
        }


class RemediationEvaluator:
    """Evaluates Remediation models against quality and correctness constraints."""

    SCHEMA_PASS_THRESHOLD: float = 0.90
    CONCEPT_PASS_THRESHOLD: float = 0.80

    @classmethod
    def evaluate(
        cls,
        golden: GoldenFinding,
        remediation: Remediation | None,
        *,
        iteration: int = 1,
        latency_ms: float = 0.0,
        error_message: str | None = None,
    ) -> EvaluationResult:
        """Evaluate a single generated Remediation against golden finding constraints."""
        # 1. Strict Schema Validation
        if remediation is None or not isinstance(remediation, Remediation):
            return EvaluationResult(
                iteration=iteration,
                golden_id=golden.id,
                schema_valid=False,
                remediation=None,
                raw_output="",
                concept_valid=False,
                is_pass=False,
                latency_ms=latency_ms,
                error_message=error_message or "Missing or invalid Remediation model",
            )

        if not remediation.summary.strip() or not remediation.explanation.strip():
            return EvaluationResult(
                iteration=iteration,
                golden_id=golden.id,
                schema_valid=False,
                remediation=remediation,
                raw_output="",
                concept_valid=False,
                is_pass=False,
                latency_ms=latency_ms,
                error_message="Empty summary or explanation in Remediation model",
            )

        # 2. Semantic Concept Matching
        text_corpus = f"{remediation.summary} {remediation.explanation}".lower()
        if remediation.code_example:
            text_corpus = f"{text_corpus} {remediation.code_example}".lower()

        concepts_found: list[str] = []
        missing_concepts: list[str] = []

        # Check all required concepts (.lower() on both concept and text_corpus)
        for concept in golden.required_concepts:
            if concept.lower() in text_corpus:
                concepts_found.append(concept)
            else:
                missing_concepts.append(concept)

        # Check required_any_of groups (OR between groups: at least one group must match)
        if golden.required_any_of:
            any_group_matched = False
            for group in golden.required_any_of:
                group_matched = [c for c in group if c.lower() in text_corpus]
                if group_matched:
                    any_group_matched = True
                    concepts_found.extend(group_matched)
            if not any_group_matched:
                group_strs = ["(" + " | ".join(g) + ")" for g in golden.required_any_of]
                missing_concepts.append(f"({' OR '.join(group_strs)})")

        # Check forbidden concepts (.lower() on both forbidden concept and text_corpus)
        forbidden_found = [
            f for f in golden.forbidden_concepts if f.lower() in text_corpus
        ]

        concept_valid = len(missing_concepts) == 0 and len(forbidden_found) == 0
        is_pass = concept_valid

        return EvaluationResult(
            iteration=iteration,
            golden_id=golden.id,
            schema_valid=True,
            remediation=remediation,
            raw_output="",
            concepts_found=concepts_found,
            missing_concepts=missing_concepts,
            forbidden_found=forbidden_found,
            concept_valid=concept_valid,
            is_pass=is_pass,
            latency_ms=latency_ms,
            error_message=error_message,
        )

    @classmethod
    def load_golden_file(cls, file_path: Path) -> GoldenFinding:
        """Load a GoldenFinding from a JSON file."""
        data = json.loads(file_path.read_text(encoding="utf-8"))
        return GoldenFinding.from_dict(data)

    @classmethod
    def load_all_golden(cls, golden_dir: Path) -> list[GoldenFinding]:
        """Load all GoldenFinding JSON files from a directory."""
        return [
            cls.load_golden_file(path) for path in sorted(golden_dir.glob("*.json"))
        ]

    @classmethod
    def aggregate_results(
        cls,
        results: list[EvaluationResult],
        model_name: str,
    ) -> BenchmarkReport:
        """Aggregate per-iteration results into a comprehensive BenchmarkReport."""
        by_golden: dict[str, list[EvaluationResult]] = {}
        for r in results:
            by_golden.setdefault(r.golden_id, []).append(r)

        findings_summary: dict[str, FindingSummaryMetrics] = {}
        all_latencies: list[float] = []

        total_iterations = len(results)
        total_schema_pass = sum(1 for r in results if r.schema_valid)
        total_concept_pass = sum(1 for r in results if r.concept_valid)
        total_overall_pass = sum(1 for r in results if r.is_pass)

        for golden_id, runs in by_golden.items():
            n = len(runs)
            s_pass = sum(1 for r in runs if r.schema_valid)
            c_pass = sum(1 for r in runs if r.concept_valid)
            o_pass = sum(1 for r in runs if r.is_pass)
            s_rate = s_pass / n if n > 0 else 0.0
            c_rate = c_pass / n if n > 0 else 0.0
            o_rate = o_pass / n if n > 0 else 0.0
            mean_lat = sum(r.latency_ms for r in runs) / n if n > 0 else 0.0
            all_latencies.extend([r.latency_ms for r in runs])

            passed_thresholds = (
                s_rate >= cls.SCHEMA_PASS_THRESHOLD
                and c_rate >= cls.CONCEPT_PASS_THRESHOLD
            )

            findings_summary[golden_id] = FindingSummaryMetrics(
                golden_id=golden_id,
                iterations=n,
                schema_pass_count=s_pass,
                schema_pass_rate=round(s_rate, 4),
                concept_pass_count=c_pass,
                concept_pass_rate=round(c_rate, 4),
                overall_pass_count=o_pass,
                overall_pass_rate=round(o_rate, 4),
                mean_latency_ms=round(mean_lat, 2),
                passed_thresholds=passed_thresholds,
            )

        overall_schema_rate = (
            total_schema_pass / total_iterations if total_iterations > 0 else 0.0
        )
        overall_concept_rate = (
            total_concept_pass / total_iterations if total_iterations > 0 else 0.0
        )
        overall_pass_rate = (
            total_overall_pass / total_iterations if total_iterations > 0 else 0.0
        )
        mean_suite_latency = (
            sum(all_latencies) / len(all_latencies) if all_latencies else 0.0
        )

        acceptance_criteria_met = (
            overall_schema_rate >= cls.SCHEMA_PASS_THRESHOLD
            and overall_concept_rate >= cls.CONCEPT_PASS_THRESHOLD
            and all(m.passed_thresholds for m in findings_summary.values())
        )

        return BenchmarkReport(
            timestamp=datetime.now(UTC).isoformat(),
            model_name=model_name,
            total_findings=len(findings_summary),
            total_iterations=total_iterations,
            overall_schema_pass_rate=overall_schema_rate,
            overall_concept_pass_rate=overall_concept_rate,
            overall_pass_rate=overall_pass_rate,
            mean_latency_ms=mean_suite_latency,
            acceptance_criteria_met=acceptance_criteria_met,
            findings_summary=findings_summary,
            iterations=results,
        )
