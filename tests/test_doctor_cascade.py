"""End-to-end integration tests for the full 3-layer Workflow Doctor cascade."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from workflow_clinic.doctor.runner import DoctorRunner
from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.models.fix import FixStrategyLayer

if TYPE_CHECKING:
    from pathlib import Path


def test_full_cascade_layer_assertions(tmp_path: Path) -> None:
    """Verify that DoctorRunner executes the multi-tier cascade using the correct layer for each finding."""
    target_file = tmp_path / "multi_issue.nf"
    initial_code = """process COMPLEX_STEP {
    script:
    \"\"\"
    /data/bin/align --in /data/inputs/sample.fq
    \"\"\"
}"""
    target_file.write_text(initial_code, encoding="utf-8")

    findings = [
        Finding(
            rule_id="W001",
            severity="error",
            message="Process 'COMPLEX_STEP' has no container defined.",
            file_path="multi_issue.nf",
            process_name="COMPLEX_STEP",
        ),
        Finding(
            rule_id="W002",
            severity="warning",
            message="Process 'COMPLEX_STEP' does not declare a CPU resource limit.",
            file_path="multi_issue.nf",
            process_name="COMPLEX_STEP",
        ),
        Finding(
            rule_id="W003",
            severity="warning",
            message="Hardcoded path: '/data/inputs/sample.fq'",
            file_path="multi_issue.nf",
            process_name="COMPLEX_STEP",
        ),
        Finding(
            rule_id="AI001",
            severity="info",
            message="AI suggests refactoring script command to modern syntax.",
            file_path="multi_issue.nf",
            process_name="COMPLEX_STEP",
        ),
    ]

    mock_llm_response = MagicMock()
    mock_llm_response.choices = [
        MagicMock(
            message=MagicMock(
                content="""process COMPLEX_STEP {
    container "quay.io/biocontainers/ubuntu:22.04"
    cpus 1
    script:
    \"\"\"
    params.sample --in params.inputs_sample
    \"\"\"
}"""
            )
        )
    ]

    runner = DoctorRunner()

    with (
        patch(
            "workflow_clinic.doctor.fixers.ai.check_model_api_key",
            return_value=True,
        ),
        patch(
            "workflow_clinic.doctor.fixers.ai.litellm.completion",
            return_value=mock_llm_response,
        ),
    ):
        session = runner.run(findings, root_dir=tmp_path, dry_run=True)

    assert len(session.proposals) == 4

    proposals_by_rule = {p.rule_id: p for p in session.proposals}

    # Verify each finding resolved through the exact expected strategy layer
    assert proposals_by_rule["W001"].strategy_layer == FixStrategyLayer.LAYER1_AST
    assert proposals_by_rule["W002"].strategy_layer == FixStrategyLayer.LAYER1_AST
    assert proposals_by_rule["W003"].strategy_layer == FixStrategyLayer.LAYER2_REGEX
    assert proposals_by_rule["AI001"].strategy_layer == FixStrategyLayer.LAYER3_AI
