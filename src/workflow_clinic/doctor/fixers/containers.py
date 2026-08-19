"""Layer 1 AST Fixer for W001 (Missing Container Directives & Unpinned Image Tags)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from workflow_clinic.doctor.base import BaseFixer, FixerRegistry
from workflow_clinic.doctor.patcher import inject_directive, replace_directive_text
from workflow_clinic.models.fix import FixProposal, FixStrategyLayer

if TYPE_CHECKING:
    from workflow_clinic.models.diagnosis import Finding
    from workflow_clinic.models.workflow_bundle import WorkflowBundle


DEFAULT_CONTAINER = "quay.io/biocontainers/ubuntu:22.04"
DEFAULT_PINNED_TAG = "22.04"


@FixerRegistry.register
class ContainerASTFixer(BaseFixer):
    """Layer 1 AST Fixer for Rule W001 (Containers)."""

    rule_id = "W001"
    strategy_layer = FixStrategyLayer.LAYER1_AST
    title = "Fix Missing or Unpinned Container Directive"

    def can_fix(self, finding: Finding) -> bool:
        """Determine if this fixer can remediate the given W001 finding.

        Args:
            finding: Diagnostic finding instance.

        Returns:
            True if rule_id matches W001.
        """
        return finding.rule_id == "W001"

    def generate_proposal(  # noqa: C901, PLR0912
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,
        source_code: str = "",
    ) -> FixProposal | None:
        """Generate a FixProposal for missing or unpinned container directives.

        Args:
            finding: Diagnostic finding instance for W001.
            bundle: Optional parsed WorkflowBundle context.
            source_code: Optional source code string.

        Returns:
            FixProposal or None if finding cannot be parsed.
        """
        if not self.can_fix(finding):
            return None

        if isinstance(bundle, str) and not source_code:
            source_code = bundle
            bundle = None

        target_file = str(
            getattr(finding, "file_path", None) or getattr(finding, "path", "")
        )

        if not source_code and target_file:
            file_p = Path(target_file)
            if not file_p.exists() and (Path.cwd() / file_p).exists():
                file_p = Path.cwd() / file_p
            if not file_p.exists():
                matches = list(Path.cwd().glob(f"**/{file_p.name}"))
                if matches:
                    file_p = matches[0]
            if file_p.exists():
                source_code = file_p.read_text(encoding="utf-8")

        if not source_code:
            return None

        process_name = getattr(finding, "location", None)
        if not process_name and finding.message:
            match = re.search(r"Process ['\"]([^'\"]+)['\"]", finding.message)
            if match:
                process_name = match.group(1)

        if not process_name:
            return None

        message = (finding.message or "").lower()

        # Case A: Missing container directive
        if "has no container defined" in message or "no container" in message:
            patched_code = inject_directive(
                code=source_code,
                process_name=process_name,
                directive=f'container "{DEFAULT_CONTAINER}"',
                comment="// TODO: Replace with specific tool image (e.g. biocontainers/samtools:1.17)",
            )
            explanation = (
                f"Inject default container '{DEFAULT_CONTAINER}' "
                f"into process '{process_name}'."
            )
            return FixProposal(
                finding_id=getattr(finding, "id", "") or f"W001:{process_name}",
                rule_id=self.rule_id,
                category=getattr(finding, "category", "") or "containerization",
                target_file=target_file,
                original_snippet=source_code,
                proposed_snippet=patched_code,
                explanation=explanation,
                strategy_layer=self.strategy_layer,
            )

        # Case B: Unpinned container tag (latest or tagless)
        container_match = re.search(
            r"image:\s*['\"]([^'\"]+)['\"]", finding.message or ""
        )
        if container_match:
            unpinned_image = container_match.group(1)
            if ":" in unpinned_image:
                pinned_image = re.sub(
                    r":latest$", f":{DEFAULT_PINNED_TAG}", unpinned_image
                )
            else:
                pinned_image = f"{unpinned_image}:{DEFAULT_PINNED_TAG}"

            patched_code = replace_directive_text(
                code=source_code,
                old_text=unpinned_image,
                new_text=pinned_image,
            )
            explanation = (
                f"Pin container image tag in process '{process_name}' "
                f"from '{unpinned_image}' to '{pinned_image}'."
            )
            return FixProposal(
                finding_id=getattr(finding, "id", "") or f"W001:{process_name}",
                rule_id=self.rule_id,
                category=getattr(finding, "category", "") or "containerization",
                target_file=target_file,
                original_snippet=source_code,
                proposed_snippet=patched_code,
                explanation=explanation,
                strategy_layer=self.strategy_layer,
            )

        return None
