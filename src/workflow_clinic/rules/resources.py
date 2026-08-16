"""Resource limits validation rule.

Checks that every task in a WorkflowBundle declares CPU and memory
resource limits. Missing resource declarations are flagged because
they prevent cloud schedulers from allocating appropriate capacity.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from workflow_clinic.models import WorkflowBundle
from workflow_clinic.rules.base import BaseRule, Finding, Severity


class ResourceLimitsRule(BaseRule):
    """Flag tasks missing CPU or memory resource declarations (W002).

    Tasks that omit ``cpus`` or ``memory`` in their resource block
    cannot be properly scheduled by cloud executors (e.g. AWS Batch,
    Google Life Sciences, Azure Batch) and risk over- or under-allocation.
    """

    id = "W002"
    name = "Resource Limits"
    description = "Checks that all tasks declare CPU and memory resource limits."

    def check(self, bundle: WorkflowBundle) -> list[Finding]:
        """Validate resource declarations across all tasks."""
        findings: list[Finding] = []

        for task in bundle.tasks:
            if task.resources.cpus is None:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        message=(
                            f"Process '{task.name}' does not declare a CPU "
                            f"resource limit."
                        ),
                        severity=Severity.WARNING,
                        task_id=task.id,
                        process_name=task.name,
                        file_path=task.file_path,
                        line_number=task.line_number,
                    )
                )
            elif task.resources.cpus == 1:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        message=(
                            f"Process '{task.name}' specifies a single CPU core. "
                            f"Verify if multi-threading is supported by this tool "
                            f"to optimize execution."
                        ),
                        severity=Severity.INFO,
                        task_id=task.id,
                        process_name=task.name,
                        file_path=task.file_path,
                        line_number=task.line_number,
                    )
                )

            if task.resources.memory is None:
                findings.append(
                    Finding(
                        rule_id=self.id,
                        message=(
                            f"Process '{task.name}' does not declare a memory "
                            f"resource limit."
                        ),
                        severity=Severity.WARNING,
                        task_id=task.id,
                        process_name=task.name,
                        file_path=task.file_path,
                        line_number=task.line_number,
                    )
                )

        return findings
