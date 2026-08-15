"""Unit tests for Workflow Doctor data models (FixProposal, FixResult, FixSession, etc.)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from workflow_clinic.models.diagnosis import Finding, Fingerprint, Remediation
from workflow_clinic.models.fix import (
    AppliedProposal,
    ApplyOutcome,
    FixProposal,
    FixSession,
    FixStrategyLayer,
)


def test_fix_strategy_layer_ordering() -> None:
    """Verify numerical priority ordering of strategy layers (LAYER1_AST < LAYER2_REGEX < LAYER3_AI)."""
    assert FixStrategyLayer.LAYER1_AST < FixStrategyLayer.LAYER2_REGEX
    assert FixStrategyLayer.LAYER2_REGEX < FixStrategyLayer.LAYER3_AI
    assert FixStrategyLayer.LAYER1_AST < FixStrategyLayer.LAYER3_AI
    assert FixStrategyLayer.LAYER1_AST == 1
    assert FixStrategyLayer.LAYER2_REGEX == 2
    assert FixStrategyLayer.LAYER3_AI == 3


def test_fix_proposal_creation_and_immutability() -> None:
    """Verify FixProposal creation, field attributes, and frozen immutability."""
    prop = FixProposal(
        finding_id="hash123",
        rule_id="W001",
        category="containerization",
        target_file="main.nf",
        original_snippet="process FASTQC {",
        proposed_snippet="process FASTQC {\n    container 'quay.io/biocontainers/fastqc:0.11.9'",
        explanation="Added container directive for process FASTQC",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )

    assert prop.finding_id == "hash123"
    assert prop.rule_id == "W001"
    assert prop.strategy_layer == FixStrategyLayer.LAYER1_AST

    # Verify frozen immutability raises ValidationError on mutation
    with pytest.raises(ValidationError):
        prop.original_snippet = "modified"  # type: ignore[misc]


def test_apply_outcome_and_applied_proposal() -> None:
    """Verify ApplyOutcome and AppliedProposal lifecycle models."""
    prop = FixProposal(
        finding_id="hash123",
        rule_id="W001",
        category="containerization",
        target_file="main.nf",
        original_snippet="process FASTQC {",
        proposed_snippet="process FASTQC {\n    container 'biocontainers/fastqc'",
        explanation="Fix container",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )

    outcome = ApplyOutcome(
        success=True,
        failure_reason=None,
        verification_passed=True,
    )

    applied = AppliedProposal(
        proposal=prop,
        applied=True,
        outcome=outcome,
    )

    assert applied.applied is True
    assert applied.outcome is not None
    assert applied.outcome.success is True


def test_fix_session_lifecycle_and_json_roundtrip() -> None:
    """Verify FixSession creation, UUID generation, findings tracking, and JSON roundtrip."""
    finding = Finding(
        id="hash_abc",
        rule_id="W002",
        severity="HIGH",
        category="resources",
        title="Missing cpus directive",
        file_path="modules/align.nf",
        line_number=15,
        remediation=Remediation(
            summary="Add cpus 4 directive",
            explanation="Processes should define explicit resource limits.",
        ),
        fingerprint=Fingerprint(hash="hash_abc"),
    )

    prop = FixProposal(
        finding_id="hash_abc",
        rule_id="W002",
        category="resources",
        target_file="modules/align.nf",
        original_snippet="process ALIGN {",
        proposed_snippet="process ALIGN {\n    cpus 4",
        explanation="Add cpus directive",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )
    outcome = ApplyOutcome(
        success=True,
        modified_file=Path("modules/align.nf"),
        verification_passed=True,
    )
    applied = AppliedProposal(proposal=prop, applied=True, outcome=outcome)

    session = FixSession(
        source="/path/to/diagnosis.json",
        findings_input=[finding],
        proposals=[prop],
        applied_proposals=[applied],
    )

    assert session.session_id is not None
    assert len(session.session_id) > 10
    assert len(session.findings_input) == 1
    assert session.findings_input[0].rule_id == "W002"
    assert session.applied_count == 1
    assert session.failed_count == 0
    assert len(session.modified_files) == 1

    json_data = session.model_dump_json()
    reconstructed = FixSession.model_validate_json(json_data)

    assert reconstructed.session_id == session.session_id
    assert reconstructed.source == session.source
    assert len(reconstructed.findings_input) == 1
    assert reconstructed.findings_input[0].id == "hash_abc"
    assert reconstructed.applied_count == 1


def test_fix_session_computed_counts() -> None:
    """Verify applied_count, failed_count, and modified_files computed properties."""
    prop1 = FixProposal(
        finding_id="h1",
        rule_id="W001",
        category="containerization",
        target_file="file1.nf",
        original_snippet="a",
        proposed_snippet="b",
        explanation="fix 1",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )
    prop2 = FixProposal(
        finding_id="h2",
        rule_id="W002",
        category="resources",
        target_file="file2.nf",
        original_snippet="c",
        proposed_snippet="d",
        explanation="fix 2",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )

    app1 = AppliedProposal(
        proposal=prop1,
        applied=True,
        outcome=ApplyOutcome(success=True, modified_file=Path("file1.nf")),
    )
    app2 = AppliedProposal(
        proposal=prop2,
        applied=False,
        outcome=ApplyOutcome(success=False, failure_reason="failed"),
    )

    session = FixSession(
        source="diagnosis.json",
        proposals=[prop1, prop2],
        applied_proposals=[app1, app2],
    )

    assert session.applied_count == 1
    assert session.failed_count == 1
    assert session.modified_files == [Path("file1.nf")]


def test_fix_session_unique_session_ids() -> None:
    """Verify two FixSession instances get unique UUIDs."""
    s1 = FixSession(source="a")
    s2 = FixSession(source="b")
    assert s1.session_id != s2.session_id


def test_apply_outcome_failure_has_no_modified_file() -> None:
    """Verify failed ApplyOutcome defaults modified_file to None."""
    outcome = ApplyOutcome(
        success=False, failure_reason="not found", verification_passed=False
    )
    assert outcome.modified_file is None
