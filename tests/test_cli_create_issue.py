"""Unit tests for create-issue CLI command and selection parser."""

import json
from pathlib import Path

from typer.testing import CliRunner

from workflow_clinic.cli import app, parse_selection

runner = CliRunner()


def test_parse_selection_utility() -> None:
    """Verify parse_selection handles all selection string input variations."""
    assert parse_selection("all", 5) == [0, 1, 2, 3, 4]
    assert parse_selection("", 5) == [0, 1, 2, 3, 4]
    assert parse_selection("a", 5) == [0, 1, 2, 3, 4]
    assert parse_selection("1", 5) == [0]
    assert parse_selection("1, 3", 5) == [0, 2]
    assert parse_selection("1-3", 5) == [0, 1, 2]
    assert parse_selection("1-3, 5", 5) == [0, 1, 2, 4]
    # Ignores out-of-bounds indices and invalid values gracefully
    assert parse_selection("0, 99, abc, 2", 5) == [1]


def test_create_issue_missing_diagnosis_file(tmp_path: Path) -> None:
    """Verify error message and exit code 1 when diagnosis.json is missing."""
    result = runner.invoke(app, ["create-issue", str(tmp_path)])
    assert result.exit_code == 1
    assert "Could not find 'diagnosis.json'" in result.output
    assert "Run workflow-clinic examine" in result.output


def test_create_issue_empty_report(tmp_path: Path) -> None:
    """Verify exit code 0 and clean message when diagnosis report has zero findings."""
    diag_file = tmp_path / "diagnosis.json"
    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "clean_pipeline",
                "tasks_count": 3,
                "findings_count": 0,
                "findings": [],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["create-issue", str(tmp_path)])
    assert result.exit_code == 0
    assert "No actionable findings to report!" in result.output


def test_create_issue_non_interactive_all(tmp_path: Path) -> None:
    """Verify --all / -y flag automatically selects all issues and exports issue.md."""
    diag_file = tmp_path / "diagnosis.json"
    out_file = tmp_path / "custom_issue.md"

    fp1_hash = "1111111111111111111111111111111111111111111111111111111111111111"
    fp2_hash = "2222222222222222222222222222222222222222222222222222222222222222"

    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 2,
                "findings_count": 2,
                "findings": [
                    {
                        "id": fp1_hash,
                        "rule_id": "W001",
                        "severity": "CRITICAL",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "line_number": 12,
                        "fingerprint": {"hash": fp1_hash},
                    },
                    {
                        "id": fp2_hash,
                        "rule_id": "W002",
                        "severity": "HIGH",
                        "category": "resources",
                        "title": "Missing memory limit",
                        "file_path": "main.nf",
                        "line_number": 25,
                        "fingerprint": {"hash": fp2_hash},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app, ["create-issue", str(tmp_path), "--all", "-o", str(out_file)]
    )
    assert result.exit_code == 0
    assert "Exported 2 issue group(s)" in result.output
    assert out_file.exists()

    content = out_file.read_text(encoding="utf-8")
    assert "Containerization" in content
    assert "Resources" in content
    assert fp1_hash in content
    assert fp2_hash in content


def test_create_issue_dry_run(tmp_path: Path) -> None:
    """Verify --dry-run prints markdown payload to stdout without writing file."""
    diag_file = tmp_path / "diagnosis.json"
    out_file = tmp_path / "should_not_exist.md"

    fp1_hash = "1111111111111111111111111111111111111111111111111111111111111111"

    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": fp1_hash,
                        "rule_id": "W001",
                        "severity": "CRITICAL",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "line_number": 12,
                        "fingerprint": {"hash": fp1_hash},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        [
            "create-issue",
            str(tmp_path),
            "--all",
            "--dry-run",
            "-o",
            str(out_file),
        ],
    )
    assert result.exit_code == 0
    assert "Issue Markdown Payload (Dry Run)" in result.output
    assert not out_file.exists()


def test_create_issue_preview_flag(tmp_path: Path) -> None:
    """Verify --preview flag renders terminal preview and writes file."""
    diag_file = tmp_path / "diagnosis.json"
    out_file = tmp_path / "preview_issue.md"

    fp1_hash = "1111111111111111111111111111111111111111111111111111111111111111"

    diag_file.write_text(
        json.dumps(
            {
                "workflow_name": "test_pipeline",
                "tasks_count": 1,
                "findings_count": 1,
                "findings": [
                    {
                        "id": fp1_hash,
                        "rule_id": "W001",
                        "severity": "CRITICAL",
                        "category": "containerization",
                        "title": "Unpinned container",
                        "file_path": "main.nf",
                        "line_number": 12,
                        "fingerprint": {"hash": fp1_hash},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["create-issue", str(tmp_path), "--all", "--preview", "-o", str(out_file)],
    )
    assert result.exit_code == 0
    assert "Issue Markdown Preview" in result.output
    assert out_file.exists()


def test_create_issue_real_examine_output(tmp_path: Path) -> None:
    """Verify create-issue successfully parses real diagnosis.json schema written by examine."""
    diag_file = tmp_path / "diagnosis.json"
    out_file = tmp_path / "issue.md"

    # Write diagnosis.json matching exact schema produced by workflow-clinic examine
    raw_examine_data = {
        "workflow_name": "sample_workflow",
        "tasks_count": 1,
        "findings_count": 1,
        "findings": [
            {
                "id": "1111111111111111111111111111111111111111111111111111111111111111",
                "rule_id": "W001",
                "severity": "warning",
                "message": "Unpinned container tag in process FASTQC",
                "location": "modules/fastqc.nf",
                "task_id": "FASTQC",
                "fingerprint": {
                    "hash": "1111111111111111111111111111111111111111111111111111111111111111"
                },
            }
        ],
    }
    diag_file.write_text(json.dumps(raw_examine_data), encoding="utf-8")

    # Run create-issue against real examine JSON schema
    create_result = runner.invoke(
        app, ["create-issue", str(tmp_path), "--all", "-o", str(out_file)]
    )
    assert create_result.exit_code == 0
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "Workflow Diagnostic Finding" in content
    assert "Containerization" in content
