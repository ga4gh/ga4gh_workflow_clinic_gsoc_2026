"""Pydantic data models for diagnosis reports, findings, and remediations."""

from typing import Literal

from pydantic import BaseModel, Field


class Fingerprint(BaseModel):
    """Container for SHA-256 fingerprint hash for finding deduplication."""

    hash: str = Field(
        ..., description="SHA-256 hash of (file_path:line_number:rule_id)"
    )


class Remediation(BaseModel):
    """Structured remediation instructions for resolving a diagnostic finding."""

    summary: str = Field(..., description="Short summary of recommended fix")
    explanation: str = Field(
        ..., description="Detailed explanation of why fix is required"
    )
    code_example: str | None = Field(
        default=None, description="Optional code snippet demonstrating the fix"
    )


class Finding(BaseModel):
    """Formal model representing a single workflow diagnostic finding."""

    id: str = Field(..., description="Unique finding ID or SHA-256 fingerprint hash")
    rule_id: str = Field(..., description="Rule identifier (e.g. W001, W002)")
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(
        ..., description="Severity level of the finding"
    )
    category: str = Field(
        ..., description="Finding category (e.g. containerization, resources, security)"
    )
    title: str = Field(..., description="Short title describing the issue")
    file_path: str = Field(..., description="Relative or absolute path to target file")
    line_number: int | None = Field(
        default=None, description="Optional line number where issue was detected"
    )
    fingerprint: Fingerprint | None = Field(
        default=None, description="Optional SHA-256 fingerprint container"
    )
    remediation: Remediation | None = Field(
        default=None, description="Optional remediation guidance"
    )


class DiagnosisReport(BaseModel):
    """Top-level diagnostic report model for serialization to diagnosis.json."""

    workflow_name: str = Field(..., description="Name of examined workflow")
    tasks_count: int = Field(default=0, description="Total tasks parsed")
    findings_count: int = Field(default=0, description="Total findings detected")
    findings: list[Finding] = Field(
        default_factory=list, description="List of diagnostic findings"
    )
