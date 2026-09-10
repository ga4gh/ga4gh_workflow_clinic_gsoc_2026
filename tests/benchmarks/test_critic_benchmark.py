"""Offline deterministic test suite for AICriticAgent evaluation and golden dataset benchmarks."""

from pathlib import Path

from workflow_clinic.critic import (
    AICriticAgent,
    BenchmarkReport,
    EvaluationResult,
    GoldenFinding,
    RemediationEvaluator,
)
from workflow_clinic.models.diagnosis import Finding, Remediation

GOLDEN_DIR = Path(__file__).parent / "golden"


def test_golden_dataset_integrity() -> None:
    """Verify all curated golden findings load correctly with expected attributes."""
    golden_cases = RemediationEvaluator.load_all_golden(GOLDEN_DIR)

    assert len(golden_cases) >= 8
    rule_ids = {g.finding.rule_id for g in golden_cases}
    assert "W001" in rule_ids
    assert "W002" in rule_ids
    assert "W003" in rule_ids
    assert "W004" in rule_ids
    assert "AI001" in rule_ids

    for golden in golden_cases:
        assert isinstance(golden, GoldenFinding)
        assert golden.id
        assert isinstance(golden.finding, Finding)
        assert len(golden.required_concepts) > 0 or len(golden.required_any_of) > 0
        assert golden.workflow_type in ("nextflow", "wdl", "snakemake")
        # Ensure serialization roundtrip works
        serialized = golden.to_dict()
        reconstructed = GoldenFinding.from_dict(serialized)
        assert reconstructed.id == golden.id
        assert reconstructed.finding.rule_id == golden.finding.rule_id


def test_remediation_evaluator_validates_schema() -> None:
    """Verify evaluator strictly enforces Pydantic Remediation schema validity."""
    sample_golden = RemediationEvaluator.load_golden_file(
        GOLDEN_DIR / "w001_unpinned_tag.json"
    )

    # Case 1: None / non-model fails immediately
    res_none = RemediationEvaluator.evaluate(sample_golden, None)
    assert res_none.schema_valid is False
    assert res_none.is_pass is False

    # Case 2: Empty summary or explanation fails schema check
    empty_remediation = Remediation(summary="", explanation="Some explanation")
    res_empty = RemediationEvaluator.evaluate(sample_golden, empty_remediation)
    assert res_empty.schema_valid is False
    assert res_empty.is_pass is False

    # Case 3: Proper model with required concept passes schema check
    valid_remediation = Remediation(
        summary="Pin container tag using explicit digest",
        explanation="Always pin container image using an immutable sha256 digest or tag.",
        code_example="container 'quay.io/biocontainers/fastqc@sha256:abc...'",
    )
    res_valid = RemediationEvaluator.evaluate(sample_golden, valid_remediation)
    assert res_valid.schema_valid is True
    assert res_valid.concept_valid is True
    assert res_valid.is_pass is True


def test_remediation_evaluator_catches_concept_violations() -> None:
    """Verify evaluator catches missing required concepts and forbidden concepts."""
    sample_golden = RemediationEvaluator.load_golden_file(
        GOLDEN_DIR / "w001_unpinned_tag.json"
    )

    # Missing required concepts
    bad_concepts = Remediation(
        summary="Fix image reference",
        explanation="Make sure the container reference is valid and available on docker hub.",
    )
    res_missing = RemediationEvaluator.evaluate(sample_golden, bad_concepts)
    assert res_missing.schema_valid is True
    assert res_missing.concept_valid is False
    assert res_missing.is_pass is False
    assert len(res_missing.missing_concepts) > 0

    # Forbidden concepts presence (e.g. recommending "use latest")
    forbidden_remediation = Remediation(
        summary="Pin tag to latest",
        explanation="You can use latest tag or sha256 to pin the container image.",
    )
    res_forbidden = RemediationEvaluator.evaluate(sample_golden, forbidden_remediation)
    assert res_forbidden.schema_valid is True
    assert res_forbidden.concept_valid is False
    assert "use latest" in res_forbidden.forbidden_found
    assert res_forbidden.is_pass is False


def test_remediation_evaluator_required_any_of_group() -> None:
    """Verify evaluator requires at least one token from required_any_of groups using OR logic."""
    sample_golden = RemediationEvaluator.load_golden_file(
        GOLDEN_DIR / "w003_hardcoded_path.json"
    )

    # Contains "path" and "params" -> passes
    passing_remediation = Remediation(
        summary="Replace absolute path with params.input_file",
        explanation="Use workflow params to supply file path dynamically.",
    )
    res_pass = RemediationEvaluator.evaluate(sample_golden, passing_remediation)
    assert res_pass.is_pass is True

    # Contains "path" but none of the required alternative tokens -> fails
    failing_remediation = Remediation(
        summary="Do not use this path",
        explanation="The filesystem location should be changed.",
    )
    res_fail = RemediationEvaluator.evaluate(sample_golden, failing_remediation)
    assert res_fail.concept_valid is False
    assert res_fail.is_pass is False

    # Multi-group OR logic: matching group 1 OR group 2 passes
    w004_golden = RemediationEvaluator.load_golden_file(
        GOLDEN_DIR / "w004_credential.json"
    )
    # Only matches group 1 ("secret"), does not match group 2 -> passes with OR
    res_or = RemediationEvaluator.evaluate(
        w004_golden,
        Remediation(
            summary="Do not hardcode secrets",
            explanation="The secret should not be stored in workflow files.",
        ),
    )
    assert res_or.concept_valid is True
    assert res_or.is_pass is True


def test_remediation_evaluator_metric_aggregation() -> None:
    """Verify aggregation computes correct pass rates, mean latency, and acceptance thresholds."""
    results: list[EvaluationResult] = [
        EvaluationResult(
            iteration=i,
            golden_id="test_finding",
            schema_valid=True,
            concept_valid=True,
            is_pass=True,
            latency_ms=100.0,
        )
        for i in range(1, 10)
    ]
    results.append(
        EvaluationResult(
            iteration=10,
            golden_id="test_finding",
            schema_valid=False,
            concept_valid=False,
            is_pass=False,
            latency_ms=150.0,
        )
    )

    report = RemediationEvaluator.aggregate_results(results, model_name="mock-model")

    assert isinstance(report, BenchmarkReport)
    assert report.total_iterations == 10
    assert report.overall_schema_pass_rate == 0.90
    assert report.overall_concept_pass_rate == 0.90
    assert report.overall_pass_rate == 0.90
    assert report.acceptance_criteria_met is True
    assert report.findings_summary["test_finding"].passed_thresholds is True

    # Serialized report verification
    rep_dict = report.to_dict()
    assert rep_dict["total_iterations"] == 10
    assert rep_dict["overall_schema_pass_rate"] == 0.90
    assert "test_finding" in rep_dict["findings_summary"]


def test_offline_knowledge_store_evaluates_against_golden_dataset() -> None:
    """Verify offline Knowledge Store fallback achieves 100% schema pass rate on all golden findings."""
    agent = AICriticAgent(enable_llm=False)
    golden_cases = RemediationEvaluator.load_all_golden(GOLDEN_DIR)

    results: list[EvaluationResult] = []
    for golden in golden_cases:
        remediation, is_fallback = agent.enhance_finding(golden.finding)
        assert is_fallback is True
        assert isinstance(remediation, Remediation)

        eval_res = RemediationEvaluator.evaluate(
            golden, remediation, iteration=1, latency_ms=5.0
        )
        assert eval_res.schema_valid is True
        results.append(eval_res)

    report = RemediationEvaluator.aggregate_results(results, model_name="offline-kb")
    assert report.total_findings == len(golden_cases)
    assert report.overall_schema_pass_rate == 1.0
