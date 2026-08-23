"""Unit and integration tests for GitHub Pull Request publishing and remote issue resolution."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from github import (
    GithubException,
    UnknownObjectException,
)
from typer.testing import CliRunner

import workflow_clinic.doctor.fixers  # noqa: F401
from workflow_clinic.cli import app
from workflow_clinic.doctor.base import BaseFixer, FixerRegistry
from workflow_clinic.models.diagnosis import DiagnosisReport, Finding, Fingerprint
from workflow_clinic.models.fix import (
    AppliedProposal,
    ApplyOutcome,
    FixProposal,
    FixSession,
    FixStrategyLayer,
)
from workflow_clinic.reporting.github_publisher import (
    MAX_PR_BODY_CHARS,
    GitHubPublisher,
    GitHubPublisherError,
    GitHubRepoNotFoundError,
    PublishedPullRequestInfo,
)
from workflow_clinic.rules import Severity

runner = CliRunner()


class DummyCliFixer(BaseFixer):
    """Dummy fixer for testing CLI fix command workflow."""

    rule_id = "W001"
    strategy_layer = FixStrategyLayer.LAYER1_AST

    def generate_proposal(
        self,
        finding: Finding,
        bundle: Any = None,  # noqa: ARG002
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
def _clear_env_and_registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Isolate tests from GitHub environment variables and clean fixer registry."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    original_fixers = dict(FixerRegistry._fixers)
    FixerRegistry.clear()
    FixerRegistry.register(DummyCliFixer)
    yield
    FixerRegistry.clear()
    FixerRegistry._fixers.update(original_fixers)


@pytest.fixture
def mock_fix_session(tmp_path: Path) -> FixSession:
    """Create a populated FixSession fixture for testing."""
    test_file = tmp_path / "main.nf"
    test_file.write_text(
        "process FASTQC { container 'fastqc:latest' }\n", encoding="utf-8"
    )

    proposal = FixProposal(
        finding_id="f1",
        rule_id="W001",
        category="portability",
        target_file="main.nf",
        original_snippet="container 'fastqc:latest'",
        proposed_snippet="container 'fastqc:v0.11.9'",
        explanation="Pin container version tag",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
        line_number=1,
    )

    applied = AppliedProposal(
        proposal=proposal,
        applied=True,
        outcome=ApplyOutcome(
            success=True,
            modified_file=Path("main.nf"),
            verification_passed=True,
        ),
    )

    return FixSession(
        session_id="session12345678",
        source=str(test_file),
        proposals=[proposal],
        applied_proposals=[applied],
        completed_at=datetime.now(UTC),
    )


def test_published_pr_info_model() -> None:
    """Verify PublishedPullRequestInfo serialization."""
    info = PublishedPullRequestInfo(
        number=42,
        title="[Workflow Clinic] Fix issues",
        url="https://github.com/owner/repo/pull/42",
        head_branch="workflow-clinic/fix-session1",
        base_branch="main",
        applied_count=1,
    )
    assert info.number == 42
    assert info.head_branch == "workflow-clinic/fix-session1"
    assert info.applied_count == 1


def test_publish_pr_skips_when_no_fixes_applied(tmp_path: Path) -> None:
    """Verify publish_pull_request raises error when session has 0 applied fixes."""
    empty_session = FixSession(
        session_id="empty123", source="main.nf", proposals=[], applied_proposals=[]
    )
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")

    with pytest.raises(GitHubPublisherError, match="0 applied fixes"):
        publisher.publish_pull_request(empty_session, root_dir=tmp_path)


def test_publish_pr_success(tmp_path: Path, mock_fix_session: FixSession) -> None:
    """Verify full end-to-end PR publishing flow via PyGithub mock."""
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"

    mock_base_ref = MagicMock()
    mock_base_ref.commit.sha = "base_sha_123"
    mock_repo.get_branch.return_value = mock_base_ref

    mock_existing_file = MagicMock()
    mock_existing_file.sha = "file_sha_456"
    mock_existing_file.decoded_content = (
        b"process FASTQC { container 'fastqc:latest' }\n"
    )
    mock_repo.get_contents.return_value = mock_existing_file

    mock_created_pr = MagicMock()
    mock_created_pr.number = 101
    mock_created_pr.title = "[Workflow Clinic] Automated Remediation — 1 issue(s) fixed"
    mock_created_pr.html_url = "https://github.com/owner/repo/pull/101"
    mock_repo.create_pull.return_value = mock_created_pr

    with patch.object(publisher, "_get_repo", return_value=mock_repo):
        pr_info = publisher.publish_pull_request(
            session=mock_fix_session,
            root_dir=tmp_path,
            linked_issues=[42, 43],
        )

    assert pr_info.number == 101
    assert pr_info.url == "https://github.com/owner/repo/pull/101"
    assert pr_info.head_branch.startswith("workflow-clinic/fix-")
    assert pr_info.base_branch == "main"
    assert pr_info.applied_count == 1

    # Verify branch created
    mock_repo.create_git_ref.assert_called_once()
    # Verify file updated with existing SHA
    mock_repo.update_file.assert_called_once()
    # Verify PR created with Closes references
    mock_repo.create_pull.assert_called_once()
    call_kwargs = mock_repo.create_pull.call_args.kwargs
    assert "Closes #42, Closes #43" in call_kwargs["body"]
    assert "W001" in call_kwargs["body"]


def test_publish_pr_create_vs_update_file_logic() -> None:
    """Verify _commit_file uses create_file for new files and update_file for existing."""
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")
    mock_repo = MagicMock()

    # Case 1: Existing file -> update_file
    mock_existing = MagicMock()
    mock_existing.sha = "sha_abc"
    mock_repo.get_contents.return_value = mock_existing
    publisher._commit_file(mock_repo, "branch1", "existing.nf", "content", "msg")
    mock_repo.update_file.assert_called_once_with(
        path="existing.nf",
        message="msg",
        content="content",
        sha="sha_abc",
        branch="branch1",
    )

    # Case 2: 404 UnknownObjectException -> create_file
    mock_repo.reset_mock()
    mock_repo.get_contents.side_effect = UnknownObjectException(404, "Not Found", {})
    publisher._commit_file(mock_repo, "branch1", "new.nf", "content", "msg")
    mock_repo.create_file.assert_called_once_with(
        path="new.nf", message="msg", content="content", branch="branch1"
    )


def test_publish_pr_branch_collision_retry(
    tmp_path: Path, mock_fix_session: FixSession
) -> None:
    """Verify branch creation retries with extended suffix when HTTP 422 occurs."""
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")

    mock_repo = MagicMock()
    mock_repo.default_branch = "main"
    mock_base_ref = MagicMock()
    mock_base_ref.commit.sha = "base_sha_123"
    mock_repo.get_branch.return_value = mock_base_ref

    mock_existing = MagicMock()
    mock_existing.sha = "file_sha"
    mock_existing.decoded_content = b"process FASTQC { container 'fastqc:latest' }\n"
    mock_repo.get_contents.return_value = mock_existing

    mock_created_pr = MagicMock()
    mock_created_pr.number = 102
    mock_created_pr.title = "Test PR"
    mock_created_pr.html_url = "https://github.com/owner/repo/pull/102"
    mock_repo.create_pull.return_value = mock_created_pr

    # First call raises 422, second call succeeds
    mock_repo.create_git_ref.side_effect = [
        GithubException(422, {"message": "Reference already exists"}, {}),
        MagicMock(),
    ]

    with patch.object(publisher, "_get_repo", return_value=mock_repo):
        pr_info = publisher.publish_pull_request(
            session=mock_fix_session, root_dir=tmp_path
        )

    assert pr_info.number == 102
    assert mock_repo.create_git_ref.call_count == 2


def test_publish_pr_body_truncated_when_too_long() -> None:
    """Verify PR body is safely truncated at MAX_PR_BODY_CHARS."""
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")

    # Create a proposal with a huge snippet
    huge_snippet = "a" * (MAX_PR_BODY_CHARS + 5000)
    huge_prop = FixProposal(
        finding_id="f_huge",
        rule_id="W001",
        category="portability",
        target_file="huge.nf",
        original_snippet=huge_snippet,
        proposed_snippet=huge_snippet,
        explanation="Large file fix",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )

    applied = AppliedProposal(
        proposal=huge_prop,
        applied=True,
        outcome=ApplyOutcome(
            success=True,
            modified_file=Path("huge.nf"),
            verification_passed=True,
        ),
    )

    huge_session = FixSession(
        session_id="session_huge",
        source="huge.nf",
        proposals=[huge_prop],
        applied_proposals=[applied],
    )

    body = publisher.build_pr_body(huge_session)
    assert len(body) <= MAX_PR_BODY_CHARS + 200
    assert "...(truncated — please review commit diffs for full modifications)" in body


def test_fetch_issue_findings_success_and_errors() -> None:
    """Verify fetch_issue_findings extracts fingerprints and handles edge cases."""
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")
    mock_repo = MagicMock()

    # Success case
    mock_issue = MagicMock()
    mock_issue.body = "Finding report <!-- workflow-clinic:fingerprint:a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2 -->"
    mock_repo.get_issue.return_value = mock_issue

    with patch.object(publisher, "_get_repo", return_value=mock_repo):
        issue_num, fps = publisher.fetch_issue_findings(42)
        assert issue_num == 42
        assert "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2" in fps

    # Case: Issue has no fingerprints
    mock_issue.body = "Generic user bug report without fingerprints"
    with (
        patch.object(publisher, "_get_repo", return_value=mock_repo),
        pytest.raises(GitHubPublisherError, match="has no Workflow Clinic findings"),
    ):
        publisher.fetch_issue_findings(43)

    # Case: Issue not found (404)
    mock_repo.get_issue.side_effect = UnknownObjectException(404, "Not Found", {})
    with (
        patch.object(publisher, "_get_repo", return_value=mock_repo),
        pytest.raises(GitHubRepoNotFoundError, match="Issue #404 not found"),
    ):
        publisher.fetch_issue_findings(404)


def test_fetch_all_clinic_issues() -> None:
    """Verify fetch_all_clinic_issues scans open issues up to limit."""
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")
    mock_repo = MagicMock()

    mock_i1 = MagicMock()
    mock_i1.number = 10
    mock_i1.body = "<!-- workflow-clinic:fingerprint:" + "a" * 64 + " -->"

    mock_i2 = MagicMock()
    mock_i2.number = 20
    mock_i2.body = "<!-- workflow-clinic:fingerprint:" + "b" * 64 + " -->"

    mock_paginated = MagicMock()
    mock_paginated.__getitem__.side_effect = lambda s: [mock_i1, mock_i2][s]
    mock_repo.get_issues.return_value = mock_paginated

    with patch.object(publisher, "_get_repo", return_value=mock_repo):
        results = publisher.fetch_all_clinic_issues(max_scan=10)

    assert len(results) == 2
    assert results[0][0] == 10
    assert "a" * 64 in results[0][1]


def test_cli_fix_dry_run_pr_preview(tmp_path: Path) -> None:
    """Verify workflow-clinic fix --create-pr --dry-run renders PR preview without API write calls."""
    pipeline_file = tmp_path / "main.nf"
    pipeline_file.write_text(
        "process FASTQC { container 'fastqc:latest' }\n", encoding="utf-8"
    )

    diag_file = tmp_path / "diagnosis.json"
    finding = Finding(
        id="f1",
        rule_id="W001",
        category="portability",
        severity=Severity.ERROR,
        message="Unpinned container",
        file_path="main.nf",
        line_number=1,
        fingerprint=Fingerprint(hash="f" * 64),
    )
    report = DiagnosisReport(
        workflow_name="test_pipeline",
        workflow_path=str(pipeline_file),
        findings=[finding],
    )
    diag_file.write_text(report.model_dump_json(), encoding="utf-8")

    with patch(
        "workflow_clinic.cli.GitHubPublisher.fetch_active_fingerprints",
        return_value=set(),
    ):
        res = runner.invoke(
            app,
            [
                "fix",
                str(pipeline_file),
                "--dry-run",
                "--create-pr",
                "--all",
                "--repo",
                "owner/repo",
                "--token",
                "ghp_test_token_1234",
            ],
        )

    assert res.exit_code == 0
    assert "Pull Request Preview (Dry Run)" in res.stdout
    assert "Workflow Clinic Automated Remediation" in res.stdout


def test_cli_fix_create_pr_success(tmp_path: Path) -> None:
    """Verify workflow-clinic fix --create-pr --all creates PR on GitHub."""
    pipeline_file = tmp_path / "main.nf"
    pipeline_file.write_text(
        "process FASTQC { container 'fastqc:latest' }\n", encoding="utf-8"
    )

    diag_file = tmp_path / "diagnosis.json"
    finding = Finding(
        id="f1",
        rule_id="W001",
        category="portability",
        severity=Severity.ERROR,
        message="Unpinned container",
        file_path="main.nf",
        line_number=1,
        fingerprint=Fingerprint(hash="f" * 64),
    )
    report = DiagnosisReport(
        workflow_name="test_pipeline",
        workflow_path=str(pipeline_file),
        findings=[finding],
    )
    diag_file.write_text(report.model_dump_json(), encoding="utf-8")

    mock_pr_info = PublishedPullRequestInfo(
        number=105,
        title="[Workflow Clinic] Fix",
        url="https://github.com/owner/repo/pull/105",
        head_branch="workflow-clinic/fix-12345678",
        base_branch="main",
        applied_count=1,
    )

    with (
        patch(
            "workflow_clinic.cli.GitHubPublisher.publish_pull_request",
            return_value=mock_pr_info,
        ),
        patch(
            "workflow_clinic.cli.GitHubPublisher.fetch_active_fingerprints",
            return_value=set(),
        ),
        patch(
            "workflow_clinic.cli.GitHubPublisher.fetch_all_clinic_issues",
            return_value=[],
        ),
    ):
        res = runner.invoke(
            app,
            [
                "fix",
                str(pipeline_file),
                "--create-pr",
                "--all",
                "--repo",
                "owner/repo",
                "--token",
                "ghp_test_token_1234",
            ],
        )

    assert res.exit_code == 0
    assert "Created Pull Request #105" in res.stdout
    assert "https://github.com/owner/repo/pull/105" in res.stdout


def test_cli_create_pr_cmd_local_export(tmp_path: Path) -> None:
    """Verify standalone 'create-pr' command exports pr.md locally when no GitHub credentials or --local is used."""
    runner = CliRunner()
    pipeline_file = tmp_path / "main.nf"
    pipeline_file.write_text(
        "process FASTQC { container 'fastqc:latest' }\n", encoding="utf-8"
    )

    diag_file = tmp_path / "diagnosis.json"
    finding = Finding(
        id="f1",
        rule_id="W001",
        category="portability",
        severity=Severity.ERROR,
        message="Unpinned container",
        file_path="main.nf",
        line_number=1,
        fingerprint=Fingerprint(hash="f" * 64),
    )
    report = DiagnosisReport(
        workflow_name="test_pipeline",
        workflow_path=str(pipeline_file),
        findings=[finding],
    )
    diag_file.write_text(report.model_dump_json(), encoding="utf-8")

    # Run fix first to generate and save fixes.json
    fix_res = runner.invoke(app, ["fix", str(diag_file), "--all"])
    assert fix_res.exit_code == 0

    output_pr = tmp_path / "custom_pr.md"
    res = runner.invoke(
        app,
        [
            "create-pr",
            str(diag_file),
            "--all",
            "--local",
            "-o",
            str(output_pr),
        ],
    )

    assert res.exit_code == 0
    assert "Exported Pull Request summary" in res.stdout
    assert output_pr.exists()
    content = output_pr.read_text(encoding="utf-8")
    assert "[Workflow Clinic] Automated Remediation" in content
    assert "Applied Modifications" in content


def test_cli_create_pr_cmd_unfixed_findings_shows_warning(tmp_path: Path) -> None:
    """Verify standalone 'create-pr' exits with warning when findings are not yet fixed locally."""
    runner = CliRunner()
    pipeline_file = tmp_path / "main.nf"
    pipeline_file.write_text(
        "process FASTQC { container 'fastqc:latest' }\n", encoding="utf-8"
    )

    diag_file = tmp_path / "diagnosis.json"
    finding = Finding(
        id="f1",
        rule_id="W001",
        category="portability",
        severity=Severity.ERROR,
        message="Unpinned container",
        file_path="main.nf",
        line_number=1,
        fingerprint=Fingerprint(hash="f" * 64),
    )
    report = DiagnosisReport(
        workflow_name="test_pipeline",
        workflow_path=str(pipeline_file),
        findings=[finding],
    )
    diag_file.write_text(report.model_dump_json(), encoding="utf-8")

    res = runner.invoke(app, ["create-pr", str(diag_file), "--all", "--local"])
    assert res.exit_code == 0
    assert "The issue is not yet fixed" in res.stdout
    assert "W001" in res.stdout


def test_cli_create_pr_cmd_remote_publishing(tmp_path: Path) -> None:
    """Verify standalone 'create-pr' command publishes to GitHub with --token and --repo."""
    runner = CliRunner()
    pipeline_file = tmp_path / "main.nf"
    pipeline_file.write_text(
        "process FASTQC { container 'fastqc:latest' }\n", encoding="utf-8"
    )

    diag_file = tmp_path / "diagnosis.json"
    finding = Finding(
        id="f1",
        rule_id="W001",
        category="portability",
        severity=Severity.ERROR,
        message="Unpinned container",
        file_path="main.nf",
        line_number=1,
        fingerprint=Fingerprint(hash="f" * 64),
    )
    report = DiagnosisReport(
        workflow_name="test_pipeline",
        workflow_path=str(pipeline_file),
        findings=[finding],
    )
    diag_file.write_text(report.model_dump_json(), encoding="utf-8")

    # Run fix first to generate and save fixes.json
    fix_res = runner.invoke(app, ["fix", str(diag_file), "--all"])
    assert fix_res.exit_code == 0

    mock_pr_info = PublishedPullRequestInfo(
        number=108,
        title="[Workflow Clinic] Fix",
        url="https://github.com/owner/repo/pull/108",
        head_branch="workflow-clinic/fix-abcdef12",
        base_branch="main",
        applied_count=1,
    )

    with (
        patch(
            "workflow_clinic.cli.GitHubPublisher.publish_pull_request",
            return_value=mock_pr_info,
        ),
        patch(
            "workflow_clinic.cli.GitHubPublisher.fetch_active_fingerprints",
            return_value=set(),
        ),
        patch(
            "workflow_clinic.cli.GitHubPublisher.fetch_all_clinic_issues",
            return_value=[],
        ),
    ):
        res = runner.invoke(
            app,
            [
                "create-pr",
                str(diag_file),
                "--all",
                "--repo",
                "owner/repo",
                "--token",
                "ghp_test_token_1234",
            ],
        )

    assert res.exit_code == 0
    assert "Created Pull Request #108" in res.stdout
    assert "https://github.com/owner/repo/pull/108" in res.stdout


def test_github_publisher_repo_normalization() -> None:
    """Verify repository formats are correctly parsed into owner/repo format."""
    # owner/repo standard format
    pub1 = GitHubPublisher(token="tok", repository="owner/repo")
    assert pub1.repository == "owner/repo"

    # https URL format
    pub2 = GitHubPublisher(
        token="tok",
        repository="https://github.com/revaarathore11/ga4gh_workflow_clinic_gsoc_2026-",
    )
    assert pub2.repository == "revaarathore11/ga4gh_workflow_clinic_gsoc_2026-"

    # https URL format with .git suffix
    pub3 = GitHubPublisher(
        token="tok",
        repository="https://github.com/revaarathore11/ga4gh_workflow_clinic_gsoc_2026-.git",
    )
    assert pub3.repository == "revaarathore11/ga4gh_workflow_clinic_gsoc_2026-"

    # SSH format
    pub4 = GitHubPublisher(
        token="tok",
        repository="git@github.com:owner/repo.git",
    )
    assert pub4.repository == "owner/repo"

    # Trailing slash format
    pub5 = GitHubPublisher(
        token="tok",
        repository="https://github.com/owner/repo/",
    )
    assert pub5.repository == "owner/repo"


def test_cli_create_pr_cmd_deduplication(tmp_path: Path) -> None:
    """Verify standalone 'create-pr' command filters out already active fingerprints."""
    runner = CliRunner()
    pipeline_file = tmp_path / "main.nf"
    pipeline_file.write_text(
        "process FASTQC { container 'fastqc:latest' }\n", encoding="utf-8"
    )

    diag_file = tmp_path / "diagnosis.json"
    finding = Finding(
        id="f1",
        rule_id="W001",
        category="portability",
        severity=Severity.ERROR,
        message="Unpinned container",
        file_path="main.nf",
        line_number=1,
        fingerprint=Fingerprint(hash="f" * 64),
    )
    report = DiagnosisReport(
        workflow_name="test_pipeline",
        workflow_path=str(pipeline_file),
        findings=[finding],
    )
    diag_file.write_text(report.model_dump_json(), encoding="utf-8")

    with (
        patch(
            "workflow_clinic.cli.GitHubPublisher.fetch_active_fingerprints",
            return_value={"f" * 64},
        ),
    ):
        res = runner.invoke(
            app,
            [
                "create-pr",
                str(diag_file),
                "--all",
                "--repo",
                "owner/repo",
                "--token",
                "ghp_test_token_1234",
            ],
        )

    assert res.exit_code == 0
    assert "filtered out 1 finding(s) already tracked" in res.stdout
    assert "There are no fixes to commit in a PR" in res.stdout


def test_publish_pr_isolates_session_changes(tmp_path: Path) -> None:
    """Verify that only proposals from the current session are committed to GitHub, ignoring unrelated local changes."""
    publisher = GitHubPublisher(token="ghp_test_token_1234", repository="owner/repo")
    mock_repo = MagicMock()
    mock_repo.default_branch = "main"

    mock_base_ref = MagicMock()
    mock_base_ref.commit.sha = "base_sha"
    mock_repo.get_branch.return_value = mock_base_ref

    # Clean content on remote repository (has container: 'latest', cpus: 1)
    mock_existing_file = MagicMock()
    mock_existing_file.sha = "file_sha"
    mock_existing_file.decoded_content = (
        b"process TEST {\n    container 'test:latest'\n    cpus 1\n}\n"
    )
    mock_repo.get_contents.return_value = mock_existing_file

    # Mock PR output
    mock_created_pr = MagicMock()
    mock_created_pr.number = 200
    mock_created_pr.title = "Test PR"
    mock_created_pr.html_url = "https://github.com/owner/repo/pull/200"
    mock_repo.create_pull.return_value = mock_created_pr

    # Local workspace file is "dirty" with both changes (container: 'v1.0.0', cpus: 4)
    local_file = tmp_path / "main.nf"
    local_file.write_text(
        "process TEST {\n    container 'test:v1.0.0'\n    cpus 4\n}\n", encoding="utf-8"
    )

    # Proposal for ONLY change 2 (cpus limit fix)
    prop2 = FixProposal(
        finding_id="f2",
        rule_id="W002",
        category="resources",
        target_file="main.nf",
        original_snippet="cpus 1",
        proposed_snippet="cpus 4",
        explanation="Increase cpus limit",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
        line_number=3,
    )
    applied = AppliedProposal(
        proposal=prop2,
        applied=True,
        outcome=ApplyOutcome(
            success=True,
            modified_file=local_file,
            verification_passed=True,
        ),
    )
    session = FixSession(
        session_id="session_test",
        source="main.nf",
        proposals=[prop2],
        applied_proposals=[applied],
    )

    with patch.object(publisher, "_get_repo", return_value=mock_repo):
        publisher.publish_pull_request(session=session, root_dir=tmp_path)

    # Verify update_file committed only change 2 to the remote branch, leaving change 1 (container) clean
    mock_repo.update_file.assert_called_once()
    call_kwargs = mock_repo.update_file.call_args.kwargs
    # The committed content should have container 'test:latest' (original clean content) and cpus 4 (proposal 2)
    expected_content = "process TEST {\n    container 'test:latest'\n    cpus 4\n}\n"
    assert call_kwargs["content"] == expected_content
