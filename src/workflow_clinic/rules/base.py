"""Core rule engine abstractions for Workflow Clinic.

This module defines the foundational types for the rule engine:
``Severity`` enum, ``Finding`` data model, and ``BaseRule`` abstract class.
All concrete validation rules inherit from ``BaseRule``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from workflow_clinic.models import WorkflowBundle


class Severity(StrEnum):
    """Severity level for a diagnostic finding.

    Attributes:
        INFO: Observation or style recommendation — does not indicate a problem.
        WARNING: Non-blocking portability or best-practice issue.
        ERROR: Blocking issue that must be resolved before deployment.
    """

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Finding(BaseModel):
    """A single diagnostic finding produced by a rule.

    Attributes:
        rule_id: Unique identifier of the rule that generated this finding.
        message: Human-readable description of the issue.
        severity: Severity level of the finding.
        task_id: ID of the task where the issue was found, if applicable.
        location: Optional location hint (e.g. process name or directive).
    """

    rule_id: str
    message: str
    severity: Severity
    task_id: str | None = None
    process_name: str | None = None
    file_path: str = ""
    line_number: int | None = None


class BaseRule(ABC):
    """Abstract base class for all validation rules.

    Every concrete rule must define ``id``, ``name``, ``description``
    class attributes and implement the ``check`` method.
    """

    id: str
    name: str
    description: str

    @abstractmethod
    def check(self, bundle: WorkflowBundle) -> list[Finding]:
        """Run this rule against a WorkflowBundle and return findings.

        Args:
            bundle: The parsed workflow representation to validate.

        Returns:
            A list of Finding objects. An empty list means the workflow
            passed this rule with no issues.
        """
