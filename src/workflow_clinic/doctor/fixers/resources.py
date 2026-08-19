"""Layer 1 AST Fixer for W002 (Missing CPU and Memory Resource Limits)."""

import re
from pathlib import Path

from workflow_clinic.doctor.base import BaseFixer
from workflow_clinic.doctor.patcher import inject_directive
from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.models.fix import FixProposal, FixStrategyLayer
from workflow_clinic.models.workflow_bundle import WorkflowBundle

DEFAULT_CPUS = 1
DEFAULT_MEMORY = "2 GB"


class ResourceASTFixer(BaseFixer):
    """Layer 1 AST Fixer for Rule W002 (Resource Limits)."""

    rule_id = "W002"
    strategy_layer = FixStrategyLayer.LAYER1_AST
    title = "Fix Missing CPU or Memory Resource Limits"

    def can_fix(self, finding: Finding) -> bool:
        """Determine if this fixer can remediate the given W002 finding.

        Args:
            finding: Diagnostic finding instance.

        Returns:
            True if rule_id matches W002.
        """
        return finding.rule_id == "W002"

    def generate_proposal(  # noqa: C901
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,
        source_code: str = "",
    ) -> FixProposal | None:
        """Generate a FixProposal for missing CPU or memory resource directives.

        Args:
            finding: Diagnostic finding instance for W002.
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
            if file_p.exists():
                source_code = file_p.read_text()

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

        # Case A: Missing CPU limit
        if "cpu resource limit" in message or "does not declare a cpu" in message:
            patched_code = inject_directive(
                code=source_code,
                process_name=process_name,
                directive=f"cpus {DEFAULT_CPUS}",
                comment="// TODO: Adjust based on tool multi-threading requirements",
            )
            explanation = (
                f"Inject default CPU limit 'cpus {DEFAULT_CPUS}' "
                f"into process '{process_name}'."
            )
            return FixProposal(
                finding_id=getattr(finding, "id", "") or f"W002:{process_name}",
                rule_id=self.rule_id,
                category=getattr(finding, "category", "") or "resources",
                target_file=target_file,
                original_snippet=source_code,
                proposed_snippet=patched_code,
                explanation=explanation,
                strategy_layer=self.strategy_layer,
            )

        # Case B: Missing Memory limit
        if "memory resource limit" in message or "does not declare a memory" in message:
            patched_code = inject_directive(
                code=source_code,
                process_name=process_name,
                directive=f"memory '{DEFAULT_MEMORY}'",
                comment="// TODO: Adjust based on tool memory requirements",
            )
            explanation = (
                f"Inject default Memory limit 'memory \"{DEFAULT_MEMORY}\"' "
                f"into process '{process_name}'."
            )
            return FixProposal(
                finding_id=getattr(finding, "id", "") or f"W002:{process_name}",
                rule_id=self.rule_id,
                category=getattr(finding, "category", "") or "resources",
                target_file=target_file,
                original_snippet=source_code,
                proposed_snippet=patched_code,
                explanation=explanation,
                strategy_layer=self.strategy_layer,
            )

        return None
