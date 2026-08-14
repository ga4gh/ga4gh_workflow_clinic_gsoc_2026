"""Reporting subpackage containing fingerprinting and report generation utilities."""

from workflow_clinic.reporting.fingerprint import compute_fingerprint
from workflow_clinic.reporting.github_publisher import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubPublisher,
    GitHubPublisherError,
    GitHubRepoNotFoundError,
    PublishedIssueInfo,
    _mask_token,
)
from workflow_clinic.reporting.issue_generator import (
    GeneratedIssue,
    extract_fingerprints,
    filter_new_findings,
    generate_issues,
    group_findings,
)

__all__ = [
    "GeneratedIssue",
    "GitHubAPIError",
    "GitHubAuthError",
    "GitHubPublisher",
    "GitHubPublisherError",
    "GitHubRepoNotFoundError",
    "PublishedIssueInfo",
    "_mask_token",
    "compute_fingerprint",
    "extract_fingerprints",
    "filter_new_findings",
    "generate_issues",
    "group_findings",
]
