"""Pydantic data models for diagnosis reports, findings, and remediations."""

from typing import Any

from pydantic import BaseModel, Field, model_validator


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

    id: str = Field(
        default="", description="Unique finding ID or SHA-256 fingerprint hash"
    )
    rule_id: str = Field(..., description="Rule identifier (e.g. W001, W002)")
    severity: str = Field(..., description="Severity level of the finding")
    category: str = Field(
        default="",
        description="Finding category (e.g. containerization, resources, security)",
    )
    title: str = Field(default="", description="Short title describing the issue")
    file_path: str = Field(
        default="", description="Relative or absolute path to target file"
    )
    line_number: int | None = Field(
        default=None, description="Optional line number where issue was detected"
    )
    process_name: str | None = Field(
        default=None, description="Optional location hint from examine output"
    )
    message: str | None = Field(
        default=None, description="Human-readable description of the issue"
    )
    fingerprint: Fingerprint | None = Field(
        default=None, description="Optional SHA-256 fingerprint container"
    )
    remediation: Remediation | None = Field(
        default=None, description="Optional remediation guidance"
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Normalize severity to upper-case string
            if "severity" in data and isinstance(data["severity"], str):
                sev = data["severity"].upper()
                valid_severities = {
                    "CRITICAL",
                    "HIGH",
                    "MEDIUM",
                    "LOW",
                    "ERROR",
                    "WARNING",
                    "INFO",
                }
                if sev not in valid_severities:
                    msg = f"Invalid severity level: {data['severity']}"
                    raise ValueError(msg)
                data["severity"] = sev

            # Normalize location to file_path
            if not data.get("file_path"):
                data["file_path"] = data.get("process_name") or "main.nf"

            # Normalize message to title
            if not data.get("title"):
                data["title"] = (
                    data.get("message") or data.get("rule_id") or "Diagnostic Finding"
                )

            # Map category from rule_id if omitted
            if not data.get("category") and data.get("rule_id"):
                rule_map = {
                    "W001": "containerization",
                    "W002": "resources",
                    "W003": "portability",
                    "W004": "security",
                }
                data["category"] = rule_map.get(str(data["rule_id"]), "portability")

            # Extract id from fingerprint if omitted
            if not data.get("id"):
                fp = data.get("fingerprint")
                if isinstance(fp, dict) and fp.get("hash"):
                    data["id"] = fp["hash"]
                else:
                    data["id"] = "finding_hash"

        return data


class DiagnosisReport(BaseModel):
    """Top-level diagnostic report model for serialization to diagnosis.json."""

    workflow_name: str = Field(..., description="Name of examined workflow")
    tasks_count: int = Field(default=0, description="Total tasks parsed")
    findings_count: int = Field(default=0, description="Total findings detected")
    findings: list[Finding] = Field(
        default_factory=list, description="List of diagnostic findings"
    )
