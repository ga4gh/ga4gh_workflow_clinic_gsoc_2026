"""Layer 3 AI Fixer utilizing LiteLLM for dynamic, context-aware Nextflow code repair."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import litellm

from workflow_clinic.doctor.base import BaseFixer, FixerRegistry
from workflow_clinic.models.fix import FixProposal, FixStrategyLayer
from workflow_clinic.utils.llm import check_model_api_key, resolve_model

if TYPE_CHECKING:
    from workflow_clinic.models.diagnosis import Finding
    from workflow_clinic.models.workflow_bundle import WorkflowBundle

logger = logging.getLogger(__name__)

try:
    from groovy_parser.parser import parse_and_digest_groovy_content
    from lark.exceptions import LarkError

    _HAS_GROOVY_PARSER = True
except ImportError:
    _HAS_GROOVY_PARSER = False
    parse_and_digest_groovy_content = None  # type: ignore[assignment]
    LarkError = Exception  # type: ignore[misc,assignment]

SYSTEM_PROMPT = """You are an automated Nextflow code repair assistant.
You will receive Nextflow workflow code along with an issue description.
Your task is to fix the issue and return ONLY the corrected, valid Nextflow DSL2 code.
CRITICAL REQUIREMENTS:
- Preserve all existing comments, docstrings, shebangs (e.g. `#!/usr/bin/env nextflow`), and license headers exactly as given.
- Do NOT omit or truncate any unchanged processes or code blocks.
- Do NOT include any conversational preamble, explanations, or postambles.
- Do NOT enclose your output in markdown backticks or code blocks."""

USER_PROMPT_TEMPLATE = """Please fix the following issue in this Nextflow workflow code:

ISSUE: {finding_message}
RULE: {rule_id}
PROCESS: {process_name}

ORIGINAL CODE:
{code_snippet}

Return ONLY the corrected Nextflow code:"""


RULE_GUIDANCE: dict[str, str] = {
    "W001": "Identify the primary bioinformatics tool and add a pinned container directive (e.g. container 'quay.io/biocontainers/<tool>:<version>').",
    "W002": "Declare realistic Nextflow resource limits (e.g. cpus 2, memory '4 GB') suited for this task.",
    "W003": "Replace hardcoded absolute file system paths with parameterized Nextflow variables (e.g. ${params.input_file}).",
    "W004": "Remove hardcoded credentials or API keys and reference environment variables or Nextflow secrets.",
    "AI001": "Refactor unsafe shell commands (such as piping curl to bash), add fail-fast flags (-fsSL), and ensure clean temporary file cleanup.",
    "AI002": "Remove external runtime installer scripts from the execution block and ensure the process relies on containerized tool binaries.",
    "AI003": "Add missing error handling, input validation, or guardrails to prevent silent task failures.",
}


def _load_source_code(target_file: str, current_code: str | None) -> str | None:
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


@FixerRegistry.register
class AIFixer(BaseFixer):
    """Layer 3 AI-driven Fixer that leverages LiteLLM to generate code patches for complex or AI-identified findings."""

    rule_id = "AI001"
    rule_ids: ClassVar[list[str]] = [
        "AI001",
        "AI002",
        "AI003",
        "W001",
        "W002",
        "W003",
        "W004",
    ]
    strategy_layer = FixStrategyLayer.LAYER3_AI

    def __init__(self, model: str | None = None) -> None:
        self.model = model or resolve_model()

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | str | None = None,
        source_code: str | None = None,
    ) -> FixProposal | None:
        """Call LLM provider to generate a context-aware fix proposal."""
        if isinstance(bundle, str) and not source_code:
            source_code = bundle
            bundle = None

        target_file = str(
            getattr(finding, "file_path", None) or getattr(finding, "path", "")
        )

        code = _load_source_code(target_file, source_code)
        if not code:
            return None

        if not check_model_api_key(self.model):
            logger.debug(
                "AIFixer skipping: No valid API key found for model '%s'", self.model
            )
            return None

        process_name = (
            getattr(finding, "process_name", None)
            or getattr(finding, "location", None)
            or "workflow"
        )

        guidance = RULE_GUIDANCE.get(
            finding.rule_id.upper(), "Apply best-practice Nextflow DSL2 refactoring."
        )
        prompt = (
            USER_PROMPT_TEMPLATE.format(
                finding_message=finding.message or "Unknown issue",
                rule_id=finding.rule_id,
                process_name=process_name,
                code_snippet=code,
            )
            + f"\n\nSPECIFIC GUIDANCE:\n{guidance}"
        )

        try:
            response = litellm.completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )
            raw_output = response.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "AIFixer LLM completion failed for finding %s: %s",
                finding.id,
                e,
            )
            return None

        cleaned_code = self._clean_llm_code_output(raw_output)
        if not cleaned_code or cleaned_code.strip() == code.strip():
            return None

        explanation = f"AI-generated patch for finding '{finding.rule_id}' in process '{process_name}'."

        return FixProposal(
            finding_id=getattr(finding, "id", "")
            or f"{finding.rule_id}:{target_file}:{process_name}",
            rule_id=finding.rule_id,
            category=getattr(finding, "category", "") or "ai_audit",
            target_file=target_file,
            original_snippet=code,
            proposed_snippet=cleaned_code,
            explanation=explanation,
            strategy_layer=self.strategy_layer,
            line_number=getattr(finding, "line_number", None),
        )

    def verify_fix(self, modified_file: Path) -> bool:
        """Verify that the AI-patched file contains syntactically valid Nextflow/Groovy code."""
        if not modified_file.exists():
            return False

        if not _HAS_GROOVY_PARSER or parse_and_digest_groovy_content is None:
            return True

        try:
            content = modified_file.read_text(encoding="utf-8")
            parse_and_digest_groovy_content(content)
        except (LarkError, Exception):  # noqa: BLE001
            logger.warning(
                "AIFixer verification failed: Patched file '%s' has syntax errors.",
                modified_file,
            )
            return False
        else:
            return True

    @staticmethod
    def _clean_llm_code_output(output: str) -> str:
        """Strip markdown fences and whitespace from LLM output."""
        text = output.strip()
        fence_match = re.search(
            r"^```(?:groovy|nextflow|nf)?\s*\n(.*?)\n```$",
            text,
            re.DOTALL | re.MULTILINE,
        )
        if fence_match:
            return fence_match.group(1).strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            # Needs at least fence-open and fence-close lines
            min_lines_for_fence = 2
            if len(lines) >= min_lines_for_fence:
                return "\n".join(lines[1:-1]).strip()
        return text
