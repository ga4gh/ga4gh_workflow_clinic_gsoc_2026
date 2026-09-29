"""Unit tests for Workflow Clinic terminal UI components (formatters, prompts, validators)."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import typer
from rich.console import Console

from workflow_clinic.models.diagnosis import DiagnosisReport, Finding
from workflow_clinic.models.fix import (
    AppliedProposal,
    FixProposal,
    FixSession,
    FixStrategyLayer,
)
from workflow_clinic.models.workflow_bundle import WorkflowBundle, WorkflowMetadata
from workflow_clinic.reporting import GeneratedIssue
from workflow_clinic.reporting.github_publisher import PublishedIssueInfo
from workflow_clinic.services import (
    ExamineResult,
    FixResult,
    PublishMode,
    PublishResult,
)
from workflow_clinic.ui import (
    display_enhance_status,
    display_examine_results,
    display_fix_results,
    display_models_table,
    display_publish_results,
    parse_selection,
    prompt_category_selection,
    prompt_issue_selection,
    verify_github_repo,
)


def _make_dummy_bundle(name: str = "dummy_pipeline") -> WorkflowBundle:
    return WorkflowBundle(
        metadata=WorkflowMetadata(name=name, version="1.0.0"),
        tasks=[],
    )


def _make_examine_result(  # noqa: PLR0913
    findings: list[Finding] | None = None,
    workflow_name: str = "dummy_pipeline",
    *,
    has_key: bool = False,
    resolved_model: str | None = None,
    enhance_failed: bool = False,
    enhance_error: str | None = None,
    fallback_count: int = 0,
) -> ExamineResult:
    report = DiagnosisReport(workflow_name=workflow_name, findings=findings or [])
    bundle = _make_dummy_bundle(name=workflow_name)
    return ExamineResult(
        report=report,
        bundle=bundle,
        scan_path=Path.cwd(),
        parser_name="nextflow",
        output_path=Path("diagnosis.json"),
        resolved_model=resolved_model,
        has_key=has_key,
        enhance_failed=enhance_failed,
        enhance_error=enhance_error,
        fallback_count=fallback_count,
    )


def _make_fix_result(
    proposals: list[FixProposal] | None = None,
    applied_proposals: list[AppliedProposal] | None = None,
    *,
    dry_run: bool = False,
    session_id: str = "deadbeef1234",
) -> FixResult:
    report = DiagnosisReport(workflow_name="dummy_pipeline", findings=[])
    session = FixSession(
        session_id=session_id,
        source="local",
        proposals=proposals or [],
        applied_proposals=applied_proposals or [],
    )
    return FixResult(
        session=session,
        report=report,
        root_dir=Path.cwd(),
        diag_path=Path("diagnosis.json"),
        dry_run=dry_run,
        actionable_findings=[],
    )


def _make_publish_result(
    mode: PublishMode,
    selected_issues: list[GeneratedIssue] | None = None,
    published_issues: list[PublishedIssueInfo] | None = None,
    local_output_path: Path | None = None,
    combined_markdown: str = "",
) -> PublishResult:
    report = DiagnosisReport(workflow_name="dummy_pipeline", findings=[])
    return PublishResult(
        report=report,
        generated_issues=[],
        selected_issues=selected_issues or [],
        published_issues=published_issues or [],
        local_output_path=local_output_path,
        combined_markdown=combined_markdown,
        mode=mode,
        existing_fingerprints=set(),
        skipped_count=0,
    )


def test_display_examine_results_renders_finding_table() -> None:
    test_console = Console(record=True, width=120)
    findings = [
        Finding(
            rule_id="W001",
            category="container",
            severity="ERROR",
            message="Missing container declaration",
            file_path="main.nf",
            line_number=12,
            process_name="FASTQC",
        ),
        Finding(
            rule_id="W002",
            category="resources",
            severity="WARNING",
            message="No cpu defined",
            file_path="main.nf",
            line_number=25,
            process_name="MULTIQC",
        ),
    ]
    res = _make_examine_result(findings=findings)

    with pytest.raises(typer.Exit) as exc_info:
        display_examine_results(res, enhance=False, console=test_console)

    assert exc_info.value.exit_code == 1
    output = test_console.export_text()
    assert "Diagnostic Findings for 'dummy_pipeline'" in output
    assert "W001" in output
    assert "W002" in output
    assert "main.nf:12" in output
    assert "Summary: 1 error(s), 1 warning(s), 0 info(s)" in output


def test_display_examine_results_no_findings_shows_clean_message() -> None:
    test_console = Console(record=True, width=120)
    res = _make_examine_result(findings=[], workflow_name="clean_pipeline")

    with pytest.raises(typer.Exit) as exc_info:
        display_examine_results(res, enhance=False, console=test_console)

    assert exc_info.value.exit_code == 0
    output = test_console.export_text()
    assert "No issues found" in output
    assert "cloud-ready" in output


def test_display_enhance_status_shows_error_and_fallback() -> None:
    test_console = Console(record=True, width=120)
    test_err = Console(record=True, width=120)
    res = _make_examine_result(
        enhance_failed=True,
        enhance_error="Timeout connection",
    )

    display_enhance_status(res, count=3, console=test_console, err_console=test_err)
    err_output = test_err.export_text()
    assert "AI Critic enhancement failed: Timeout connection" in err_output
    assert "Using offline Knowledge Store" in err_output


def test_display_enhance_status_shows_model_guidance() -> None:
    test_console = Console(record=True, width=120)
    res = _make_examine_result(
        has_key=True,
        resolved_model="gpt-4o",
        fallback_count=0,
    )

    display_enhance_status(res, count=5, console=test_console)
    output = test_console.export_text()
    assert "AI remediation guidance added to 5/5 findings (model: gpt-4o)" in output


def test_display_models_table_renders_model_list() -> None:
    test_console = Console(record=True, width=120)
    custom_models = [("TEST_API_KEY", "test-model-v1")]
    display_models_table(models=custom_models, console=test_console)

    output = test_console.export_text()
    assert "Supported AI Models" in output
    assert "TEST_API_KEY" in output
    assert "test-model-v1" in output


def test_display_fix_results_no_proposals() -> None:
    test_console = Console(record=True, width=120)
    result = _make_fix_result(proposals=[], session_id="abc12345-6789")

    with pytest.raises(typer.Exit) as exc:
        display_fix_results(result, console=test_console)

    assert exc.value.exit_code == 0
    assert "No registered fixers available" in test_console.export_text()


def test_display_fix_results_dry_run_renders_diff_table() -> None:
    test_console = Console(record=True, width=120)
    proposal = FixProposal(
        finding_id="fp001",
        rule_id="W001",
        category="container",
        target_file="workflow.nf",
        original_snippet="process A {}",
        proposed_snippet="process A { container 'ubuntu:22.04' }",
        explanation="Added pinned container directive",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )
    result = _make_fix_result(
        proposals=[proposal],
        dry_run=True,
        session_id="deadbeef1234",
    )

    with pytest.raises(typer.Exit) as exc:
        display_fix_results(result, console=test_console)

    assert exc.value.exit_code == 0
    output = test_console.export_text()
    assert "Workflow Doctor Dry Run (1 proposed fix(es))" in output
    assert "W001" in output
    assert "workflow.nf" in output
    assert "LAYER1_AST" in output
    assert "Dry-run complete for session deadbeef" in output


def test_display_fix_results_applied_mode_shows_success() -> None:
    test_console = Console(record=True, width=120)
    proposal = FixProposal(
        finding_id="fp001",
        rule_id="W001",
        category="container",
        target_file="workflow.nf",
        original_snippet="process A {}",
        proposed_snippet="process A { container 'ubuntu:22.04' }",
        explanation="Added container",
        strategy_layer=FixStrategyLayer.LAYER1_AST,
    )
    applied = AppliedProposal(proposal=proposal, applied=True)
    result = _make_fix_result(
        proposals=[proposal],
        applied_proposals=[applied],
        dry_run=False,
        session_id="cafebabe5678",
    )

    display_fix_results(result, console=test_console)
    output = test_console.export_text()
    assert (
        "Workflow Doctor completed session cafebabe: 1/1 fix(es) applied successfully."
        in output
    )


def test_display_publish_results_dry_run_shows_summary() -> None:
    test_console = Console(record=True, width=120)
    result = _make_publish_result(
        mode=PublishMode.DRY_RUN,
        combined_markdown="# Dry Run Issue Body",
    )

    with pytest.raises(typer.Exit) as exc:
        display_publish_results(result, console=test_console)

    assert exc.value.exit_code == 0
    output = test_console.export_text()
    assert "--- Issue Markdown Payload (Dry Run) ---" in output
    assert "# Dry Run Issue Body" in output


def test_display_publish_results_github_mode_shows_urls() -> None:
    test_console = Console(record=True, width=120)
    pub_issue = PublishedIssueInfo(
        number=42,
        url="https://github.com/org/repo/issues/42",
        title="[Clinic] Missing Containers",
        category="container",
    )
    result = _make_publish_result(
        mode=PublishMode.GITHUB,
        published_issues=[pub_issue],
    )

    display_publish_results(
        result,
        repo_name="org/repo",
        console=test_console,
    )
    output = test_console.export_text()
    assert "Successfully published 1 issue(s) to GitHub repository 'org/repo'" in output
    assert "#42" in output
    assert "https://github.com/org/repo/issues/42" in output


def test_display_publish_results_github_mode_empty_raises_exit_1() -> None:
    test_console = Console(record=True, width=120)
    test_err = Console(record=True, width=120)
    result = _make_publish_result(
        mode=PublishMode.GITHUB,
        published_issues=[],
    )

    with pytest.raises(typer.Exit) as exc:
        display_publish_results(
            result,
            console=test_console,
            err_console=test_err,
        )

    assert exc.value.exit_code == 1
    assert "Failed to publish any issues to GitHub." in test_err.export_text()


def test_display_publish_results_local_mode_shows_file_path(tmp_path: Path) -> None:
    test_console = Console(record=True, width=240)
    out_file = tmp_path / "issue.md"
    issue = GeneratedIssue(
        category="container",
        title="[Clinic] Container",
        body="Body",
        severity="ERROR",
        fingerprints=["fp1"],
    )
    result = _make_publish_result(
        mode=PublishMode.LOCAL_FILE,
        selected_issues=[issue],
        local_output_path=out_file,
    )

    display_publish_results(result, console=test_console)
    output = test_console.export_text()
    assert f"Exported 1 issue group(s) to {out_file}" in output


def test_parse_selection_handles_range_and_comma_and_all() -> None:
    assert parse_selection("all", 5) == [0, 1, 2, 3, 4]
    assert parse_selection("", 5) == [0, 1, 2, 3, 4]
    assert parse_selection("a", 5) == [0, 1, 2, 3, 4]
    assert parse_selection("1", 5) == [0]
    assert parse_selection("1, 3", 5) == [0, 2]
    assert parse_selection("1-3", 5) == [0, 1, 2]
    assert parse_selection("1-3, 5", 5) == [0, 1, 2, 4]


def test_parse_selection_ignores_out_of_bounds() -> None:
    assert parse_selection("0, 99, abc, 2", 5) == [1]
    assert parse_selection("10-20", 5) == []


def test_prompt_issue_selection_auto_selects_in_non_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_console = Console(record=True, width=120)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    issues = [
        GeneratedIssue(
            category="container",
            title="Title 1",
            body="Body 1",
            severity="ERROR",
            fingerprints=["fp1"],
        ),
        GeneratedIssue(
            category="resources",
            title="Title 2",
            body="Body 2",
            severity="WARNING",
            fingerprints=["fp2"],
        ),
    ]

    selected = prompt_issue_selection(issues, console=test_console)
    assert len(selected) == 2
    assert "Non-interactive terminal detected" in test_console.export_text()


def test_prompt_issue_selection_interactive_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_console = Console(record=True, width=120)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)

    def _mock_prompt(_msg: str, default: str = "") -> str:
        _ = default
        return "1"

    monkeypatch.setattr("typer.prompt", _mock_prompt)

    issues = [
        GeneratedIssue(
            category="container",
            title="Title 1",
            body="Body 1",
            severity="ERROR",
            fingerprints=["fp1"],
        ),
        GeneratedIssue(
            category="resources",
            title="Title 2",
            body="Body 2",
            severity="WARNING",
            fingerprints=["fp2"],
        ),
    ]

    selected = prompt_issue_selection(issues, console=test_console)
    assert len(selected) == 1
    assert selected[0].category == "container"


def test_prompt_category_selection_interactive_and_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_console = Console(record=True, width=120)
    mock_coordinator = MagicMock()
    f1 = Finding(
        rule_id="W001",
        category="container",
        severity="ERROR",
        message="m1",
    )
    f2 = Finding(
        rule_id="W002",
        category="resources",
        severity="WARNING",
        message="m2",
    )
    mock_coordinator.group_findings_by_category.return_value = {
        "container": [f1],
        "resources": [f2],
    }

    def _mock_prompt(_msg: str, default: str = "") -> str:
        _ = default
        return "2"

    monkeypatch.setattr("typer.prompt", _mock_prompt)

    selected = prompt_category_selection(
        mock_coordinator,
        actionable_findings=[f1, f2],
        workflow_name="test_wf",
        console=test_console,
    )
    assert selected == [f2]
    assert "Diagnostic Categories to Repair for 'test_wf'" in test_console.export_text()


def test_verify_github_repo_missing_token_with_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_err = Console(record=True, width=120)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    with pytest.raises(typer.Exit) as exc:
        verify_github_repo(token=None, repo="owner/repo", err_console=test_err)
    assert exc.value.exit_code == 1
    assert (
        "GitHub repository specified but GitHub token is missing"
        in test_err.export_text()
    )


def test_verify_github_repo_missing_repo_with_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_err = Console(record=True, width=120)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    with pytest.raises(typer.Exit) as exc:
        verify_github_repo(token="ghp_123", repo=None, err_console=test_err)
    assert exc.value.exit_code == 1
    assert "GitHub token specified but repository is missing" in test_err.export_text()
