"""Rule runner for Workflow Clinic.

This module provides the ``RuleRunner`` that executes registered
validation rules against a ``WorkflowBundle`` and collects findings.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow_clinic.models import WorkflowBundle
    from workflow_clinic.rules.base import Finding
from workflow_clinic.rules.registry import RuleRegistry

logger = logging.getLogger(__name__)


class RuleRunner:
    """Executes validation rules against a WorkflowBundle.

    By default, runs all rules registered in the ``RuleRegistry``.
    An optional ``rule_ids`` filter can restrict execution to a subset.
    """

    def run(
        self,
        bundle: WorkflowBundle,
        rule_ids: list[str] | None = None,
    ) -> list[Finding]:
        """Run rules against a bundle and return all findings.

        Args:
            bundle: The parsed workflow to validate.
            rule_ids: Optional list of rule ids to run. If None, all
                registered rules are executed.

        Returns:
            A combined list of findings from all executed rules.
        """
        if rule_ids is not None:
            rules = [RuleRegistry.get_rule(rid) for rid in rule_ids]
        else:
            rules = RuleRegistry.get_all_rules()

        findings: list[Finding] = []
        for rule in rules:
            logger.info("Running rule: %s (%s)", rule.id, rule.name)
            rule_findings = rule.check(bundle)
            findings.extend(rule_findings)
            logger.info("Rule %s produced %d finding(s)", rule.id, len(rule_findings))

        return findings
