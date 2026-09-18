"""Coordinator service for automated GitHub issue publishing and local markdown reporting."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

from workflow_clinic.models.diagnosis import DiagnosisReport
from workflow_clinic.reporting import (
    GitHubPublisher,
    GitHubPublisherError,
    filter_new_findings,
    generate_issues,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from workflow_clinic.models.diagnosis import Finding
    from workflow_clinic.reporting import (
        GeneratedIssue,
        PublishedIssueInfo,
    )

logger = logging.getLogger(__name__)


class PublishMode(StrEnum):
    """Target execution mode for issue reporting."""

    GITHUB = "github"
    LOCAL_FILE = "local_file"
    DRY_RUN = "dry_run"


@dataclass
class PublishConfig:
    """Headless configuration for issue publishing orchestration."""

    target: str = "."
    all_issues: bool = False
    dry_run: bool = False
    preview: bool = False
    token: str | None = None
    repo: str | None = None
    local: bool = False
    output: Path = Path("issue.md")


@dataclass
class PublishCallbacks:
    """Lifecycle event callbacks decoupling coordinator execution from terminal output."""

    on_load_start: Callable[[Path], None] | None = None
    on_report_loaded: Callable[[DiagnosisReport, int], None] | None = None
    on_deduplication_start: Callable[[str], None] | None = None
    on_deduplication_complete: Callable[[int, int], None] | None = None
    on_issues_generated: Callable[[list[GeneratedIssue]], None] | None = None
    on_issue_published: Callable[[PublishedIssueInfo], None] | None = None
    on_local_exported: Callable[[Path, int], None] | None = None
    on_warning: Callable[[str], None] | None = None
    on_error: Callable[[str], None] | None = None


@dataclass
class PublishDependencies:
    """Pluggable dependencies for testing and runtime injection."""

    publisher_factory: Callable[[str, str], GitHubPublisher] | None = None
    publisher: GitHubPublisher | None = None
    issue_generator_fn: Callable[[DiagnosisReport], list[GeneratedIssue]] | None = None
    filter_new_findings_fn: (
        Callable[[list[Finding], set[str]], list[Finding]] | None
    ) = None
    custom_logger: logging.Logger | None = None


@dataclass
class PublishResult:
    """Structured result returned by PublishCoordinator.execute()."""

    report: DiagnosisReport
    generated_issues: list[GeneratedIssue]
    selected_issues: list[GeneratedIssue]
    published_issues: list[PublishedIssueInfo]
    local_output_path: Path | None
    combined_markdown: str
    mode: PublishMode
    existing_fingerprints: set[str]
    skipped_count: int


class PublishCoordinator:
    """Headless coordinator for diagnostic loading, issue deduplication, and GitHub publishing."""

    def __init__(
        self,
        config: PublishConfig,
        callbacks: PublishCallbacks | None = None,
        dependencies: PublishDependencies | None = None,
    ) -> None:
        self.config = config
        self.callbacks = callbacks or PublishCallbacks()
        self.deps = dependencies or PublishDependencies()
        self.logger = self.deps.custom_logger or logger
        self._cached_report: DiagnosisReport | None = None
        self._cached_fingerprints: set[str] | None = None
        self._cached_publisher_mode: (
            tuple[GitHubPublisher | None, PublishMode] | None
        ) = None

    def resolve_diagnosis_path(self) -> Path:
        """Resolve path to diagnosis.json from input target.

        Returns:
            Resolved absolute Path to diagnosis.json.

        Raises:
            FileNotFoundError: If diagnosis.json does not exist.
        """
        target_path = Path(self.config.target).resolve()
        diag_path = (
            target_path
            if target_path.is_file() and target_path.suffix == ".json"
            else target_path / "diagnosis.json"
        )

        if not diag_path.exists():
            msg = f"Could not find '{diag_path.name}' at '{diag_path.parent}'."
            raise FileNotFoundError(msg)

        return diag_path

    def load_diagnosis(self, diag_path: Path | None = None) -> DiagnosisReport:
        """Load and validate the diagnosis JSON report into a DiagnosisReport model."""
        if self._cached_report is not None and diag_path is None:
            return self._cached_report

        path = diag_path or self.resolve_diagnosis_path()
        if self.callbacks.on_load_start:
            self.callbacks.on_load_start(path)

        data = json.loads(path.read_text(encoding="utf-8"))
        report = DiagnosisReport.model_validate(data)

        if self.callbacks.on_report_loaded:
            self.callbacks.on_report_loaded(report, len(report.findings))

        self._cached_report = report
        return report

    def resolve_publisher(self) -> tuple[GitHubPublisher | None, PublishMode]:
        """Resolve publisher instance and determined publish mode.

        Returns:
            Tuple of (optional GitHubPublisher instance, determined PublishMode).

        Raises:
            ValueError: If GitHub token is specified without repo or vice versa when not in local mode.
        """
        if self._cached_publisher_mode is not None:
            return self._cached_publisher_mode

        token_val = self.config.token or os.getenv("GITHUB_TOKEN")
        repo_val = self.config.repo or os.getenv("GITHUB_REPOSITORY")

        if self.config.local:
            if token_val or repo_val:
                warn_msg = (
                    "--local overrides GitHub credentials; exporting to local file."
                )
                if self.callbacks.on_warning:
                    self.callbacks.on_warning(warn_msg)
                else:
                    self.logger.warning("%s", warn_msg)
            mode = (
                PublishMode.DRY_RUN if self.config.dry_run else PublishMode.LOCAL_FILE
            )
            self._cached_publisher_mode = (None, mode)
            return None, mode

        if token_val and repo_val:
            factory = self.deps.publisher_factory
            if factory:
                publisher = factory(token_val, repo_val)
            else:
                publisher = self.deps.publisher or GitHubPublisher(
                    token=token_val, repository=repo_val
                )
            mode = PublishMode.DRY_RUN if self.config.dry_run else PublishMode.GITHUB
            self._cached_publisher_mode = (publisher, mode)
            return publisher, mode

        if token_val and not repo_val:
            msg = (
                "GitHub repository specified but GitHub token is missing. "
                "Provide via --token or GITHUB_TOKEN."
                if not token_val
                else "GitHub token specified but repository is missing. "
                "Provide via --repo or GITHUB_REPOSITORY."
            )
            raise ValueError(msg)

        if repo_val and not token_val:
            msg = (
                "GitHub repository specified but GitHub token is missing. "
                "Provide via --token or GITHUB_TOKEN."
            )
            raise ValueError(msg)

        mode = PublishMode.DRY_RUN if self.config.dry_run else PublishMode.LOCAL_FILE
        self._cached_publisher_mode = (None, mode)
        return None, mode

    def fetch_active_fingerprints(
        self, publisher: GitHubPublisher | None = None
    ) -> set[str]:
        """Fetch remote active fingerprints for deduplication.

        Returns empty set if publisher is None.
        Raises GitHubPublisherError if remote API or authentication fails.
        """
        if publisher is None:
            return set()

        if self._cached_fingerprints is not None:
            return self._cached_fingerprints

        if self.callbacks.on_deduplication_start:
            self.callbacks.on_deduplication_start(publisher.repository)

        fps = publisher.fetch_active_fingerprints()
        self._cached_fingerprints = fps
        return fps

    def get_actionable_issues(
        self,
        report: DiagnosisReport,
        existing_fingerprints: set[str] | None = None,
    ) -> tuple[list[GeneratedIssue], int]:
        """Filter findings against existing fingerprints and generate grouped issues.

        Returns:
            Tuple of (list of GeneratedIssue, count of skipped duplicates).
        """
        fingerprints = existing_fingerprints or set()
        filter_fn = self.deps.filter_new_findings_fn or filter_new_findings
        new_findings = filter_fn(report.findings, fingerprints)
        skipped_count = len(report.findings) - len(new_findings)

        report_to_process = DiagnosisReport(
            workflow_name=report.workflow_name,
            findings=new_findings,
        )

        generator = self.deps.issue_generator_fn or generate_issues
        generated_issues = generator(report_to_process)

        if self.callbacks.on_deduplication_complete:
            self.callbacks.on_deduplication_complete(
                len(fingerprints), len(generated_issues)
            )

        if self.callbacks.on_issues_generated:
            self.callbacks.on_issues_generated(generated_issues)

        return generated_issues, skipped_count

    def execute(
        self, selected_issues: list[GeneratedIssue] | None = None
    ) -> PublishResult:
        """Run the issue publishing or local export pipeline.

        Args:
            selected_issues: Optional pre-filtered list of issues.
                If None, all actionable issues are generated and used.
                If empty list `[]`, returns clean result with 0 published issues.

        Returns:
            PublishResult tracking generated, selected, and published issues.
        """
        diag_path = self.resolve_diagnosis_path()
        report = self.load_diagnosis(diag_path)
        publisher, mode = self.resolve_publisher()
        existing_fingerprints = self.fetch_active_fingerprints(publisher)

        actionable_issues, skipped_count = self.get_actionable_issues(
            report, existing_fingerprints
        )

        if selected_issues is None:
            chosen_issues = actionable_issues
        else:
            chosen_issues = selected_issues

        combined_markdown = "\n\n---\n\n".join(iss.body for iss in chosen_issues)

        if not chosen_issues:
            return PublishResult(
                report=report,
                generated_issues=actionable_issues,
                selected_issues=[],
                published_issues=[],
                local_output_path=None,
                combined_markdown="",
                mode=mode,
                existing_fingerprints=existing_fingerprints,
                skipped_count=skipped_count,
            )

        if mode == PublishMode.DRY_RUN:
            return PublishResult(
                report=report,
                generated_issues=actionable_issues,
                selected_issues=chosen_issues,
                published_issues=[],
                local_output_path=None,
                combined_markdown=combined_markdown,
                mode=PublishMode.DRY_RUN,
                existing_fingerprints=existing_fingerprints,
                skipped_count=skipped_count,
            )

        if mode == PublishMode.GITHUB and publisher is not None:
            published: list[PublishedIssueInfo] = []
            for iss in chosen_issues:
                try:
                    info = publisher.publish_issue(iss)
                    published.append(info)
                    if self.callbacks.on_issue_published:
                        self.callbacks.on_issue_published(info)
                except GitHubPublisherError as e:
                    err_msg = f"Failed to publish issue '{iss.title}': {e}"
                    if self.callbacks.on_error:
                        self.callbacks.on_error(err_msg)
                    else:
                        self.logger.exception("%s", err_msg)

            return PublishResult(
                report=report,
                generated_issues=actionable_issues,
                selected_issues=chosen_issues,
                published_issues=published,
                local_output_path=None,
                combined_markdown=combined_markdown,
                mode=PublishMode.GITHUB,
                existing_fingerprints=existing_fingerprints,
                skipped_count=skipped_count,
            )

        out_path = Path(self.config.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(combined_markdown + "\n", encoding="utf-8")

        if self.callbacks.on_local_exported:
            self.callbacks.on_local_exported(out_path, len(chosen_issues))

        return PublishResult(
            report=report,
            generated_issues=actionable_issues,
            selected_issues=chosen_issues,
            published_issues=[],
            local_output_path=out_path,
            combined_markdown=combined_markdown,
            mode=PublishMode.LOCAL_FILE,
            existing_fingerprints=existing_fingerprints,
            skipped_count=skipped_count,
        )
