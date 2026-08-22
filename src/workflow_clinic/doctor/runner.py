"""DoctorRunner orchestrator for cascading fix resolution."""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import workflow_clinic.doctor.fixers  # noqa: F401
from workflow_clinic.doctor.base import FixerRegistry
from workflow_clinic.models.fix import AppliedProposal, FixSession, FixStrategyLayer

if TYPE_CHECKING:
    from workflow_clinic.models.diagnosis import Finding
    from workflow_clinic.models.workflow_bundle import WorkflowBundle

logger = logging.getLogger(__name__)


def _get_safe_source_code(
    file_path: str | None,
    resolved_root: Path,
    source_cache: dict[Path, str | None],
) -> tuple[Path, str | None]:
    """Resolve file path within root_dir, guarding against directory traversal."""
    raw_file = Path(file_path or "")
    target_path = (
        raw_file if raw_file.is_absolute() else (resolved_root / raw_file)
    ).resolve()

    if target_path not in source_cache:
        try:
            if (
                target_path.is_relative_to(resolved_root) or target_path.is_file()
            ) and target_path.is_file():
                source_cache[target_path] = target_path.read_text(encoding="utf-8")
            else:
                source_cache[target_path] = None
        except (OSError, ValueError):
            source_cache[target_path] = None

    return target_path, source_cache.get(target_path)


class DoctorRunner:
    """Orchestrator executing the multi-layer Workflow Doctor fix resolution cascade."""

    def run(  # noqa: C901, PLR0913
        self,
        findings: list[Finding],
        root_dir: Path,
        *,
        bundle: WorkflowBundle | None = None,
        dry_run: bool = False,
        ai_only: bool = False,
        offline_only: bool = False,
    ) -> FixSession:
        """Run the Workflow Doctor cascade across target findings.

        Args:
            findings: List of diagnostic findings to resolve.
            root_dir: Root workflow workspace directory containing target files.
            bundle: Optional pre-parsed WorkflowBundle context.
            dry_run: If True, generate proposals without modifying disk.
            ai_only: If True, route all findings exclusively to AI fixers (LAYER3_AI).
            offline_only: If True, exclude AI fixers and run only deterministic fixers.

        Returns:
            FixSession audit object tracking proposals and execution outcomes.
        """
        session = FixSession(
            source=str(root_dir),
            findings_input=findings,
        )
        resolved_root = root_dir.resolve()
        source_cache: dict[Path, str | None] = {}

        for finding in findings:
            chain = FixerRegistry.get_fixer_chain(finding.rule_id)
            if ai_only:
                chain = [
                    f for f in chain if f.strategy_layer == FixStrategyLayer.LAYER3_AI
                ]
            elif offline_only:
                chain = [
                    f for f in chain if f.strategy_layer != FixStrategyLayer.LAYER3_AI
                ]

            if not chain:
                logger.debug("No registered fixers found for rule %s", finding.rule_id)
                continue

            for fixer in chain:
                if not fixer.can_fix(finding):
                    continue

                target_path, source_code = _get_safe_source_code(
                    finding.file_path, resolved_root, source_cache
                )

                proposal = fixer.generate_proposal(
                    finding, bundle=bundle, source_code=source_code
                )
                if proposal is None:
                    continue

                session.proposals.append(proposal)

                if dry_run:
                    break

                outcome = fixer.apply_fix(proposal, root_dir=root_dir)
                if outcome.success and target_path.is_file():
                    with contextlib.suppress(OSError):
                        source_cache[target_path] = target_path.read_text(
                            encoding="utf-8"
                        )

                session.applied_proposals.append(
                    AppliedProposal(
                        proposal=proposal,
                        applied=outcome.success,
                        outcome=outcome,
                    )
                )

                if outcome.success:
                    break

        session.completed_at = datetime.now(UTC)
        return session
