"""Unit tests for remote repository scanning in workflow-clinic examine CLI."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from workflow_clinic.cli import app
from workflow_clinic.exceptions import ParserError
from workflow_clinic.utils.git import clone_remote_repo, is_remote_url

runner = CliRunner()


def test_is_remote_url_detection() -> None:
    """Verify URL detection logic for remote Git and HTTP targets."""
    assert is_remote_url("https://github.com/org/repo") is True
    assert is_remote_url("http://gitlab.com/user/workflow.git") is True
    assert is_remote_url("git@github.com:org/repo.git") is True
    assert is_remote_url("git://github.com/org/repo") is True
    assert is_remote_url("https://github.com/org/repo.git") is True

    # Local file paths
    assert is_remote_url(".") is False
    assert is_remote_url("main.nf") is False
    assert is_remote_url("/abs/path/to/workflow") is False
    assert is_remote_url("./relative/path") is False


@patch("workflow_clinic.utils.git.subprocess.run")
def test_clone_remote_repo_success(mock_run: MagicMock, tmp_path: Path) -> None:
    """Verify shallow git clone invocation."""
    mock_run.return_value = MagicMock(returncode=0, stdout="Cloning...", stderr="")
    dest = tmp_path / "cloned_repo"

    result = clone_remote_repo("https://github.com/example/repo", dest)

    assert result == dest
    mock_run.assert_called_once_with(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "https://github.com/example/repo",
            str(dest),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@patch("workflow_clinic.utils.git.subprocess.run")
def test_clone_remote_repo_failure_raises_parser_error(
    mock_run: MagicMock, tmp_path: Path
) -> None:
    """Verify clone failure raises ParserError with stderr details."""
    mock_run.return_value = MagicMock(
        returncode=128, stdout="", stderr="Repository not found"
    )
    dest = tmp_path / "cloned_repo"

    with pytest.raises(ParserError, match="Failed to clone remote repository"):
        clone_remote_repo("https://github.com/invalid/repo", dest)


@patch("workflow_clinic.utils.git.subprocess.run")
def test_clone_remote_repo_git_not_found(mock_run: MagicMock, tmp_path: Path) -> None:
    """Verify missing git executable raises friendly ParserError."""
    mock_run.side_effect = FileNotFoundError()
    dest = tmp_path / "cloned_repo"

    with pytest.raises(ParserError, match="Git executable not found in PATH"):
        clone_remote_repo("https://github.com/example/repo", dest)


@patch("workflow_clinic.cli.clone_remote_repo")
@patch("workflow_clinic.cli.ParserRegistry.detect_parser")
@patch("workflow_clinic.cli.ParserRegistry.get_parser")
def test_examine_remote_repository_cli_flow(
    mock_get_parser: MagicMock,
    mock_detect_parser: MagicMock,
    mock_clone: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify full CLI flow for scanning a remote repository URL."""
    # Setup mock parser & bundle
    mock_bundle = MagicMock()
    mock_bundle.metadata.name = "Remote Workflow"
    mock_task = MagicMock()
    mock_task.id = "FASTQC"
    mock_task.name = "FASTQC"
    mock_task.command = None
    mock_task.file_path = "main.nf"
    mock_task.line_number = 42
    mock_bundle.tasks = [mock_task]

    mock_parser_inst = MagicMock()
    mock_parser_inst.parse.return_value = mock_bundle
    mock_get_parser.return_value = mock_parser_inst

    mock_detect_parser.return_value = "nextflow"
    mock_clone.side_effect = lambda _url, dest: dest

    out_file = tmp_path / "diagnosis.json"
    remote_url = "https://github.com/ga4gh/sample-workflow"

    result = runner.invoke(app, ["examine", remote_url, "-o", str(out_file)])

    assert result.exit_code == 0
    assert "Scanning workflow" in result.output
    mock_clone.assert_called_once()
    mock_parser_inst.parse.assert_called_once()
    assert out_file.exists()


@patch("workflow_clinic.cli.clone_remote_repo")
@patch("workflow_clinic.cli.ParserRegistry.detect_parser")
@patch("workflow_clinic.cli.ParserRegistry.get_parser")
def test_remote_examine_stable_fingerprint_across_runs(
    mock_get_parser: MagicMock,
    mock_detect_parser: MagicMock,
    mock_clone: MagicMock,
    tmp_path: Path,
) -> None:
    """Verify scanning the same remote URL produces identical fingerprint hashes across different temp directory paths."""
    mock_bundle = MagicMock()
    mock_bundle.metadata.name = "Remote Workflow"
    mock_task = MagicMock()
    mock_task.id = "FASTQC"
    mock_task.name = "FASTQC"
    mock_task.command = None
    mock_task.file_path = "main.nf"
    mock_task.line_number = 42
    mock_bundle.tasks = [mock_task]

    mock_parser_inst = MagicMock()
    mock_parser_inst.parse.return_value = mock_bundle
    mock_get_parser.return_value = mock_parser_inst
    mock_detect_parser.return_value = "nextflow"

    dir1 = tmp_path / "temp_run_1"
    dir2 = tmp_path / "temp_run_2"
    dir1.mkdir()
    dir2.mkdir()

    mock_clone.side_effect = [dir1, dir2]

    out_file1 = tmp_path / "diag1.json"
    out_file2 = tmp_path / "diag2.json"
    remote_url = "https://github.com/ga4gh/sample-workflow"

    res1 = runner.invoke(app, ["examine", remote_url, "-o", str(out_file1)])
    res2 = runner.invoke(app, ["examine", remote_url, "-o", str(out_file2)])

    assert res1.exit_code == 0
    assert res2.exit_code == 0

    report1 = json.loads(out_file1.read_text(encoding="utf-8"))
    report2 = json.loads(out_file2.read_text(encoding="utf-8"))

    assert report1["findings"] == report2["findings"]
