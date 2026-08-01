"""Unit tests for diagnosis Pydantic data models."""

import json

import pytest
from pydantic import ValidationError

from workflow_clinic.models.diagnosis import (
    DiagnosisReport,
    Finding,
    Fingerprint,
    Remediation,
)


def test_fingerprint_creation() -> None:
    """Verify Fingerprint model initializes hash string correctly."""
    fp = Fingerprint(hash="abc123sha256hash")
    assert fp.hash == "abc123sha256hash"


def test_remediation_creation() -> None:
    """Verify Remediation model stores summary, explanation, and code examples."""
    remediation = Remediation(
        summary="Pin container tag",
        explanation="Unpinned container tags introduce non-reproducible runtime environments.",
        code_example="container 'biocontainers/fastqc:v0.11.9'",
    )
    assert remediation.summary == "Pin container tag"
    assert remediation.code_example == "container 'biocontainers/fastqc:v0.11.9'"


def test_finding_creation_and_validation() -> None:
    """Verify Finding model validates fields and accepts optional submodels."""
    finding = Finding(
        id="hash_123",
        rule_id="W001",
        severity="CRITICAL",
        category="containerization",
        title="Unpinned container tag",
        file_path="modules/fastqc.nf",
        line_number=12,
        fingerprint=Fingerprint(hash="hash_123"),
        remediation=Remediation(
            summary="Pin container tag",
            explanation="Use specific version tag",
        ),
    )
    assert finding.id == "hash_123"
    assert finding.severity == "CRITICAL"
    assert finding.line_number == 12
    assert finding.fingerprint is not None
    assert finding.fingerprint.hash == "hash_123"


def test_invalid_severity_raises_validation_error() -> None:
    """Verify passing invalid severity level raises Pydantic ValidationError."""
    with pytest.raises(ValidationError):
        Finding(
            id="hash_123",
            rule_id="W001",
            severity="INVALID_SEVERITY",  # type: ignore[arg-type]
            category="containerization",
            title="Test issue",
            file_path="main.nf",
        )


def test_diagnosis_report_json_roundtrip() -> None:
    """Verify DiagnosisReport serializes to JSON and deserializes back perfectly."""
    finding = Finding(
        id="sha_456",
        rule_id="W002",
        severity="HIGH",
        category="resources",
        title="Missing memory limit",
        file_path="processes/align.nf",
    )
    report = DiagnosisReport(
        workflow_name="rnaseq",
        tasks_count=5,
        findings_count=1,
        findings=[finding],
    )

    json_str = report.model_dump_json(indent=2)
    assert "rnaseq" in json_str
    assert "W002" in json_str

    # Deserialize back from JSON
    data = json.loads(json_str)
    deserialized = DiagnosisReport.model_validate(data)
    assert deserialized.workflow_name == "rnaseq"
    assert len(deserialized.findings) == 1
    assert deserialized.findings[0].rule_id == "W002"
    assert deserialized.findings[0].severity == "HIGH"
