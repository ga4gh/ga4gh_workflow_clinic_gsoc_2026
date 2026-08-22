"""AI Critic Agent implementation powered by LiteLLM and TOML Knowledge Store."""

import json
import logging
import re
from typing import Any, NamedTuple

from workflow_clinic.advisor.retriever import RuleKnowledgeStore
from workflow_clinic.models.diagnosis import DiagnosisReport, Finding, Remediation
from workflow_clinic.models.workflow_bundle import WorkflowBundle
from workflow_clinic.utils.llm import check_model_api_key, resolve_model


class EnhancedResult(NamedTuple):
    """Result of an AI Critic enhancement pass."""

    report: DiagnosisReport
    fallback_count: int


logger = logging.getLogger(__name__)

MAX_AUDIT_TASKS = 30

try:
    import litellm
except ImportError:  # pragma: no cover
    from types import ModuleType

    litellm = ModuleType("litellm")  # type: ignore[assignment]
    litellm.completion = None  # type: ignore[attr-defined]


class AICriticAgent:
    """AI Critic Agent that enhances diagnostic findings with actionable remediation advice.

    Uses LiteLLM for multi-provider LLM calls (OpenAI, Gemini, Anthropic, Ollama) combined
    with exact rule knowledge from the local TOML Knowledge Store. Automatically falls back
    to TOML Knowledge Store recommendations if LLM API keys are missing or offline.
    """

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        knowledge_store: RuleKnowledgeStore | None = None,
        enable_llm: bool = True,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize the AI Critic Agent.

        Args:
            model_name: Target LiteLLM model identifier (e.g. gemini/gemini-3.6-flash, gpt-4o).
            api_key: Optional explicit API key override.
            knowledge_store: Optional custom RuleKnowledgeStore instance.
            enable_llm: Whether to attempt LLM calls when API keys are available.
        """
        self.model_name = model_name or resolve_model(
            explicit_model=None, api_key=api_key
        )
        self.api_key = api_key
        self.enable_llm = enable_llm
        self.knowledge_store = knowledge_store or RuleKnowledgeStore()

    def _has_api_key(self) -> bool:
        """Check if an API key exists specifically for the configured model provider."""
        return check_model_api_key(self.model_name, self.api_key)

    def _fallback_remediation(
        self, finding: Finding, kb_sections: list[str] | None = None
    ) -> tuple[Remediation, bool]:
        """Construct a structured Remediation directly from the TOML Knowledge Store.

        Returns:
            Tuple of (Remediation, is_fallback=True).
        """
        sections = (
            kb_sections
            if kb_sections is not None
            else self.knowledge_store.retrieve(finding.rule_id)
        )
        if sections:
            summary = (
                f"Remediation guidance for rule {finding.rule_id} ({finding.title})"
            )
            explanation = "\n\n".join(sections)
            return Remediation(
                summary=summary,
                explanation=explanation,
                code_example=None,
            ), True

        return Remediation(
            summary=f"Resolve issue: {finding.title}",
            explanation=(
                f"Finding '{finding.title}' ({finding.rule_id}) was flagged in category '{finding.category}'. "
                f"Please review file '{finding.file_path}' to ensure cloud readiness and compliance."
            ),
            code_example=None,
        ), True

    def _build_prompt(self, finding: Finding, kb_sections: list[str]) -> str:
        """Build a structured LLM prompt incorporating finding context and knowledge base rules."""
        context_block = (
            "\n\n".join(kb_sections) if kb_sections else "No specific KB entry found."
        )

        return f"""You are the GA4GH Workflow Clinic AI Critic.
Your job is to provide clear, actionable remediation guidance for a bioinformatics workflow diagnostic finding.

FINDING DETAILS:
- Rule ID: {finding.rule_id}
- Title: {finding.title}
- Category: {finding.category}
- Severity: {finding.severity}
- File Path: {finding.file_path}
- Line Number: {finding.line_number if finding.line_number is not None else "N/A"}

KNOWLEDGE STORE RECOMMENDATIONS:
{context_block}

INSTRUCTIONS:
Provide structured remediation advice in valid JSON format matching the schema below:
{{
  "summary": "Short 1-sentence summary of the required fix",
  "explanation": "Detailed explanation of why this fix is necessary for cloud readiness and portability",
  "code_example": "Optional code snippet demonstrating the correct pattern (MUST be a single JSON string or null)"
}}

Return ONLY valid JSON.
"""

    def enhance_finding(self, finding: Finding) -> tuple[Remediation, bool]:
        """Generate structured remediation advice for a single diagnostic finding.

        Args:
            finding: Target Finding model instance to enhance.

        Returns:
            Tuple of (Remediation, is_fallback) where is_fallback is True if local TOML fallback was used.
        """
        # Fetch TOML knowledge base context
        kb_sections = self.knowledge_store.retrieve(finding.rule_id)

        # Check if LLM calls should be attempted
        if (
            not self.enable_llm
            or not getattr(litellm, "completion", None)
            or not self._has_api_key()
        ):
            logger.info(
                "LLM API key not configured or disabled. Using TOML Knowledge Store fallback for rule %s",
                finding.rule_id,
            )
            return self._fallback_remediation(finding, kb_sections)

        prompt = self._build_prompt(finding, kb_sections)

        try:
            kwargs: dict[str, Any] = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key

            response = litellm.completion(**kwargs)
            if isinstance(response, dict):
                content = response["choices"][0]["message"]["content"].strip()
            else:
                content = response.choices[0].message.content.strip()

            # Clean JSON markdown code blocks if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:].strip()

            data = json.loads(content)
            code_example_raw = data.get("code_example")
            if code_example_raw is not None and not isinstance(code_example_raw, str):
                code_example_raw = json.dumps(code_example_raw, indent=2)

            return Remediation(
                summary=data.get("summary", f"Resolve {finding.rule_id}"),
                explanation=data.get("explanation", "Review workflow configuration."),
                code_example=code_example_raw,
            ), False
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "LLM completion failed for rule %s (%s). Falling back to Knowledge Store.",
                finding.rule_id,
                e,
            )
            return self._fallback_remediation(finding, kb_sections)

    def enhance_report(self, report: DiagnosisReport) -> EnhancedResult:
        """Enhance all findings in a DiagnosisReport with AI Critic remediation advice.

        Args:
            report: Target DiagnosisReport instance.

        Returns:
            EnhancedResult named tuple containing the new report and fallback count.
        """
        fallback_count = 0
        enhanced_findings = []
        for finding in report.findings:
            remediation, is_fallback = self.enhance_finding(finding)
            if is_fallback:
                fallback_count += 1
            enhanced_finding = finding.model_copy(update={"remediation": remediation})
            enhanced_findings.append(enhanced_finding)

        enhanced_report = report.model_copy(update={"findings": enhanced_findings})
        return EnhancedResult(report=enhanced_report, fallback_count=fallback_count)

    def _serialize_bundle(self, bundle: WorkflowBundle) -> str:
        """Produce a concise workflow summary for LLM context."""
        tasks = [
            {
                "name": task.name,
                "container": task.resources.container if task.resources else None,
                "cpus": task.resources.cpus if task.resources else None,
                "memory": task.resources.memory if task.resources else None,
                "script": task.command or None,
                "file_path": task.file_path,
                "line_number": task.line_number,
            }
            for task in bundle.tasks
        ]
        return json.dumps(
            {"workflow_name": bundle.metadata.name, "tasks": tasks}, indent=2
        )

    def _validate_ai_finding(self, raw: dict, bundle: WorkflowBundle) -> Finding | None:
        """Validate LLM-generated finding against actual WorkflowBundle."""
        # 1. rule_id must start with AI
        rule_id = raw.get("rule_id", "")
        if not rule_id.startswith("AI"):
            logger.warning("Rejected AI finding with invalid rule_id: %s", rule_id)
            return None

        # 2. If process name given, it must exist in the bundle
        process_name = raw.get("process_name") or raw.get("location")
        if process_name:
            known_tasks = {t.name for t in bundle.tasks}
            if process_name not in known_tasks:
                logger.warning(
                    "Rejected AI finding referencing unknown process: %s", process_name
                )
                return None
            raw["process_name"] = process_name  # Normalize in case they used location

        # 3. severity must be a valid enum value
        severity = raw.get("severity", "").lower()
        if severity not in ("info", "warning", "error"):
            raw["severity"] = "warning"  # safe default

        # 4. message must be non-empty
        if not raw.get("message", "").strip():
            return None

        try:
            return Finding(**raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to validate AI finding: %s", e)
            return None

    def audit_workflow(  # noqa: C901, PLR0912
        self, bundle: WorkflowBundle, static_findings: list[Any] | None = None
    ) -> list[Finding]:
        """Perform a high-level AI audit of the entire workflow."""
        if (
            not self.enable_llm
            or not getattr(litellm, "completion", None)
            or not self._has_api_key()
        ):
            logger.info("LLM API key not configured or disabled. Skipping AI audit.")
            return []

        if len(bundle.tasks) > MAX_AUDIT_TASKS:
            logger.warning(
                "Workflow has %d tasks — auditing first %d only.",
                len(bundle.tasks),
                MAX_AUDIT_TASKS,
            )
            # Truncate tasks for auditing to avoid token explosion
            bundle = bundle.model_copy(update={"tasks": bundle.tasks[:MAX_AUDIT_TASKS]})

        workflow_json = self._serialize_bundle(bundle)

        static_findings_str = "None"
        if static_findings:
            sf_list = [
                {
                    "process_name": getattr(f, "process_name", ""),
                    "rule_id": f.rule_id,
                    "message": f.message,
                }
                for f in static_findings
            ]
            static_findings_str = json.dumps(sf_list, indent=2)

        prompt = f"""You are a bioinformatics workflow auditor. Analyze this Nextflow workflow for cloud-readiness issues
that static rules miss: shell anti-patterns, implicit dependencies, missing error handling.

IMPORTANT DEDUPLICATION INSTRUCTION:
Do NOT report any issues that are already covered by these existing static findings:
{static_findings_str}

Use ONLY these rule_ids for categories of issues you find:
- AI001: Shell scripting anti-patterns or risky commands
- AI002: Implicit dependencies or silent failure risks
- AI003: Logic bugs or missing guardrails

Return ONLY a JSON array. Each item must match this exact schema:
[
  {{
    "rule_id": "AI001",
    "severity": "warning",
    "category": "shell_patterns",
    "title": "Short description",
    "message": "Detailed explanation",
    "process_name": "PROCESS_NAME or null",
    "file_path": "filename.nf or null"
  }}
]

Return [] if no issues found. Return ONLY JSON, no markdown fences, no explanation.

CRITICAL INSTRUCTIONS TO PREVENT HALLUCINATION:
1. DO NOT invent or hallucinate issues. If a process does not have a shell script anti-pattern, do not report it.
2. DO NOT assume missing functionality (like missing error handling or missing checks) is a bug unless it explicitly violates a known Nextflow/Cloud best practice.
3. If the script is perfectly fine, or if all issues are already covered by the static findings above, you MUST return an empty array []. Do not try to force a finding.

Workflow:
{workflow_json}
"""

        try:
            kwargs: dict[str, Any] = {
                "model": self.model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.2,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key

            response = litellm.completion(**kwargs)
            if isinstance(response, dict):
                content = response["choices"][0]["message"]["content"].strip()
            else:
                content = response.choices[0].message.content.strip()

            # Extract JSON array even if surrounded by conversational text or markdown blocks
            json_match = re.search(r"\[.*\]", content, re.DOTALL)
            if json_match:
                content = json_match.group(0).strip()
            elif content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:].strip()

            data = json.loads(content)
            if not isinstance(data, list):
                logger.warning("AI audit returned non-list JSON.")
                return []

            findings = []
            for raw_finding in data:
                validated = self._validate_ai_finding(raw_finding, bundle)
                if validated:
                    findings.append(validated)

            return findings  # noqa: TRY300

        except Exception as e:  # noqa: BLE001
            logger.warning(
                "LLM completion failed during AI audit. Returning empty findings. Error: %s",
                e,
            )
            return []
