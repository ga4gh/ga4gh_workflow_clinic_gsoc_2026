"""DoctorRunner orchestrator for cascading fix resolution."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import workflow_clinic.doctor.fixers  # noqa: F401
from workflow_clinic.doctor.base import FixerRegistry
from workflow_clinic.models.fix import AppliedProposal, FixSession

if TYPE_CHECKING:
    from pathlib import Path

    from workflow_clinic.models.diagnosis import Finding
    from workflow_clinic.models.workflow_bundle import WorkflowBundle

logger = logging.getLogger(__name__)


class DoctorRunner:
    """Orchestrator executing the multi-layer Workflow Doctor fix resolution cascade."""

    def run(
        self,
        findings: list[Finding],
        root_dir: Path,
        bundle: WorkflowBundle | None = None,
        dry_run: bool = False,  # noqa: FBT001, FBT002
    ) -> FixSession:
        """Run the Workflow Doctor cascade across target findings.

        Args:
            findings: List of diagnostic findings to resolve.
            root_dir: Root workflow workspace directory containing target files.
            bundle: Optional pre-parsed WorkflowBundle context.
            dry_run: If True, generate proposals without modifying disk.

        Returns:
            FixSession audit object tracking proposals and execution outcomes.
        """
        session = FixSession(
            source=str(root_dir),
            findings_input=findings,
        )

        for finding in findings:
            chain = FixerRegistry.get_fixer_chain(finding.rule_id)
            if not chain:
                logger.debug("No registered fixers found for rule %s", finding.rule_id)
                continue

            for fixer in chain:
                if not fixer.can_fix(finding):
                    continue

                proposal = fixer.generate_proposal(finding, bundle=bundle)
                if proposal is None:
                    continue

                session.proposals.append(proposal)

                if dry_run:
                    # Stop after first valid proposal in cascade during dry-run
                    break

                outcome = fixer.apply_fix(proposal, root_dir=root_dir)
                session.applied_proposals.append(
                    AppliedProposal(
                        proposal=proposal,
                        applied=outcome.success,
                        outcome=outcome,
                    )
                )

                if outcome.success:
                    # Cascade stops on first successful fix application for this finding
                    break

        session.completed_at = datetime.now(UTC)
        return session
