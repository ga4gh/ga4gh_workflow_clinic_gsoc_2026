"""Core base class and registry definitions for Workflow Doctor fixers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar

from workflow_clinic.models.fix import (
    ApplyOutcome,
    FixProposal,
    FixStrategyLayer,
)

if TYPE_CHECKING:
    from pathlib import Path

    from workflow_clinic.models.diagnosis import Finding
    from workflow_clinic.models.workflow_bundle import WorkflowBundle

logger = logging.getLogger(__name__)


class BaseFixer(ABC):
    """Abstract base class for all Workflow Doctor rule-specific fixers."""

    rule_id: ClassVar[str]
    strategy_layer: ClassVar[FixStrategyLayer]

    def can_fix(self, finding: Finding) -> bool:
        """Determine if this fixer can handle the given finding.

        Args:
            finding: Target diagnostic finding.

        Returns:
            True if rule_id matches this fixer's rule_id.
        """
        return finding.rule_id == self.rule_id

    @abstractmethod
    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,
        source_code: str | None = None,
    ) -> FixProposal | None:
        """Generate a proposed code modification for a finding.

        Args:
            finding: The diagnostic finding to fix.
            bundle: Optional parsed WorkflowBundle context.

        Returns:
            FixProposal if a fix can be constructed, None otherwise.
        """

    def verify_fix(self, _modified_file: Path) -> bool:
        """Perform post-apply validation on the modified workflow file.

        Args:
            _modified_file: Absolute path to the modified file on disk.

        Returns:
            True if verification passed, False otherwise. Defaults to True.
        """
        return True

    def apply_fix(self, proposal: FixProposal, root_dir: Path) -> ApplyOutcome:
        """Apply a FixProposal directly to a target file on disk.

        Args:
            proposal: The proposal to apply.
            root_dir: Root working directory containing target workflow files.

        Returns:
            ApplyOutcome describing success status and failure reasons.
        """
        target_path = (root_dir / proposal.target_file).resolve()
        if not target_path.exists():
            return ApplyOutcome(
                success=False,
                failure_reason=f"Target file '{proposal.target_file}' does not exist on disk.",
                verification_passed=False,
            )

        try:
            content = target_path.read_text(encoding="utf-8")
            if proposal.original_snippet not in content:
                return ApplyOutcome(
                    success=False,
                    failure_reason=(
                        f"Original snippet not found in '{proposal.target_file}'. "
                        "The file may have been modified concurrently."
                    ),
                    verification_passed=False,
                )

            if proposal.line_number and proposal.line_number > 0:
                occurrences: list[int] = []
                start = 0
                while True:
                    idx = content.find(proposal.original_snippet, start)
                    if idx == -1:
                        break
                    occurrences.append(idx)
                    start = idx + 1

                if occurrences:
                    best_idx = min(
                        occurrences,
                        key=lambda pos: abs(
                            content[:pos].count("\n") + 1 - proposal.line_number  # type: ignore[operator]
                        ),
                    )
                    new_content = (
                        content[:best_idx]
                        + proposal.proposed_snippet
                        + content[best_idx + len(proposal.original_snippet) :]
                    )
                else:
                    new_content = content.replace(
                        proposal.original_snippet, proposal.proposed_snippet, 1
                    )
            else:
                new_content = content.replace(
                    proposal.original_snippet, proposal.proposed_snippet, 1
                )

            target_path.write_text(new_content, encoding="utf-8")

            verified = self.verify_fix(target_path)
            if not verified:
                # Revert change if verification failed
                target_path.write_text(content, encoding="utf-8")
                return ApplyOutcome(
                    success=False,
                    modified_file=target_path,
                    failure_reason=f"Post-apply verification failed for '{proposal.target_file}'. Reverted.",
                    verification_passed=False,
                )

            return ApplyOutcome(
                success=True,
                modified_file=target_path,
                verification_passed=True,
            )
        except (OSError, UnicodeDecodeError) as e:
            logger.exception("Failed to apply fix proposal to %s", target_path)
            return ApplyOutcome(
                success=False,
                failure_reason=f"IO error while applying fix: {e}",
                verification_passed=False,
            )


class FixerRegistry:
    """Registry managing available Workflow Doctor rule fixers and cascade chains."""

    _fixers: ClassVar[dict[tuple[str, FixStrategyLayer], BaseFixer]] = {}

    @classmethod
    def register(cls, fixer_cls: type[BaseFixer]) -> type[BaseFixer]:
        """Decorator to register a BaseFixer subclass in the global registry.

        Args:
            fixer_cls: Subclass of BaseFixer to instantiate and register.

        Returns:
            The registered class unchanged.

        Raises:
            ValueError: If a fixer for the same rule_id and strategy_layer is already registered.
        """
        key = (fixer_cls.rule_id, fixer_cls.strategy_layer)
        if key in cls._fixers:
            msg = (
                f"Duplicate fixer registration for rule '{fixer_cls.rule_id}' "
                f"at layer '{fixer_cls.strategy_layer.name}'."
            )
            raise ValueError(msg)

        cls._fixers[key] = fixer_cls()
        logger.debug(
            "Registered fixer %s for rule %s at layer %s",
            fixer_cls.__name__,
            fixer_cls.rule_id,
            fixer_cls.strategy_layer.name,
        )
        return fixer_cls

    @classmethod
    def has_fixer(cls, rule_id: str) -> bool:
        """Check if any fixer is registered for a given rule_id.

        Args:
            rule_id: Rule ID to check (e.g. 'W001').

        Returns:
            True if at least one fixer is registered for rule_id.
        """
        return any(r_id == rule_id for (r_id, _) in cls._fixers)

    @classmethod
    def get_fixer_chain(cls, rule_id: str) -> list[BaseFixer]:
        """Retrieve all registered fixers for a rule_id ordered by strategy layer.

        Args:
            rule_id: Rule ID to look up (e.g. 'W001').

        Returns:
            List of BaseFixer instances sorted by strategy_layer ascending (LAYER1_AST -> LAYER2_REGEX -> LAYER3_AI).
        """
        matching = [
            fixer for (r_id, _), fixer in cls._fixers.items() if r_id == rule_id
        ]
        return sorted(matching, key=lambda f: f.strategy_layer)

    @classmethod
    def get_all_fixers(cls) -> list[BaseFixer]:
        """Get all registered fixer instances."""
        return list(cls._fixers.values())

    @classmethod
    def clear(cls) -> None:
        """Clear all registered fixers (primarily used for test isolation)."""
        cls._fixers.clear()
