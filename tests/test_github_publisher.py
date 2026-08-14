"""Unit and integration tests for PyGitHub publisher and online issue creation CLI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003
from unittest.mock import MagicMock, patch

import pytest
from github import (
    BadCredentialsException,
    GithubException,
    RateLimitExceededException,
    UnknownObjectException,
)
from typer.testing import CliRunner

from workflow_clinic.cli import app
from workflow_clinic.models.diagnosis import DiagnosisReport, Finding
from workflow_clinic.reporting import (
    GeneratedIssue,
    GitHubAPIError,
    GitHubAuthError,
    GitHubPublisher,
    GitHubRepoNotFoundError,
    PublishedIssueInfo,
    _mask_token,
)

runner = CliRunner()


def test_mask_token_masks_pat_correctly() -> None:
    """Verify _mask_token sanitizes sensitive PAT tokens."""
    assert _mask_token("ghp_1234567890abcdef") == "ghp_...cdef"
    assert _mask_token("short") == "****"
    assert _mask_token("") == "****"


@patch("workflow_clinic.reporting.github_publisher.Github")
def test_github_publisher_auth_success(mock_github_cls: MagicMock) -> None:
    """Verify PyGitHub client initialization and lazy repo retrieval."""
    mock_gh_instance = MagicMock()
    mock_repo = MagicMock()
    mock_gh_instance.get_repo.return_value = mock_repo
    mock_github_cls.return_value = mock_gh_instance

    publisher = GitHubPublisher(token="ghp_test123456", repository="ga4gh/test-repo")
    repo = publisher._get_repo()

    assert repo == mock_repo
    mock_gh_instance.get_repo.assert_called_once_with("ga4gh/test-repo")


def test_github_publisher_invalid_args() -> None:
    """Verify constructor raises on empty token or invalid repository format."""
    with pytest.raises(GitHubAuthError, match="Token is required"):
        GitHubPublisher(token="", repository="owner/repo")

    with pytest.raises(GitHubRepoNotFoundError, match="Expected 'owner/repo'"):
        GitHubPublisher(token="ghp_test1234", repository="invalid_repo_string")


@patch("workflow_clinic.reporting.github_publisher.Github")
def test_github_publisher_bad_credentials_raises_auth_error(
    mock_github_cls: MagicMock,
) -> None:
    """Verify 401 BadCredentialsException maps to GitHubAuthError without token leakage."""
    mock_gh_instance = MagicMock()
    mock_gh_instance.get_repo.side_effect = BadCredentialsException(
        status=401, data={"message": "Bad credentials"}, headers={}
    )
    mock_github_cls.return_value = mock_gh_instance

    secret_token = "ghp_SUPER_SECRET_TOKEN_9999"  # noqa: S105
    publisher = GitHubPublisher(token=secret_token, repository="owner/repo")

    with pytest.raises(GitHubAuthError) as exc_info:
        publisher._get_repo()

    assert "Invalid GitHub Personal Access Token provided" in str(exc_info.value)
    assert secret_token not in str(exc_info.value)


@patch("workflow_clinic.reporting.github_publisher.Github")
def test_github_publisher_repo_not_found_raises_error(
    mock_github_cls: MagicMock,
) -> None:
    """Verify 404 UnknownObjectException maps to GitHubRepoNotFoundError."""
    mock_gh_instance = MagicMock()
    mock_gh_instance.get_repo.side_effect = UnknownObjectException(
        status=404, data={"message": "Not Found"}, headers={}
    )
    mock_github_cls.return_value = mock_gh_instance

    publisher = GitHubPublisher(token="ghp_test123", repository="nonexistent/repo")

    with pytest.raises(GitHubRepoNotFoundError) as exc_info:
        publisher._get_repo()

    assert "Repository 'nonexistent/repo' not found or inaccessible" in str(
        exc_info.value
    )


@patch("workflow_clinic.reporting.github_publisher.Github")
def test_rate_limit_exceeded_gives_reset_time(mock_github_cls: MagicMock) -> None:
    """Verify 403 RateLimitExceededException extracts reset time in UTC format."""
    mock_gh_instance = MagicMock()
    mock_gh_instance.get_repo.side_effect = RateLimitExceededException(
        status=403, data={"message": "API rate limit exceeded"}, headers={}
    )

    reset_dt = datetime(2026, 8, 13, 14, 30, 0, tzinfo=UTC)
    mock_rate_limit = MagicMock()
    mock_rate_limit.core.reset = reset_dt
    mock_gh_instance.get_rate_limit.return_value = mock_rate_limit

    mock_github_cls.return_value = mock_gh_instance

    publisher = GitHubPublisher(token="ghp_test123", repository="owner/repo")

    with pytest.raises(GitHubAPIError) as exc_info:
        publisher._get_repo()

    assert "GitHub API rate limit exceeded" in str(exc_info.value)
    assert "14:30 UTC" in str(exc_info.value)


@patch("workflow_clinic.reporting.github_publisher.Github")
def test_ensure_label_race_condition_handled(mock_github_cls: MagicMock) -> None:
    """Verify race condition when label is created concurrently by another process."""
    mock_gh_instance = MagicMock()
    mock_repo = MagicMock()
    mock_label = MagicMock()
    mock_label.name = "workflow-clinic"

    # 1. get_label raises UnknownObjectException
    # 2. create_label raises GithubException (concurrent creation 422)
    # 3. second get_label succeeds
    mock_repo.get_label.side_effect = [
        UnknownObjectException(status=404, data={"message": "Not Found"}, headers={}),
        mock_label,
    ]
    mock_repo.create_label.side_effect = GithubException(
        status=422, data={"message": "Already exists"}, headers={}
    )

    mock_gh_instance.get_repo.return_value = mock_repo
    mock_github_cls.return_value = mock_gh_instance

    publisher = GitHubPublisher(token="ghp_test123", repository="owner/repo")
    lbl = publisher.ensure_label("workflow-clinic")

    assert lbl == mock_label
    assert mock_repo.get_label.call_count == 2
    mock_repo.create_label.assert_called_once()


@patch("workflow_clinic.reporting.github_publisher.Github")
def test_github_publisher_fetch_active_fingerprints(
    mock_github_cls: MagicMock,
) -> None:
    """Verify active fingerprint hash extraction from open GitHub issue bodies."""
    mock_gh_instance = MagicMock()
    mock_repo = MagicMock()

    issue1 = MagicMock()
    issue1.body = "Some issue content\n<!-- workflow-clinic:fingerprint:a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890 -->"

    issue2 = MagicMock()
    issue2.body = "Other issue\n<!-- workflow-clinic:fingerprint:11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff -->"

    mock_repo.get_issues.return_value = [issue1, issue2]
    mock_gh_instance.get_repo.return_value = mock_repo
    mock_github_cls.return_value = mock_gh_instance

    publisher = GitHubPublisher(token="ghp_test123", repository="owner/repo")
    fps = publisher.fetch_active_fingerprints()

    assert len(fps) == 2
    assert "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890" in fps
    assert "11223344556677889900aabbccddeeff11223344556677889900aabbccddeeff" in fps


@patch("workflow_clinic.reporting.github_publisher.Github")
def test_github_publisher_publish_issue_success(
    mock_github_cls: MagicMock,
) -> None:
    """Verify successful issue publishing returning PublishedIssueInfo."""
    mock_gh_instance = MagicMock()
    mock_repo = MagicMock()
    mock_label = MagicMock()
    mock_created_issue = MagicMock()
    mock_created_issue.number = 42
    mock_created_issue.title = "[Workflow Clinic] Diagnostic Finding: Containerization"
    mock_created_issue.html_url = "https://github.com/owner/repo/issues/42"

    mock_repo.get_label.return_value = mock_label
    mock_repo.create_issue.return_value = mock_created_issue
    mock_gh_instance.get_repo.return_value = mock_repo
    mock_github_cls.return_value = mock_gh_instance

    publisher = GitHubPublisher(token="ghp_test123", repository="owner/repo")
    issue_group = GeneratedIssue(
        category="containerization",
        title="Containerization",
        severity="ERROR",
        fingerprints=["hash1", "hash2"],
        body="## Containerization Issues\n\n- File: main.nf",
    )

    res = publisher.publish_issue(issue_group)

    assert isinstance(res, PublishedIssueInfo)
    assert res.number == 42
    assert res.url == "https://github.com/owner/repo/issues/42"
    assert res.category == "containerization"
    mock_repo.create_issue.assert_called_once()


def test_cli_create_issue_dry_run_does_not_publish(tmp_path: Path) -> None:
    """Verify --dry-run prints markdown payload to stdout and makes zero PyGitHub API calls."""
    diag_file = tmp_path / "diagnosis.json"
    report = DiagnosisReport(
        workflow_name="Test Workflow",
        tasks_count=1,
        findings_count=1,
        findings=[
            Finding(
                id="f1",
                rule_id="W001",
                severity="ERROR",
                category="containerization",
                title="No Container",
                file_path="main.nf",
            )
        ],
    )
    diag_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    with patch("workflow_clinic.cli.GitHubPublisher") as mock_pub_cls:
        mock_instance = MagicMock()
        mock_pub_cls.return_value = mock_instance
        result = runner.invoke(
            app,
            [
                "create-issue",
                str(diag_file),
                "--dry-run",
                "--token",
                "ghp_test123",
                "--repo",
                "owner/repo",
            ],
        )

        assert result.exit_code == 0
        assert "Issue Markdown Payload (Dry Run)" in result.stdout
        # Verify publish_issue was never called in dry-run mode
        mock_instance.publish_issue.assert_not_called()


def test_token_never_appears_in_error_messages(tmp_path: Path) -> None:
    """Verify sensitive PAT token string never appears in CLI error stdout or stderr."""
    diag_file = tmp_path / "diagnosis.json"
    report = DiagnosisReport(
        workflow_name="Test Workflow",
        tasks_count=1,
        findings_count=1,
        findings=[
            Finding(
                id="f1",
                rule_id="W001",
                severity="ERROR",
                category="containerization",
                title="No Container",
                file_path="main.nf",
            )
        ],
    )
    diag_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    secret_token = "ghp_SUPER_SECRET_TOKEN_9999"  # noqa: S105

    with patch("workflow_clinic.cli.GitHubPublisher") as mock_pub_cls:
        mock_pub_instance = MagicMock()
        mock_pub_instance.fetch_active_fingerprints.side_effect = GitHubAuthError(
            "Invalid GitHub Personal Access Token provided."
        )
        mock_pub_cls.return_value = mock_pub_instance

        result = runner.invoke(
            app,
            [
                "create-issue",
                str(diag_file),
                "--token",
                secret_token,
                "--repo",
                "owner/repo",
            ],
        )

        assert result.exit_code == 1
        assert "GitHub Authentication/API Error" in result.output
        assert secret_token not in result.output


def test_cli_create_issue_online_publishing_flow(tmp_path: Path) -> None:
    """Verify CLI create-issue online publishing flow with PAT and repo arguments."""
    diag_file = tmp_path / "diagnosis.json"
    report = DiagnosisReport(
        workflow_name="Test Workflow",
        tasks_count=1,
        findings_count=1,
        findings=[
            Finding(
                id="f1",
                rule_id="W001",
                severity="ERROR",
                category="containerization",
                title="No Container",
                file_path="main.nf",
            )
        ],
    )
    diag_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    with patch("workflow_clinic.cli.GitHubPublisher") as mock_pub_cls:
        mock_pub_instance = MagicMock()
        mock_pub_instance.repository = "ga4gh/demo-repo"
        mock_pub_instance.fetch_active_fingerprints.return_value = set()
        mock_pub_instance.publish_issue.return_value = PublishedIssueInfo(
            number=101,
            title="[Workflow Clinic] Diagnostic Finding: Containerization",
            url="https://github.com/owner/repo/issues/101",
            category="containerization",
        )
        mock_pub_cls.return_value = mock_pub_instance

        result = runner.invoke(
            app,
            [
                "create-issue",
                str(diag_file),
                "--all",
                "--token",
                "ghp_valid_token",
                "--repo",
                "ga4gh/demo-repo",
            ],
        )

        assert result.exit_code == 0
        assert "Successfully published 1 issue(s) to GitHub repository" in result.stdout
        assert "#101" in result.stdout
        assert "https://github.com/owner" in result.stdout
        mock_pub_instance.publish_issue.assert_called_once()


def test_cli_create_issue_local_fallback_when_no_credentials(tmp_path: Path) -> None:
    """Verify create-issue falls back to local issue.md export when PAT/Repo are missing or --local is passed."""
    diag_file = tmp_path / "diagnosis.json"
    out_file = tmp_path / "custom_issue.md"

    report = DiagnosisReport(
        workflow_name="Test Workflow",
        tasks_count=1,
        findings_count=1,
        findings=[
            Finding(
                id="f1",
                rule_id="W001",
                severity="ERROR",
                category="containerization",
                title="No Container",
                file_path="main.nf",
            )
        ],
    )
    diag_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    result = runner.invoke(
        app,
        [
            "create-issue",
            str(diag_file),
            "--local",
            "--all",
            "--output",
            str(out_file),
        ],
    )

    assert result.exit_code == 0
    assert out_file.exists()
    assert "Exported 1 issue group(s)" in result.stdout
    assert "Containerization" in out_file.read_text(encoding="utf-8")


def test_all_findings_already_open_shows_clean_message(tmp_path: Path) -> None:
    """Verify that when all findings are already open on GitHub, a clean message is displayed and exit 0."""
    diag_file = tmp_path / "diagnosis.json"
    fp_hash = "a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890a1b2c3d4e5f67890"
    report = DiagnosisReport(
        workflow_name="Test Workflow",
        tasks_count=1,
        findings_count=1,
        findings=[
            Finding(
                id=fp_hash,
                rule_id="W001",
                severity="ERROR",
                category="containerization",
                title="No Container",
                file_path="main.nf",
            )
        ],
    )
    diag_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")

    with patch("workflow_clinic.cli.GitHubPublisher") as mock_pub_cls:
        mock_pub_instance = MagicMock()
        mock_pub_instance.repository = "owner/repo"
        mock_pub_instance.fetch_active_fingerprints.return_value = {fp_hash}
        mock_pub_cls.return_value = mock_pub_instance

        result = runner.invoke(
            app,
            [
                "create-issue",
                str(diag_file),
                "--token",
                "ghp_test123",
                "--repo",
                "owner/repo",
            ],
        )

        assert result.exit_code == 0
        assert "No new actionable findings to report!" in result.stdout
        mock_pub_instance.publish_issue.assert_not_called()
