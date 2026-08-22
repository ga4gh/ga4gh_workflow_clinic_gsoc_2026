"""Integration and unit tests for workflow-clinic fix CLI command."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import workflow_clinic.doctor.fixers  # noqa: F401
from workflow_clinic.cli import app
from workflow_clinic.doctor.base import BaseFixer, FixerRegistry
from workflow_clinic.doctor.fixers.ai import AIFixer
from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.models.fix import FixProposal, FixStrategyLayer
from workflow_clinic.models.workflow_bundle import WorkflowBundle, WorkflowMetadata

runner = CliRunner()


class DummyCliFixer(BaseFixer):
    """Dummy fixer for testing CLI fix command workflow."""

    rule_id = "W001"
    strategy_layer = FixStrategyLayer.LAYER1_AST

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,  # noqa: ARG002
        source_code: str | None = None,  # noqa: ARG002
    ) -> FixProposal | None:
        return FixProposal(
            finding_id=finding.id,
            rule_id=self.rule_id,
            category=finding.category,
            target_file=finding.file_path,
            original_snippet="process FASTQC {",
            proposed_snippet="process FASTQC {\n    container 'quay.io/biocontainers/fastqc:0.11.9'",
            explanation="Added container directive for process FASTQC",
            strategy_layer=self.strategy_layer,
        )


@pytest.fixture(autouse=True)
def _clear_env_and_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from GitHub environment variables and clean fixer registry."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    original_fixers = dict(FixerRegistry._fixers)
    FixerRegistry.clear()
    FixerRegistry.register(DummyCliFixer)
    yield
    FixerRegistry.clear()
    FixerRegistry._fixers.update(original_fixers)


def test_fix_cli_missing_diagnosis_file(tmp_path: Path) -> None:
    """Verify exit code 1 and actionable error when diagnosis.json does not exist."""
    result = runner.invoke(app, ["fix", str(tmp_path)])
    assert result.exit_code == 1
    assert "Could not find 'diagnosis.json'" in result.output
    assert "workflow-clinic examine" in result.output


def test_fix_cli_empty_findings_exits_clean(tmp_path: Path) -> None:
    """Verify exit code 0 when diagnosis report contains zero findings."""
    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "clean_pipeline",
                "tasks_count": 2,
                "findings_count": 0,
                "findings": [],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["fix", str(tmp_path)])
    assert result.exit_code == 0
    assert "No actionable findings to fix!" in result.output


def test_fix_cli_rule_filter_excludes_other_rules(tmp_path: Path) -> None:
    """Verify --rule W002 excludes W001 findings and exits cleanly if no matches found."""
    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "CRITICAL",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["fix", str(tmp_path), "-r", "W002"])
    assert result.exit_code == 0
    assert "No findings matching rule filter(s): W002" in result.output


def test_fix_cli_dry_run_renders_diff_table(tmp_path: Path) -> None:
    """Verify --dry-run prints proposal diff table without mutating target workflow file."""
    diag_file = tmp_path / "diagnosis.json"
    target_nf = tmp_path / "main.nf"
    original_code = "process FASTQC {\n    script:\n    'fastqc input'\n}\n"
    target_nf.write_text(original_code, encoding="utf-8")

    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "CRITICAL",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["fix", str(tmp_path), "--all", "--dry-run"])
    assert result.exit_code == 0
    assert "Workflow Doctor Dry Run (1 proposed fix(es))" in result.output
    assert "W001" in result.output
    assert "LAYER1_AST" in result.output
    # Ensure dry run did NOT modify file on disk
    assert target_nf.read_text(encoding="utf-8") == original_code


def test_fix_cli_apply_mode_modifies_file(tmp_path: Path) -> None:
    """Verify workflow-clinic fix --all applies proposals directly to disk."""
    diag_file = tmp_path / "diagnosis.json"
    target_nf = tmp_path / "main.nf"
    original_code = "process FASTQC {\n    script:\n    'fastqc input'\n}\n"
    target_nf.write_text(original_code, encoding="utf-8")

    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "CRITICAL",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["fix", str(tmp_path), "--all"])
    assert result.exit_code == 0
    assert "1/1 fix(es) applied successfully" in result.output
    assert "quay.io/biocontainers/fastqc:0.11.9" in target_nf.read_text(
        encoding="utf-8"
    )


def test_fix_cli_no_fixer_registered_for_rule(tmp_path: Path) -> None:
    """Verify clean exit message when finding exists but no fixer is registered for its rule."""
    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h2",
                        "rule_id": "W999",
                        "severity": "MEDIUM",
                        "category": "portability",
                        "title": "Unknown defect",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h2"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["fix", str(tmp_path), "--all"])
    assert result.exit_code == 0
    assert (
        "No registered fixers available for the selected findings yet" in result.output
    )


def test_fix_cli_github_source_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify exit with error message when --repo is supplied without a valid token."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    result = runner.invoke(app, ["fix", "--repo", "ga4gh/test-repo"])
    assert result.exit_code != 0
    assert "GitHub API token required" in result.output or "Error" in result.output


def test_fix_cli_interactive_shows_rules_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify category domain table includes Rules column in interactive mode."""
    monkeypatch.setenv("FORCE_INTERACTIVE", "1")
    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 2,
                "findings_count": 2,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "CRITICAL",
                        "category": "containerization",
                        "title": "Missing container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    },
                    {
                        "id": "h2",
                        "rule_id": "W002",
                        "severity": "WARNING",
                        "category": "resources",
                        "title": "Missing CPU",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h2"},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # Simulate interactive user pressing Enter (accepting default 'all')
    result = runner.invoke(app, ["fix", str(tmp_path), "--dry-run"], input="all\n")
    assert result.exit_code == 0
    assert "Rules" in result.output
    assert "W001" in result.output
    assert "W002" in result.output


def test_fix_enhance_without_key_degrades_gracefully(tmp_path: Path) -> None:
    """Verify --enhance prints warning and continues offline fix when API key is missing."""
    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "WARNING",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with patch("workflow_clinic.cli.check_model_api_key", return_value=False):
        result = runner.invoke(app, ["fix", str(tmp_path), "--enhance", "--dry-run"])

    assert result.exit_code == 0
    assert "No API key found" in result.output
    assert "W001" in result.output


def test_fix_enhance_with_key_merges_ai_findings(tmp_path: Path) -> None:
    """Verify --enhance invokes AICriticAgent and includes AI findings in fix session."""
    workflow_file = tmp_path / "main.nf"
    workflow_file.write_text("process STEP { script: 'echo 1' }\n", encoding="utf-8")

    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "WARNING",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    mock_ai_finding = Finding(
        rule_id="AI001",
        severity="INFO",
        category="ai_audit",
        title="AI optimization",
        message="AI recommended improvement",
        file_path="main.nf",
    )

    mock_llm_response = MagicMock()
    mock_llm_response.choices = [
        MagicMock(
            message=MagicMock(
                content="process STEP {\n    script: 'echo optimized'\n}\n"
            )
        )
    ]

    mock_bundle = WorkflowBundle(
        metadata=WorkflowMetadata(name="test_pipeline"),
        tasks=[],
    )
    mock_parser = MagicMock()
    mock_parser.parse.return_value = mock_bundle

    FixerRegistry.register(AIFixer)

    with (
        patch("workflow_clinic.cli.check_model_api_key", return_value=True),
        patch(
            "workflow_clinic.doctor.fixers.ai.check_model_api_key", return_value=True
        ),
        patch(
            "workflow_clinic.doctor.fixers.ai.litellm.completion",
            return_value=mock_llm_response,
        ),
        patch(
            "workflow_clinic.cli.ParserRegistry.detect_parser",
            return_value="nextflow",
        ),
        patch(
            "workflow_clinic.cli.ParserRegistry.get_parser",
            return_value=mock_parser,
        ),
        patch(
            "workflow_clinic.cli.AICriticAgent.audit_workflow",
            return_value=[mock_ai_finding],
        ),
    ):
        result = runner.invoke(app, ["fix", str(tmp_path), "--enhance", "--dry-run"])

    assert result.exit_code == 0
    assert "AI Critic identified 1 enhancement finding(s)" in result.output
    assert "AI001" in result.output


def test_fix_ai_only_missing_key_fails(tmp_path: Path) -> None:
    """Verify --ai-only fails with exit code 1 when no LLM API key is found."""
    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "WARNING",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with patch("workflow_clinic.cli.check_model_api_key", return_value=False):
        result = runner.invoke(app, ["fix", str(tmp_path), "--ai-only"])

    assert result.exit_code == 1
    assert "'--ai-only' requires an active LLM API key" in result.output


def test_fix_ai_only_with_key_routes_to_ai(tmp_path: Path) -> None:
    """Verify --ai-only forces Layer 3 AI fixer for all findings."""
    workflow_file = tmp_path / "main.nf"
    workflow_file.write_text("process FASTQC { script: 'echo 1' }\n", encoding="utf-8")

    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": "h1",
                        "rule_id": "W001",
                        "severity": "WARNING",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "fingerprint": {"hash": "h1"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    mock_llm_response = MagicMock()
    mock_llm_response.choices = [
        MagicMock(
            message=MagicMock(
                content="process FASTQC {\n    container 'quay.io/biocontainers/fastqc:0.12.1'\n    script: 'echo 1'\n}\n"
            )
        )
    ]

    mock_bundle = WorkflowBundle(
        metadata=WorkflowMetadata(name="test_pipeline"),
        tasks=[],
    )
    mock_parser = MagicMock()
    mock_parser.parse.return_value = mock_bundle

    FixerRegistry.register(AIFixer)

    with (
        patch("workflow_clinic.cli.check_model_api_key", return_value=True),
        patch(
            "workflow_clinic.doctor.fixers.ai.check_model_api_key", return_value=True
        ),
        patch(
            "workflow_clinic.doctor.fixers.ai.litellm.completion",
            return_value=mock_llm_response,
        ),
        patch(
            "workflow_clinic.cli.ParserRegistry.detect_parser",
            return_value="nextflow",
        ),
        patch(
            "workflow_clinic.cli.ParserRegistry.get_parser",
            return_value=mock_parser,
        ),
        patch(
            "workflow_clinic.cli.AICriticAgent.audit_workflow",
            return_value=[],
        ),
    ):
        result = runner.invoke(app, ["fix", str(tmp_path), "--ai-only", "--dry-run"])

    assert result.exit_code == 0
    assert "LAYER3_AI" in result.output
