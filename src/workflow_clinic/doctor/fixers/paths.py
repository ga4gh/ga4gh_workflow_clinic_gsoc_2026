"""Layer 2 Regex Fixer for hardcoded absolute paths (Rule W003)."""

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


def path_to_param_name(path: str) -> str:
    """Generate a clean, meaningful Nextflow parameter name from an absolute path.

    Extracts the file stem and cleans non-alphanumeric characters. If the stem is
    too generic (e.g. 'input', 'output', 'data', 'file', 'tmp', 'results'), it prefixes
    with the parent directory name to maintain clarity.

    Args:
        path: Absolute or literal path string (e.g., '/data/genomes/hg38.fa').

    Returns:
        Formatted parameter identifier (e.g., 'params.genome_file' or 'params.genomes_hg38').
    """
    raw_path = Path(path.strip("'\""))
    stem = raw_path.stem.lower()
    clean_stem = re.sub(r"[^a-z0-9_]", "_", stem).strip("_")

    generic_stems = {
        "input",
        "output",
        "data",
        "file",
        "tmp",
        "temp",
        "results",
        "sample",
        "test",
        "read",
        "reads",
    }

    if not clean_stem or clean_stem in generic_stems:
        parent_name = raw_path.parent.name.lower()
        clean_parent = re.sub(r"[^a-z0-9_]", "_", parent_name).strip("_")
        if clean_parent and clean_parent != "root":
            clean_stem = f"{clean_parent}_{clean_stem}" if clean_stem else clean_parent
        else:
            clean_stem = f"{clean_stem}_file" if clean_stem else "input_file"

    return f"params.{clean_stem}"


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


def _find_absolute_path_target(finding: Finding, source_code: str) -> str | None:
    """Locate the hardcoded path from finding message or source code."""
    if finding.message:
        match = re.search(r"['\"](/[^\s'\"]+)['\"]", finding.message)
        if match:
            return match.group(1)

    path_pattern = re.compile(r"['\"](/[^'\"\n]+)['\"]")
    match = path_pattern.search(source_code)
    return match.group(1) if match else None


def _replace_path_with_param(code: str, path_str: str, param_name: str) -> str:
    """Safely replace single-quoted, double-quoted, or raw path in code string."""
    quoted_single = f"'{path_str}'"
    quoted_double = f'"{path_str}"'

    if quoted_single in code:
        return code.replace(quoted_single, param_name)
    if quoted_double in code:
        return code.replace(quoted_double, param_name)
    if path_str in code:
        return code.replace(path_str, param_name)
    return code


def _fix_path_in_code(
    code: str, path_str: str, param_name: str, process_name: str | None = None
) -> str:
    """Replace path in code, scoped to target process when process_name is provided."""
    if process_name:
        try:
            start_line, end_line = get_process_line_range(code, process_name)
            lines = code.splitlines(keepends=True)
            if 1 <= start_line <= end_line <= len(lines):
                block_text = "".join(lines[start_line - 1 : end_line])
                patched_block = _replace_path_with_param(
                    block_text, path_str, param_name
                )
                return (
                    "".join(lines[: start_line - 1])
                    + patched_block
                    + "".join(lines[end_line:])
                )
        except Exception:  # noqa: BLE001, S110
            pass

    return _replace_path_with_param(code, path_str, param_name)


@FixerRegistry.register
class PathRegexFixer(BaseFixer):
    """Layer 2 Fixer that replaces hardcoded absolute paths with parameterized Nextflow variables."""

    rule_id = "W003"
    strategy_layer = FixStrategyLayer.LAYER2_REGEX

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | str | None = None,
        source_code: str | None = None,
    ) -> FixProposal | None:
        """Generate a FixProposal replacing hardcoded paths with parameterized identifiers."""
        if isinstance(bundle, str) and not source_code:
            source_code = bundle
            bundle = None

        target_file = str(
            getattr(finding, "file_path", None) or getattr(finding, "path", "")
        )

        code = _extract_source_code(target_file, source_code)
        if not code:
            return None

        extracted_path = _find_absolute_path_target(finding, code)
        if not extracted_path:
            return None

        param_name = path_to_param_name(extracted_path)
        process_name = getattr(finding, "process_name", None)
        patched_code = _fix_path_in_code(code, extracted_path, param_name, process_name)

        if patched_code == code:
            return None

        explanation = (
            f"Replace hardcoded absolute path '{extracted_path}' "
            f"with parameterized reference '{param_name}'."
        )

        return FixProposal(
            finding_id=getattr(finding, "id", "")
            or f"W003:{target_file}:{extracted_path}",
            rule_id=self.rule_id,
            category=getattr(finding, "category", "") or "portability",
            target_file=target_file,
            original_snippet=code,
            proposed_snippet=patched_code,
            explanation=explanation,
            strategy_layer=self.strategy_layer,
            line_number=getattr(finding, "line_number", None),
        )
