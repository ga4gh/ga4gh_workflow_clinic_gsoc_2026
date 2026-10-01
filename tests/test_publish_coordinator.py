"""Unit tests for PublishCoordinator service orchestration."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from unittest.mock import MagicMock

import pytest

from workflow_clinic.models.diagnosis import DiagnosisReport
from workflow_clinic.reporting import (
    GitHubPublisherError,
    PublishedIssueInfo,
)
from workflow_clinic.services.publish_coordinator import (
    PublishCallbacks,
    PublishConfig,
    PublishCoordinator,
    PublishDependencies,
    PublishMode,
    PublishResult,
)


@pytest.fixture(autouse=True)
def _clear_github_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure GitHub credentials from environment do not interfere with tests."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)


FP1_HASH = "1111111111111111111111111111111111111111111111111111111111111111"
FP2_HASH = "2222222222222222222222222222222222222222222222222222222222222222"


def _create_sample_diagnosis_file(
    target_dir: Path, filename: str = "diagnosis.json"
) -> Path:
    """Helper to write sample diagnosis.json in target_dir."""
    diag_file = target_dir / filename
    diag_data = {
        "workflow_name": "sample_pipeline",
        "tasks_count": 2,
        "findings_count": 2,
        "findings": [
            {
                "id": "finding_1",
                "rule_id": "W001",
                "severity": "CRITICAL",
                "category": "containerization",
                "title": "Missing container directive",
                "message": "Process 'FASTQC' has no container defined.",
                "process_name": "FASTQC",
                "file_path": "main.nf",
                "line_number": 1,
                "fingerprint": {"hash": FP1_HASH},
            },
            {
                "id": "finding_2",
                "rule_id": "W002",
                "severity": "WARNING",
                "category": "resources",
                "title": "Missing memory directive",
                "message": "Process 'MULTIQC' has no memory defined.",
                "process_name": "MULTIQC",
                "file_path": "main.nf",
                "line_number": 10,
                "fingerprint": {"hash": FP2_HASH},
            },
        ],
    }
    diag_file.write_text(json.dumps(diag_data), encoding="utf-8")
    return diag_file


def test_publish_coordinator_resolves_diagnosis_file_and_directory(
    tmp_path: Path,
) -> None:
    """Verify coordinator resolves paths when target is a file or a directory."""
    diag_file = _create_sample_diagnosis_file(tmp_path)

    # 1. Target is direct json file
    coord_file = PublishCoordinator(config=PublishConfig(target=str(diag_file)))
    assert coord_file.resolve_diagnosis_path() == diag_file.resolve()

    # 2. Target is directory containing diagnosis.json
    coord_dir = PublishCoordinator(config=PublishConfig(target=str(tmp_path)))
    assert coord_dir.resolve_diagnosis_path() == diag_file.resolve()


def test_publish_coordinator_missing_diagnosis_raises_file_not_found(
    tmp_path: Path,
) -> None:
    """Verify coordinator raises FileNotFoundError when diagnosis.json is missing."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()

    coordinator = PublishCoordinator(config=PublishConfig(target=str(empty_dir)))
    with pytest.raises(FileNotFoundError, match=r"Could not find 'diagnosis\.json'"):
        coordinator.resolve_diagnosis_path()


def test_publish_coordinator_invalid_json_raises_error(tmp_path: Path) -> None:
    """Verify coordinator raises error when diagnosis JSON is malformed."""
    bad_file = tmp_path / "diagnosis.json"
    bad_file.write_text("{not valid json", encoding="utf-8")

    coordinator = PublishCoordinator(config=PublishConfig(target=str(bad_file)))
    with pytest.raises(json.JSONDecodeError):
        coordinator.load_diagnosis()


def test_publish_coordinator_missing_token_or_repo_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ValueError is raised when token is given without repo or vice versa."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    # Token without repo
    coord_no_repo = PublishCoordinator(
        config=PublishConfig(token="test_token", repo=None, local=False)
    )
    with pytest.raises(ValueError, match="repository is missing"):
        coord_no_repo.resolve_publisher()

    # Repo without token
    coord_no_token = PublishCoordinator(
        config=PublishConfig(token=None, repo="owner/repo", local=False)
    )
    with pytest.raises(ValueError, match="token is missing"):
        coord_no_token.resolve_publisher()


def test_publish_coordinator_local_flag_with_credentials_warns(
    tmp_path: Path,
) -> None:
    """Verify --local + credentials triggers warning callback and uses LOCAL_FILE mode."""
    warnings: list[str] = []
    callbacks = PublishCallbacks(on_warning=warnings.append)

    coordinator = PublishCoordinator(
        config=PublishConfig(
            target=str(tmp_path),
            token="test_token",
            repo="owner/repo",
            local=True,
        ),
        callbacks=callbacks,
    )
    publisher, mode = coordinator.resolve_publisher()

    assert publisher is None
    assert mode == PublishMode.LOCAL_FILE
    assert len(warnings) == 1
    assert "--local overrides GitHub credentials" in warnings[0]


def test_publish_coordinator_local_fallback_when_no_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify coordinator falls back to LOCAL_FILE mode when no token or repo provided."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    coordinator = PublishCoordinator(
        config=PublishConfig(token=None, repo=None, local=False)
    )
    publisher, mode = coordinator.resolve_publisher()

    assert publisher is None
    assert mode == PublishMode.LOCAL_FILE


def test_publish_coordinator_deduplication_filters_existing_fingerprints(
    tmp_path: Path,
) -> None:
    """Verify findings with active fingerprints are skipped and tracked in skipped_count."""
    diag_file = _create_sample_diagnosis_file(tmp_path)
    coordinator = PublishCoordinator(config=PublishConfig(target=str(diag_file)))
    report = coordinator.load_diagnosis()

    # Simulate FP1_HASH already existing on GitHub
    existing_fingerprints = {FP1_HASH}
    actionable_issues, skipped_count = coordinator.get_actionable_issues(
        report, existing_fingerprints
    )

    assert skipped_count == 1
    assert len(actionable_issues) == 1
    assert actionable_issues[0].category == "resources"


def test_publish_coordinator_github_network_failure_raises(
    tmp_path: Path,
) -> None:
    """Verify fingerprint network failure raises GitHubPublisherError to fail fast."""
    diag_file = _create_sample_diagnosis_file(tmp_path)

    mock_publisher = MagicMock()
    mock_publisher.fetch_active_fingerprints.side_effect = GitHubPublisherError(
        "Network connection timeout"
    )

    coordinator = PublishCoordinator(
        config=PublishConfig(target=str(diag_file)),
    )
    with pytest.raises(GitHubPublisherError, match="Network connection timeout"):
        coordinator.fetch_active_fingerprints(mock_publisher)


def test_publish_coordinator_empty_actionable_findings_returns_empty(
    tmp_path: Path,
) -> None:
    """Verify empty findings report generates 0 issues and exits cleanly."""
    empty_diag = tmp_path / "diagnosis.json"
    dummy_report = DiagnosisReport(
        workflow_name="empty",
        findings=[],
        findings_count=0,
    )
    empty_diag.write_text(
        json.dumps(dummy_report.model_dump(mode="json")), encoding="utf-8"
    )

    coordinator = PublishCoordinator(config=PublishConfig(target=str(empty_diag)))
    result = coordinator.execute()

    assert isinstance(result, PublishResult)
    assert len(result.generated_issues) == 0
    assert len(result.selected_issues) == 0
    assert result.combined_markdown == ""


def test_publish_coordinator_explicit_empty_selection_exits_clean(
    tmp_path: Path,
) -> None:
    """Verify passing selected_issues=[] returns a clean result with 0 published issues."""
    diag_file = _create_sample_diagnosis_file(tmp_path)
    coordinator = PublishCoordinator(config=PublishConfig(target=str(diag_file)))
    result = coordinator.execute(selected_issues=[])

    assert isinstance(result, PublishResult)
    assert len(result.generated_issues) == 2
    assert len(result.selected_issues) == 0
    assert result.published_issues == []
    assert result.local_output_path is None


def test_publish_coordinator_dry_run_returns_payload_without_disk_or_api(
    tmp_path: Path,
) -> None:
    """Verify dry_run=True returns combined markdown without creating files on disk."""
    diag_file = _create_sample_diagnosis_file(tmp_path)
    output_file = tmp_path / "exported_issue.md"

    coordinator = PublishCoordinator(
        config=PublishConfig(
            target=str(diag_file),
            dry_run=True,
            output=output_file,
        )
    )
    result = coordinator.execute()

    assert result.mode == PublishMode.DRY_RUN
    assert len(result.selected_issues) == 2
    assert len(result.combined_markdown) > 0
    assert "workflow-clinic:fingerprint:" in result.combined_markdown
    # Output file must NOT be written in dry run mode
    assert not output_file.exists()


def test_publish_coordinator_local_export_writes_file_in_tmp_path(
    tmp_path: Path,
) -> None:
    """Verify local export writes issue markdown to disk and triggers callback."""
    diag_file = _create_sample_diagnosis_file(tmp_path)
    output_file = tmp_path / "custom_output.md"
    exported_events: list[tuple[Path, int]] = []

    callbacks = PublishCallbacks(
        on_local_exported=lambda p, count: exported_events.append((p, count))
    )
    coordinator = PublishCoordinator(
        config=PublishConfig(
            target=str(diag_file),
            local=True,
            output=output_file,
        ),
        callbacks=callbacks,
    )
    result = coordinator.execute()

    assert result.mode == PublishMode.LOCAL_FILE
    assert result.local_output_path == output_file
    assert output_file.exists()
    assert len(exported_events) == 1
    assert exported_events[0] == (output_file, 2)
    content = output_file.read_text(encoding="utf-8")
    assert "Containerization" in content
    assert "Resources" in content


def test_publish_coordinator_online_publishing_success(
    tmp_path: Path,
) -> None:
    """Verify online publishing calls publisher.publish_issue and tracks results."""
    diag_file = _create_sample_diagnosis_file(tmp_path)
    mock_publisher = MagicMock()
    mock_publisher.fetch_active_fingerprints.return_value = set()
    mock_publisher.publish_issue.side_effect = [
        PublishedIssueInfo(
            number=101,
            title="Containerization Issues",
            url="https://github.com/owner/repo/issues/101",
            category="containerization",
        ),
        PublishedIssueInfo(
            number=102,
            title="Resource Limits Issues",
            url="https://github.com/owner/repo/issues/102",
            category="resources",
        ),
    ]

    published_events: list[PublishedIssueInfo] = []
    callbacks = PublishCallbacks(on_issue_published=published_events.append)

    coordinator = PublishCoordinator(
        config=PublishConfig(
            target=str(diag_file),
            token="gh_token",
            repo="owner/repo",
            local=False,
        ),
        callbacks=callbacks,
        dependencies=PublishDependencies(publisher=mock_publisher),
    )
    result = coordinator.execute()

    assert result.mode == PublishMode.GITHUB
    assert len(result.published_issues) == 2
    assert result.published_issues[0].number == 101
    assert result.published_issues[1].number == 102
    assert len(published_events) == 2
    assert published_events[0].number == 101


def test_publish_coordinator_dependency_injection(
    tmp_path: Path,
) -> None:
    """Verify custom filter_new_findings_fn and issue_generator_fn can be injected."""
    diag_file = _create_sample_diagnosis_file(tmp_path)

    custom_filter = MagicMock(return_value=[])
    custom_generator = MagicMock(return_value=[])

    coordinator = PublishCoordinator(
        config=PublishConfig(target=str(diag_file), dry_run=True),
        dependencies=PublishDependencies(
            filter_new_findings_fn=custom_filter,
            issue_generator_fn=custom_generator,
        ),
    )
    coordinator.execute()

    assert custom_filter.called
    assert custom_generator.called


def test_publish_coordinator_lifecycle_callbacks_fire(
    tmp_path: Path,
) -> None:
    """Verify all lifecycle callbacks fire correctly during diagnosis load, deduplication, and execution."""
    diag_file = _create_sample_diagnosis_file(tmp_path)
    events: list[str] = []

    callbacks = PublishCallbacks(
        on_load_start=lambda p: events.append(f"load_start:{p.name}"),
        on_report_loaded=lambda _r, count: events.append(f"loaded:{count}"),
        on_deduplication_start=lambda repo: events.append(f"dedup_start:{repo}"),
        on_deduplication_complete=lambda exist, rem: events.append(
            f"dedup_done:{exist}:{rem}"
        ),
        on_issues_generated=lambda issues: events.append(f"generated:{len(issues)}"),
        on_issue_published=lambda info: events.append(f"published:{info.number}"),
    )

    mock_publisher = MagicMock()
    mock_publisher.repository = "owner/repo"
    mock_publisher.fetch_active_fingerprints.return_value = set()
    mock_publisher.publish_issue.return_value = PublishedIssueInfo(
        number=42,
        title="Test Issue",
        url="https://github.com/owner/repo/issues/42",
        category="containerization",
    )

    coordinator = PublishCoordinator(
        config=PublishConfig(
            target=str(diag_file),
            token="gh_token",
            repo="owner/repo",
            local=False,
        ),
        callbacks=callbacks,
        dependencies=PublishDependencies(publisher=mock_publisher),
    )
    result = coordinator.execute()

    assert "load_start:diagnosis.json" in events
    assert "loaded:2" in events
    assert "dedup_start:owner/repo" in events
    assert "dedup_done:0:2" in events
    assert "generated:2" in events
    assert "published:42" in events
    assert len(result.published_issues) == 2
