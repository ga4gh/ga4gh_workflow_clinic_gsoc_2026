import json
from unittest.mock import MagicMock, patch

import pytest

from workflow_clinic.critic.agent import AICriticAgent
from workflow_clinic.models.metadata import WorkflowMetadata
from workflow_clinic.models.task import Task
from workflow_clinic.models.workflow_bundle import WorkflowBundle


@pytest.fixture
def agent():
    return AICriticAgent(model_name="test-model", api_key="test-key")


@pytest.fixture
def sample_bundle():
    return WorkflowBundle(
        metadata=WorkflowMetadata(name="Test Workflow", version="1.0"),
        tasks=[
            Task(id="FASTQC", name="FASTQC", file_path="main.nf", line_number=10),
            Task(id="BWA_MEM", name="BWA_MEM", file_path="main.nf", line_number=20),
        ],
    )


def test_validate_ai_finding_rejects_unknown_process(agent, sample_bundle):
    raw = {
        "rule_id": "AI001",
        "severity": "warning",
        "process_name": "UNKNOWN_PROCESS",
        "message": "Some issue",
    }
    result = agent._validate_ai_finding(raw, sample_bundle)
    assert result is None


def test_validate_ai_finding_rejects_invalid_rule_id(agent, sample_bundle):
    raw = {
        "rule_id": "W001",
        "severity": "warning",
        "process_name": "FASTQC",
        "message": "Some issue",
    }
    result = agent._validate_ai_finding(raw, sample_bundle)
    assert result is None


def test_validate_ai_finding_normalizes_bad_severity(agent, sample_bundle):
    raw = {
        "rule_id": "AI002",
        "severity": "CRITICAL_BAD",
        "process_name": "FASTQC",
        "message": "Some issue",
    }
    result = agent._validate_ai_finding(raw, sample_bundle)
    assert result is not None
    assert result.severity == "WARNING"


def test_validate_ai_finding_rejects_empty_message(agent, sample_bundle):
    raw = {
        "rule_id": "AI003",
        "severity": "warning",
        "process_name": "FASTQC",
        "message": "   ",
    }
    result = agent._validate_ai_finding(raw, sample_bundle)
    assert result is None


def test_serialize_bundle(agent, sample_bundle):
    serialized = agent._serialize_bundle(sample_bundle)
    data = json.loads(serialized)
    assert data["workflow_name"] == "Test Workflow"
    assert len(data["tasks"]) == 2
    assert data["tasks"][0]["name"] == "FASTQC"


@patch("workflow_clinic.critic.agent.litellm.completion")
def test_audit_workflow_valid_json(mock_completion, agent, sample_bundle):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        [
            {
                "rule_id": "AI001",
                "severity": "error",
                "process_name": "FASTQC",
                "message": "Found shell injection risk.",
            }
        ]
    )
    mock_completion.return_value = mock_response

    findings = agent.audit_workflow(sample_bundle)
    assert len(findings) == 1
    assert findings[0].rule_id == "AI001"
    assert findings[0].severity == "ERROR"
    assert findings[0].message == "Found shell injection risk."


@patch("workflow_clinic.critic.agent.litellm.completion")
def test_audit_workflow_invalid_json(mock_completion, agent, sample_bundle):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Not a JSON"
    mock_completion.return_value = mock_response

    findings = agent.audit_workflow(sample_bundle)
    assert len(findings) == 0


def test_audit_workflow_max_tasks_truncation(agent):
    tasks = [Task(id=f"TASK_{i}", name=f"TASK_{i}") for i in range(50)]
    bundle = WorkflowBundle(
        metadata=WorkflowMetadata(name="Huge Workflow"), tasks=tasks
    )
    with (
        patch.object(agent, "_serialize_bundle", return_value="{}") as mock_serialize,
        patch("workflow_clinic.critic.agent.litellm.completion") as mock_completion,
    ):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "[]"
        mock_completion.return_value = mock_response

        agent.audit_workflow(bundle)

        # Assert serialize was called with truncated bundle
        args, _ = mock_serialize.call_args
        truncated_bundle = args[0]
        assert len(truncated_bundle.tasks) == 30


def test_audit_workflow_returns_empty_without_api_key(sample_bundle):
    """Verify audit_workflow returns [] when no API key is configured."""
    agent = AICriticAgent(model_name="test", api_key=None)
    # Ensure no env vars set
    with patch("os.getenv", return_value=None):
        findings = agent.audit_workflow(sample_bundle)
    assert findings == []


@patch("workflow_clinic.critic.agent.litellm.completion")
def test_audit_workflow_filters_invalid_findings(mock_completion, agent, sample_bundle):
    """Verify _validate_ai_finding filters are applied — invalid findings dropped."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps(
        [
            {
                "rule_id": "W001",
                "severity": "warning",
                "message": "bad rule_id",
            },  # rejected
            {
                "rule_id": "AI001",
                "severity": "warning",
                "message": "valid finding",
                "process_name": "FASTQC",
            },  # accepted
        ]
    )
    mock_completion.return_value = mock_response
    findings = agent.audit_workflow(sample_bundle)
    assert len(findings) == 1  # only the valid one
    assert findings[0].rule_id == "AI001"


@patch("workflow_clinic.critic.agent.litellm.completion")
def test_audit_workflow_handles_non_list_response(
    mock_completion, agent, sample_bundle
):
    """Verify non-list JSON response returns empty list."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({"error": "unexpected dict"})
    mock_completion.return_value = mock_response
    findings = agent.audit_workflow(sample_bundle)
    assert findings == []
