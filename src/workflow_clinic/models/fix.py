"""Data models for Workflow Doctor fix proposals, strategy layers, and session lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path  # noqa: TC003
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field

from workflow_clinic.models.diagnosis import Finding  # noqa: TC001


class FixStrategyLayer(IntEnum):
    """Execution priority layers for fixing workflow defects."""

    LAYER1_AST = 1
    LAYER2_REGEX = 2
    LAYER3_AI = 3


class FixProposal(BaseModel):
    """Immutable representation of a proposed code modification for a single finding."""

    model_config = ConfigDict(frozen=True)

    finding_id: str = Field(
        ..., description="SHA-256 hash or unique ID of the target finding"
    )
    rule_id: str = Field(..., description="Diagnostic rule ID (e.g. W001, W002)")
    category: str = Field(
        ..., description="Domain category (containerization, resources, etc.)"
    )
    target_file: str = Field(
        ..., description="Relative path of the workflow file being modified"
    )
    original_snippet: str = Field(..., description="Original code snippet before fix")
    proposed_snippet: str = Field(..., description="Proposed replacement code snippet")
    explanation: str = Field(
        ..., description="Human-readable rationale for the proposed fix"
    )
    strategy_layer: FixStrategyLayer = Field(
        ..., description="Priority layer strategy used to generate proposal"
    )
    line_number: int | None = Field(
        default=None,
        description="Optional 1-based line number of the target code snippet",
    )


class ApplyOutcome(BaseModel):
    """Result details after attempting to apply a FixProposal to disk."""

    success: bool = Field(..., description="True if proposal was applied to disk")
    modified_file: Path | None = Field(
        default=None, description="Path to the modified file if successful"
    )
    failure_reason: str | None = Field(
        default=None, description="Detailed error message if application failed"
    )
    verification_passed: bool = Field(
        default=True, description="True if post-apply verification check passed"
    )


class AppliedProposal(BaseModel):
    """Wrapper tracking a proposal and its application lifecycle result."""

    proposal: FixProposal = Field(..., description="The target fix proposal")
    applied: bool = Field(..., description="True if the fix was applied")
    outcome: ApplyOutcome | None = Field(
        default=None, description="Execution outcome details"
    )


class FixSession(BaseModel):
    """Top-level audit model encapsulating a full Workflow Doctor execution run."""

    session_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique UUID for this doctor fix session",
    )
    source: str = Field(
        ..., description="Input source (diagnosis.json path or GitHub repository URI)"
    )
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp when session started",
    )
    findings_input: list[Finding] = Field(
        default_factory=list, description="List of input findings to fix"
    )
    proposals: list[FixProposal] = Field(
        default_factory=list, description="Generated fix proposals"
    )
    applied_proposals: list[AppliedProposal] = Field(
        default_factory=list, description="Applied fix proposals and outcomes"
    )
    completed_at: datetime | None = Field(
        default=None, description="UTC timestamp when session completed"
    )

    @computed_field
    def applied_count(self) -> int:
        """Total count of successfully applied proposals."""
        return sum(1 for p in self.applied_proposals if p.applied)

    @computed_field
    def failed_count(self) -> int:
        """Total count of proposals that failed to apply."""
        return sum(1 for p in self.applied_proposals if not p.applied)

    @computed_field
    def modified_files(self) -> list[Path]:
        """Unique list of modified file paths on disk."""
        files: set[Path] = set()
        for p in self.applied_proposals:
            if p.outcome and p.outcome.modified_file:
                files.add(p.outcome.modified_file)
        return list(files)
