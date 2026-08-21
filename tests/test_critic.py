"""Unit tests for LiteLLM AI Critic Agent and Knowledge Store fallback integration."""

from unittest.mock import MagicMock, patch

import pytest

from workflow_clinic.advisor.retriever import RuleKnowledgeStore
from workflow_clinic.critic.agent import AICriticAgent
from workflow_clinic.models.diagnosis import DiagnosisReport, Finding, Remediation


def test_ai_critic_fallback_without_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify AICriticAgent defaults to Knowledge Store fallback when no LLM API keys are set."""
    # Ensure all LLM API environment variables are cleared
    for key in [
        "GEMINI_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "MISTRAL_API_KEY",
        "COHERE_API_KEY",
        "GROQ_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)

    agent = AICriticAgent(enable_llm=True)
    finding = Finding(
        id="test-id-1",
        rule_id="W001",
        severity="HIGH",
        category="containerization",
        title="Unpinned Container Tag",
        file_path="main.nf",
        line_number=12,
    )

    remediation, _ = agent.enhance_finding(finding)

    assert isinstance(remediation, Remediation)
    assert "W001" in remediation.summary
    assert len(remediation.explanation) > 0


def test_ai_critic_fallback_custom_knowledge_store() -> None:
    """Verify custom RuleKnowledgeStore integration in fallback mode."""
    mock_ks = MagicMock(spec=RuleKnowledgeStore)
    mock_ks.retrieve.return_value = [
        "## Solution\n\nPin container tags using SHA or explicit version."
    ]

    agent = AICriticAgent(knowledge_store=mock_ks, enable_llm=False)
    finding = Finding(
        id="test-id-2",
        rule_id="W001",
        severity="HIGH",
        category="containerization",
        title="Unpinned Container",
        file_path="modules/fastqc.nf",
    )

    remediation, _ = agent.enhance_finding(finding)

    assert isinstance(remediation, Remediation)
    assert "Pin container tags using SHA" in remediation.explanation
    mock_ks.retrieve.assert_called_once_with("W001")


@patch("workflow_clinic.critic.agent.litellm.completion")
def test_ai_critic_llm_completion_success(mock_completion: MagicMock) -> None:
    """Verify successful LLM response parsing into Remediation instance."""
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content='```json\n{"summary": "Pin container tag to v0.11.9", "explanation": "Using unpinned container tags breaks reproducibility.", "code_example": "container \'biocontainers/fastqc:v0.11.9\'"}\n```'
            )
        )
    ]
    mock_completion.return_value = mock_response

    agent = AICriticAgent(api_key="test-key", model_name="gemini/gemini-3.6-flash")
    finding = Finding(
        id="test-id-3",
        rule_id="W001",
        severity="HIGH",
        category="containerization",
        title="Unpinned Container",
        file_path="main.nf",
    )

    remediation, _ = agent.enhance_finding(finding)

    assert isinstance(remediation, Remediation)
    assert remediation.summary == "Pin container tag to v0.11.9"
    assert (
        remediation.explanation
        == "Using unpinned container tags breaks reproducibility."
    )
    assert remediation.code_example == "container 'biocontainers/fastqc:v0.11.9'"
    mock_completion.assert_called_once()


@patch("workflow_clinic.critic.agent.litellm.completion")
def test_ai_critic_llm_completion_error_fallback(mock_completion: MagicMock) -> None:
    """Verify LLM exception triggers graceful fallback to Knowledge Store."""
    mock_completion.side_effect = RuntimeError("API rate limit exceeded")

    agent = AICriticAgent(api_key="test-key")
    finding = Finding(
        id="test-id-4",
        rule_id="W002",
        severity="MEDIUM",
        category="resources",
        title="Missing Resource Limits",
        file_path="processes/align.nf",
    )

    remediation, _ = agent.enhance_finding(finding)

    assert isinstance(remediation, Remediation)
    assert (
        "W002" in remediation.summary
        or "Missing Resource Limits" in remediation.summary
    )


def test_ai_critic_enhance_report() -> None:
    """Verify enhance_report populates remediations for all findings in a DiagnosisReport."""
    agent = AICriticAgent(enable_llm=False)
    report = DiagnosisReport(
        workflow_name="Test Workflow",
        tasks_count=2,
        findings_count=2,
        findings=[
            Finding(
                id="f1",
                rule_id="W001",
                severity="HIGH",
                category="containerization",
                title="Unpinned Container",
                file_path="main.nf",
            ),
            Finding(
                id="f2",
                rule_id="W002",
                severity="MEDIUM",
                category="resources",
                title="Missing Resource Limits",
                file_path="main.nf",
            ),
        ],
    )

    enhanced_report, fallback_count = agent.enhance_report(report)

    assert isinstance(enhanced_report, DiagnosisReport)
    assert len(enhanced_report.findings) == 2
    assert enhanced_report.findings[0].remediation is not None
    assert enhanced_report.findings[1].remediation is not None
    assert fallback_count == 2
