"""Integration and unit tests for workflow-clinic fix CLI command."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workflow_clinic.cli import app
from workflow_clinic.doctor.base import BaseFixer, FixerRegistry
from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.models.fix import FixProposal, FixStrategyLayer
from workflow_clinic.models.workflow_bundle import WorkflowBundle

runner = CliRunner()


class DummyCliFixer(BaseFixer):
    """Dummy fixer for testing CLI fix command workflow."""

    rule_id = "W001"
    strategy_layer = FixStrategyLayer.LAYER1_AST

    def generate_proposal(
        self,
        finding: Finding,
        bundle: WorkflowBundle | None = None,  # noqa: ARG002
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
    FixerRegistry.clear()
    FixerRegistry.register(DummyCliFixer)
    yield
    FixerRegistry.clear()


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
