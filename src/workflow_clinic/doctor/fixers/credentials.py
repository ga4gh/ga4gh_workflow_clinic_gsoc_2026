"""Layer 2 Regex Fixer for hardcoded credentials and secrets (Rule W004)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from workflow_clinic.doctor.base import BaseFixer, FixerRegistry
from workflow_clinic.doctor.patcher import get_process_line_range
from workflow_clinic.models.fix import FixProposal, FixStrategyLayer

if TYPE_CHECKING:
    from workflow_clinic.models.diagnosis import Finding
    from workflow_clinic.models.workflow_bundle import WorkflowBundle


_VENDOR_REPLACEMENTS: list[tuple[re.Pattern[str], str, str]] = [
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "params.aws_access_key",
        "AWS Access Key",
    ),
    (
        re.compile(r"(?i)aws_secret_access_key\s*=\s*['\"]([A-Za-z0-9+/]{40})['\"]"),
        "aws_secret_access_key = params.aws_secret_access_key",
        "AWS Secret Access Key",
    ),
    (
        re.compile(r"gh[pous]_[A-Za-z0-9]{36}"),
        "params.github_token",
        "GitHub Personal Access Token",
    ),
    (
        re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
        "params.github_token",
        "GitHub Fine-Grained Token",
    ),
    (
        re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
        "params.slack_token",
        "Slack Token",
    ),
    (
        re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{24,}"),
        "params.stripe_key",
        "Stripe Secret Key",
    ),
    (
        re.compile(r"AIza[0-9A-Za-z_-]{35}"),
        "params.google_api_key",
        "Google API Key",
    ),
]

_GENERIC_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(api_key|password|secret|token|auth_token)\s*=\s*['\"]([^'\"]+)['\"]"
)


def _extract_source_code(target_file: str, current_code: str | None) -> str | None:
    """Helper to retrieve source code from disk if not provided directly."""
    if current_code is not None:
        return current_code

    if not target_file:
        return None

    file_p = Path(target_file)
    if not file_p.exists() and (Path.cwd() / file_p).exists():
        file_p = Path.cwd() / file_p
    if not file_p.exists():
        matches = list(Path.cwd().glob(f"**/{file_p.name}"))
        if matches:
            file_p = matches[0]

    return file_p.read_text(encoding="utf-8") if file_p.exists() else None


def _mask_and_parameterize_secrets(
    block_text: str,
) -> tuple[str, str | None]:
    """Replace hardcoded credentials in block_text with parameterized variables."""
    # Check vendor patterns
    for pattern, replacement, vendor_name in _VENDOR_REPLACEMENTS:
        match = pattern.search(block_text)
        if match:
            matched_str = match.group(0)
            lines = block_text.splitlines(keepends=True)
            for i, line in enumerate(lines):
                if matched_str in line:
                    stripped = line.rstrip("\r\n")
                    ending = line[len(stripped) :]
                    new_line = stripped.replace(matched_str, replacement, 1)
                    if "Rotate this credential" not in new_line:
                        new_line = f"{new_line}  // TODO: Rotate this credential immediately — it was exposed in workflow code"
                    lines[i] = new_line + ending
                    return (
                        "".join(lines),
                        f"Mask and parameterize hardcoded {vendor_name} to '{replacement}'. Rotate this credential immediately.",
                    )

    # Check generic assignments
    generic_match = _GENERIC_SECRET_ASSIGNMENT.search(block_text)
    if generic_match:
        key_name = generic_match.group(1)
        full_match = generic_match.group(0)
        lines = block_text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if full_match in line:
                stripped = line.rstrip("\r\n")
                ending = line[len(stripped) :]
                new_line = stripped.replace(
                    full_match, f"{key_name} = params.{key_name.lower()}", 1
                )
                if "Rotate this credential" not in new_line:
                    new_line = f"{new_line}  // TODO: Rotate this credential immediately — it was exposed in workflow code"
                lines[i] = new_line + ending
                return (
                    "".join(lines),
                    f"Parameterize generic credential assignment '{key_name}'. Rotate this credential immediately.",
                )

    return block_text, None


@FixerRegistry.register
class CredentialRegexFixer(BaseFixer):
    """Layer 2 Fixer that masks and parameterizes hardcoded secrets (Rule W004)."""

    rule_id = "W004"
    strategy_layer = FixStrategyLayer.LAYER2_REGEX

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | str | None = None,
        source_code: str | None = None,
    ) -> FixProposal | None:
        """Generate a FixProposal parameterizing detected credentials with a rotation TODO."""
        if isinstance(bundle, str) and not source_code:
            source_code = bundle
            bundle = None

        target_file = str(
            getattr(finding, "file_path", None) or getattr(finding, "path", "")
        )

        code = _extract_source_code(target_file, source_code)
        if not code:
            return None

        process_name = getattr(finding, "process_name", None)
        if process_name:
            try:
                start_line, end_line = get_process_line_range(code, process_name)
                lines = code.splitlines(keepends=True)
                if 1 <= start_line <= end_line <= len(lines):
                    block_text = "".join(lines[start_line - 1 : end_line])
                    patched_block, rationale = _mask_and_parameterize_secrets(
                        block_text
                    )
                    if patched_block != block_text and rationale:
                        patched_code = (
                            "".join(lines[: start_line - 1])
                            + patched_block
                            + "".join(lines[end_line:])
                        )
                        return FixProposal(
                            finding_id=getattr(finding, "id", "")
                            or f"W004:{target_file}:{process_name}",
                            rule_id=self.rule_id,
                            category=getattr(finding, "category", "") or "security",
                            target_file=target_file,
                            original_snippet=code,
                            proposed_snippet=patched_code,
                            explanation=f"{rationale} in process '{process_name}'.",
                            strategy_layer=self.strategy_layer,
                            line_number=getattr(finding, "line_number", None),
                        )
            except Exception:  # noqa: BLE001, S110
                pass

        # Fallback to full file replacement if process scoping unavailable
        patched_code, rationale = _mask_and_parameterize_secrets(code)
        if patched_code == code or not rationale:
            return None

        return FixProposal(
            finding_id=getattr(finding, "id", "") or f"W004:{target_file}",
            rule_id=self.rule_id,
            category=getattr(finding, "category", "") or "security",
            target_file=target_file,
            original_snippet=code,
            proposed_snippet=patched_code,
            explanation=rationale,
            strategy_layer=self.strategy_layer,
            line_number=getattr(finding, "line_number", None),
        )
