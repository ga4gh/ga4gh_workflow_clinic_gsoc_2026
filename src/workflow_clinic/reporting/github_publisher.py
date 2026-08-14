"""PyGitHub integration module for publishing diagnostic issues to online GitHub repositories."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from github import (
    Auth,
    BadCredentialsException,
    Github,
    GithubException,
    RateLimitExceededException,
    UnknownObjectException,
)
from pydantic import BaseModel, Field

from workflow_clinic.reporting.issue_generator import extract_fingerprints

if TYPE_CHECKING:
    from github.Repository import Repository

    from workflow_clinic.reporting.issue_generator import GeneratedIssue

logger = logging.getLogger(__name__)
HTTP_UNPROCESSABLE_ENTITY = 422


class PublishedIssueInfo(BaseModel):
    """Container for metadata of a published GitHub issue."""

    number: int = Field(..., description="GitHub issue number")
    title: str = Field(..., description="Title of the published issue")
    url: str = Field(..., description="HTML URL to view issue on GitHub")
    category: str = Field(..., description="Category domain of the issue")


class GitHubPublisherError(Exception):
    """Base exception class for GitHub publisher errors."""


class GitHubAuthError(GitHubPublisherError):
    """Raised when GitHub Personal Access Token authentication fails (HTTP 401)."""


class GitHubRepoNotFoundError(GitHubPublisherError):
    """Raised when target GitHub repository is missing or inaccessible (HTTP 404)."""


class GitHubAPIError(GitHubPublisherError):
    """Raised when GitHub API requests fail (Rate limit HTTP 403, 500, network error)."""


def _mask_token(token: str) -> str:
    """Mask sensitive GitHub Personal Access Token for safe logging/display.

    Args:
        token: Raw GitHub access token string.

    Returns:
        Masked token string (e.g. 'ghp_1234...5678').
    """
    if not token or len(token) < 8:  # noqa: PLR2004
        return "****"
    return f"{token[:4]}...{token[-4:]}"


def _get_rate_limit_reset_utc(g: Github) -> str:
    """Extract UTC reset time string from PyGitHub rate limit object safely."""
    try:
        rate_obj = g.get_rate_limit()
        core_or_rate = getattr(rate_obj, "core", getattr(rate_obj, "rate", rate_obj))
        reset_time: Any = getattr(core_or_rate, "reset", None)
        if hasattr(reset_time, "strftime"):
            return str(reset_time.strftime("%H:%M UTC"))
    except Exception:  # noqa: BLE001, S110
        pass
    return "unknown"


class GitHubPublisher:
    """Publishes diagnostic issues to GitHub repositories via PyGitHub API."""

    def __init__(self, token: str, repository: str) -> None:
        """Initialize GitHubPublisher client with PAT token and target repository.

        Args:
            token: GitHub Personal Access Token (PAT).
            repository: Target repository in 'owner/repo' format.
        """
        if not token or not token.strip():
            msg = "GitHub Personal Access Token is required."
            raise GitHubAuthError(msg)
        if not repository or "/" not in repository:
            msg = f"Invalid repository format '{repository}'. Expected 'owner/repo'."
            raise GitHubRepoNotFoundError(msg)

        self.token = token.strip()
        self.repository = repository.strip()
        self._auth = Auth.Token(self.token)
        self.g = Github(auth=self._auth)
        self._repo_obj: Repository | None = None

        logger.info(
            "Initialized GitHubPublisher for repository '%s' using token '%s'",
            self.repository,
            _mask_token(self.token),
        )

    def _get_repo(self) -> Repository:
        """Lazily retrieve and cache PyGitHub Repository object with exception mapping."""
        if self._repo_obj is not None:
            return self._repo_obj

        try:
            self._repo_obj = self.g.get_repo(self.repository)
        except RateLimitExceededException as e:
            reset_utc = _get_rate_limit_reset_utc(self.g)
            msg = f"GitHub API rate limit exceeded. Resets at {reset_utc}."
            raise GitHubAPIError(msg) from e
        except BadCredentialsException as e:
            msg = "Invalid GitHub Personal Access Token provided."
            raise GitHubAuthError(msg) from e
        except UnknownObjectException as e:
            msg = f"Repository '{self.repository}' not found or inaccessible."
            raise GitHubRepoNotFoundError(msg) from e
        except GithubException as e:
            msg = f"GitHub API error: {getattr(e, 'data', {}).get('message', str(e))}"
            raise GitHubAPIError(msg) from e
        else:
            return self._repo_obj

    def ensure_label(
        self, label_name: str = "workflow-clinic", color: str = "d73a4a"
    ) -> Any:
        """Safely get or create a label on target repository, handling race conditions.

        Args:
            label_name: Name of the label (default: 'workflow-clinic').
            color: Hex color string without '#' (default: 'd73a4a').

        Returns:
            PyGitHub Label object.
        """
        repo = self._get_repo()
        try:
            return repo.get_label(label_name)
        except UnknownObjectException:
            try:
                return repo.create_label(
                    name=label_name,
                    color=color,
                    description="Diagnostic finding report generated by GA4GH Workflow Clinic",
                )
            except GithubException as e:
                # 422 Unprocessable Entity can happen if label was created concurrently by another process
                status = getattr(e, "status", None)
                data = getattr(e, "data", {})
                if status == HTTP_UNPROCESSABLE_ENTITY or (
                    isinstance(data, dict) and "already_exists" in str(data)
                ):
                    return repo.get_label(label_name)
                msg = f"Failed to ensure label '{label_name}': {getattr(e, 'message', str(e))}"
                raise GitHubAPIError(msg) from e

    def fetch_active_fingerprints(self) -> set[str]:
        """Fetch 64-hex SHA-256 fingerprints from workflow-clinic open issues in target repository.

        Returns:
            Set of active fingerprint hashes found in open repository issue bodies.
        """
        repo = self._get_repo()
        active_hashes: set[str] = set()
        try:
            open_issues = repo.get_issues(state="open", labels=["workflow-clinic"])
            for issue in open_issues:
                if issue.body:
                    extracted = extract_fingerprints(issue.body)
                    active_hashes.update(extracted)
        except RateLimitExceededException as e:
            reset_utc = _get_rate_limit_reset_utc(self.g)
            msg = f"GitHub API rate limit exceeded while reading open issues. Resets at {reset_utc}."
            raise GitHubAPIError(msg) from e
        except GithubException as e:
            msg = f"Failed to fetch open issues for fingerprint deduplication: {getattr(e, 'message', str(e))}"
            raise GitHubAPIError(msg) from e
        return active_hashes

    def publish_issue(self, issue_group: GeneratedIssue) -> PublishedIssueInfo:
        """Publish a single GeneratedIssue to the target GitHub repository online.

        Args:
            issue_group: GeneratedIssue instance to publish.

        Returns:
            PublishedIssueInfo containing issue number, title, and HTML URL.
        """
        repo = self._get_repo()
        title = f"[Workflow Clinic] Diagnostic Finding: {issue_group.title}"

        try:
            label_obj = self.ensure_label(label_name="workflow-clinic")
            created_issue = repo.create_issue(
                title=title,
                body=issue_group.body,
                labels=[label_obj],
            )
            logger.info(
                "Published GitHub issue #%d to %s: %s",
                created_issue.number,
                self.repository,
                created_issue.html_url,
            )
            return PublishedIssueInfo(
                number=created_issue.number,
                title=created_issue.title,
                url=created_issue.html_url,
                category=issue_group.category,
            )
        except RateLimitExceededException as e:
            reset_utc = _get_rate_limit_reset_utc(self.g)
            msg = f"GitHub API rate limit exceeded while publishing issue. Resets at {reset_utc}."
            raise GitHubAPIError(msg) from e
        except GithubException as e:
            msg = f"Failed to publish issue to GitHub: {getattr(e, 'data', {}).get('message', str(e))}"
            raise GitHubAPIError(msg) from e
