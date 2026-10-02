"""CLI and terminal input/credential validators for Workflow Clinic."""

import logging
import os

import typer
from rich.console import Console
from rich.markup import escape

from workflow_clinic.reporting import GitHubPublisher, GitHubPublisherError
from workflow_clinic.ui.console import err_console as default_err_console

logger = logging.getLogger(__name__)


def verify_github_repo(
    token: str | None,
    repo: str | None,
    err_console: Console | None = None,
) -> None:
    """Validate GitHub token and repository credentials if provided."""
    err = err_console or default_err_console
    token_val = token or os.getenv("GITHUB_TOKEN")
    repo_val = repo or os.getenv("GITHUB_REPOSITORY")
    if not repo_val and not token:
        return

    if not token_val:
        err.print(
            "[red]Error:[/red] GitHub repository specified but GitHub token is missing. Provide via --token or GITHUB_TOKEN."
        )
        raise typer.Exit(code=1)
    if not repo_val:
        err.print(
            "[red]Error:[/red] GitHub token specified but repository is missing. Provide via --repo or GITHUB_REPOSITORY."
        )
        raise typer.Exit(code=1)
    try:
        publisher = GitHubPublisher(token=token_val, repository=repo_val)
        active_fps = publisher.fetch_active_fingerprints()
        if active_fps:
            logger.info(
                "Fetched %d active fingerprints from GitHub repository %s",
                len(active_fps),
                repo_val,
            )
    except GitHubPublisherError as e:
        err.print(f"[red]GitHub Authentication/API Error:[/red] {escape(str(e))}")
        raise typer.Exit(code=1) from e
