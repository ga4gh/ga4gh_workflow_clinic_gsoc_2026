"""AI Critic Agent implementation powered by LiteLLM and TOML Knowledge Store."""

import json
import logging
import os
from typing import Any

from workflow_clinic.advisor.retriever import RuleKnowledgeStore
from workflow_clinic.models.diagnosis import DiagnosisReport, Finding, Remediation

logger = logging.getLogger(__name__)

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
        model_name: str = "gemini/gemini-2.5-flash",
        api_key: str | None = None,
        knowledge_store: RuleKnowledgeStore | None = None,
        enable_llm: bool = True,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize the AI Critic Agent.

        Args:
            model_name: Target LiteLLM model identifier (e.g. gemini/gemini-2.5-flash, gpt-4o).
            api_key: Optional explicit API key override.
            knowledge_store: Optional custom RuleKnowledgeStore instance.
            enable_llm: Whether to attempt LLM calls when API keys are available.
        """
        self.model_name = model_name
        self.api_key = api_key
        self.enable_llm = enable_llm
        self.knowledge_store = knowledge_store or RuleKnowledgeStore()

    def _has_api_key(self) -> bool:
        """Check if an explicit API key or standard LLM environment variable is set."""
        if self.api_key:
            return True

        env_keys = [
            "GEMINI_API_KEY",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "MISTRAL_API_KEY",
            "COHERE_API_KEY",
            "GROQ_API_KEY",
        ]
        return any(bool(os.getenv(k)) for k in env_keys)

    def _fallback_remediation(
        self, finding: Finding, kb_sections: list[str] | None = None
    ) -> Remediation:
        """Construct a structured Remediation directly from the TOML Knowledge Store."""
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
            )

        return Remediation(
            summary=f"Resolve issue: {finding.title}",
            explanation=(
                f"Finding '{finding.title}' ({finding.rule_id}) was flagged in category '{finding.category}'. "
                f"Please review file '{finding.file_path}' to ensure cloud readiness and compliance."
            ),
            code_example=None,
        )

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
- Line Number: {finding.line_number or "N/A"}

KNOWLEDGE STORE RECOMMENDATIONS:
{context_block}

INSTRUCTIONS:
Provide structured remediation advice in valid JSON format matching the schema below:
{{
  "summary": "Short 1-sentence summary of the required fix",
  "explanation": "Detailed explanation of why this fix is necessary for cloud readiness and portability",
  "code_example": "Optional code snippet demonstrating the correct pattern (or null)"
}}

Return ONLY valid JSON.
"""

    def enhance_finding(self, finding: Finding) -> Remediation:
        """Generate structured remediation advice for a single diagnostic finding.

        Args:
            finding: Target Finding model instance to enhance.

        Returns:
            Remediation instance containing summary, explanation, and optional code_example.
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
            content = response.choices[0].message.content.strip()

            # Clean JSON markdown code blocks if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:].strip()

            data = json.loads(content)
            return Remediation(
                summary=data.get("summary", f"Resolve {finding.rule_id}"),
                explanation=data.get("explanation", "Review workflow configuration."),
                code_example=data.get("code_example"),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "LLM completion failed for rule %s (%s). Falling back to Knowledge Store.",
                finding.rule_id,
                e,
            )
            return self._fallback_remediation(finding, kb_sections)

    def enhance_report(self, report: DiagnosisReport) -> DiagnosisReport:
        """Enhance all findings in a DiagnosisReport with AI Critic remediation advice.

        Args:
            report: Target DiagnosisReport instance.

        Returns:
            New DiagnosisReport instance with populated remediations.
        """
        enhanced_findings = []
        for finding in report.findings:
            remediation = self.enhance_finding(finding)
            enhanced_finding = finding.model_copy(update={"remediation": remediation})
            enhanced_findings.append(enhanced_finding)

        return report.model_copy(update={"findings": enhanced_findings})
