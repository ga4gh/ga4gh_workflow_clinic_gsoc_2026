"""Rule engine public API for Workflow Clinic.

This package provides the validation rule infrastructure. Import from
here rather than from submodules directly.
"""

from workflow_clinic.rules.base import BaseRule, Finding, Severity
from workflow_clinic.rules.container import PinnedContainerRule
from workflow_clinic.rules.registry import RuleRegistry
from workflow_clinic.rules.resources import ResourceLimitsRule
from workflow_clinic.rules.runner import RuleRunner

# Register built-in rules
RuleRegistry.register(PinnedContainerRule)
RuleRegistry.register(ResourceLimitsRule)

__all__ = [
    "BaseRule",
    "Finding",
    "PinnedContainerRule",
    "ResourceLimitsRule",
    "RuleRegistry",
    "RuleRunner",
    "Severity",
]
