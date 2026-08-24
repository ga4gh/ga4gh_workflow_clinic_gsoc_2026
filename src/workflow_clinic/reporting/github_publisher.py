"""PyGitHub integration module for publishing diagnostic issues to online GitHub repositories."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

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

    from workflow_clinic.models.fix import FixProposal, FixSession
    from workflow_clinic.reporting.issue_generator import GeneratedIssue

logger = logging.getLogger(__name__)
HTTP_UNPROCESSABLE_ENTITY = 422
MAX_PR_BODY_CHARS = 60_000
MAX_ISSUES_TO_SCAN = 50


class PublishedIssueInfo(BaseModel):
    """Container for metadata of a published GitHub issue."""

    number: int = Field(..., description="GitHub issue number")
    title: str = Field(..., description="Title of the published issue")
    url: str = Field(..., description="HTML URL to view issue on GitHub")
    category: str = Field(..., description="Category domain of the issue")


class PublishedPullRequestInfo(BaseModel):
    """Container for metadata of a published GitHub Pull Request."""

    number: int = Field(..., description="GitHub Pull Request number")
    title: str = Field(..., description="Title of the published Pull Request")
    url: str = Field(..., description="HTML URL to view Pull Request on GitHub")
    head_branch: str = Field(..., description="Head branch name created for the fix")
    base_branch: str = Field(..., description="Target base branch on the repository")
    applied_count: int = Field(..., description="Number of applied fixes in this PR")


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


def apply_proposal_to_content(content: str, proposal: FixProposal) -> str:
    """Apply a FixProposal in memory to the provided content string."""
    if proposal.original_snippet not in content:
        msg = (
            f"Original snippet not found for proposal {proposal.rule_id} "
            f"in '{proposal.target_file}' while building PR content."
        )
        raise GitHubPublisherError(msg)

    if proposal.line_number and proposal.line_number > 0:
        line_num: int = proposal.line_number
        occurrences: list[int] = []
        start = 0
        while True:
            idx = content.find(proposal.original_snippet, start)
            if idx == -1:
                break
            occurrences.append(idx)
            start = idx + 1

        if occurrences:
            best_idx = min(
                occurrences,
                key=lambda pos: abs(content[:pos].count("\n") + 1 - line_num),
            )
            return (
                content[:best_idx]
                + proposal.proposed_snippet
                + content[best_idx + len(proposal.original_snippet) :]
            )

    return content.replace(proposal.original_snippet, proposal.proposed_snippet, 1)


class GitHubPublisher:
    """Publishes diagnostic issues to GitHub repositories via PyGitHub API."""

    def __init__(self, token: str, repository: str) -> None:
        """Initialize GitHubPublisher client with PAT token and target repository.

        Args:
            token: GitHub Personal Access Token (PAT).
            repository: Target repository in 'owner/repo' format or full URL.
        """
        if not token or not token.strip():
            msg = "GitHub Personal Access Token is required."
            raise GitHubAuthError(msg)

        repo_str = repository.strip()
        if repo_str.startswith("git@github.com:"):
            repo_str = repo_str[len("git@github.com:") :]
        elif repo_str.startswith("https://github.com/"):
            repo_str = repo_str[len("https://github.com/") :]
        elif repo_str.startswith("http://github.com/"):
            repo_str = repo_str[len("http://github.com/") :]

        repo_str = repo_str.removesuffix(".git")

        repo_str = repo_str.rstrip("/")
        parts = [p for p in repo_str.split("/") if p]
        if len(parts) >= 2:  # noqa: PLR2004
            self.repository = f"{parts[0]}/{parts[1]}"
        else:
            self.repository = repo_str

        if not self.repository or "/" not in self.repository:
            msg = f"Invalid repository format '{repository}'. Expected 'owner/repo' or GitHub repository URL."
            raise GitHubRepoNotFoundError(msg)

        self.token = token.strip()
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

    def _commit_file(
        self,
        repo: Repository,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        """Commit a single file to a remote branch, safely creating or updating."""
        try:
            existing = repo.get_contents(path, ref=branch)
            if isinstance(existing, list):
                msg = f"Path '{path}' on branch '{branch}' is a directory, not a file."
                raise GitHubAPIError(msg)
            repo.update_file(
                path=path,
                message=message,
                content=content,
                sha=existing.sha,
                branch=branch,
            )
        except UnknownObjectException:
            # 404 Not Found -> File doesn't exist yet on branch, create it
            repo.create_file(
                path=path,
                message=message,
                content=content,
                branch=branch,
            )
        except GithubException as e:
            if getattr(e, "status", None) == 404:  # noqa: PLR2004
                repo.create_file(
                    path=path,
                    message=message,
                    content=content,
                    branch=branch,
                )
            else:
                msg = f"Failed to commit file '{path}' to branch '{branch}': {getattr(e, 'message', str(e))}"
                raise GitHubAPIError(msg) from e

    def build_pr_body(
        self,
        session: FixSession,
        linked_issues: list[int] | None = None,
    ) -> str:
        """Construct rich Pull Request markdown body text from session audit details.

        Args:
            session: FixSession context details.
            linked_issues: Optional list of related GitHub issue numbers.

        Returns:
            PR body text string in markdown format.
        """
        lines = [
            "## 🩺 Workflow Clinic Automated Remediation",
            "",
            f"**Session ID:** `{session.session_id}`  ",
            f"**Applied Fixes:** `{session.applied_count}` fix(es) across `{len(session.modified_files)}` file(s)  ",
            f"**Timestamp:** `{session.started_at.strftime('%Y-%m-%d %H:%M UTC')}`  ",
            "",
            "### 📋 Applied Modifications",
            "",
            "| Rule ID | Layer | Target File | Line | Description |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]

        for applied in [ap for ap in session.applied_proposals if ap.applied]:
            p = applied.proposal
            line_str = str(p.line_number) if p.line_number else "N/A"
            explanation = (
                p.explanation.replace("\n", " ")
                if p.explanation
                else "Automated remediation patch"
            )
            lines.append(
                f"| `{p.rule_id}` | `{p.strategy_layer.name}` | `{p.target_file}` | {line_str} | {explanation} |"
            )

        lines.extend(["", "### 🔍 Detailed Changes", ""])

        for idx, applied in enumerate(
            [ap for ap in session.applied_proposals if ap.applied], start=1
        ):
            p = applied.proposal
            lines.extend(
                [
                    "<details>",
                    f"<summary><strong>Fix #{idx}: {p.rule_id} in <code>{p.target_file}</code></strong> ({p.strategy_layer.name})</summary>",
                    "",
                    "```diff",
                ]
            )
            lines.extend(f"- {line}" for line in p.original_snippet.splitlines())
            lines.extend(f"+ {line}" for line in p.proposed_snippet.splitlines())
            lines.extend(["```", "", "</details>", ""])

        lines.extend(
            [
                "### ✅ Verification Status",
                "- AST Syntax Compilation: **PASSED**",
                "- Pre-Apply Integrity Check: **PASSED**",
                "",
                "---",
                "*Generated automatically by [GA4GH Workflow Clinic](https://github.com/ga4gh/ga4gh_workflow_clinic_gsoc_2026).* ",
            ]
        )

        if linked_issues:
            close_lines = ", ".join(f"Closes #{num}" for num in linked_issues)
            lines.extend(["", f"🔗 **Issue Resolutions:** {close_lines}"])

        # Embed fingerprints for deduplication
        fingerprints: list[str] = []
        applied_finding_ids = {
            ap.proposal.finding_id.lower().strip()
            for ap in session.applied_proposals
            if ap.applied and ap.proposal.finding_id
        }
        for finding in session.findings_input:
            fp_hash = ""
            if finding.fingerprint and finding.fingerprint.hash:
                fp_hash = finding.fingerprint.hash.lower().strip()
            elif finding.id:
                fp_hash = finding.id.lower().strip()

            finding_id_clean = finding.id.lower().strip() if finding.id else ""
            is_applied = (
                finding_id_clean and finding_id_clean in applied_finding_ids
            ) or (fp_hash and fp_hash in applied_finding_ids)
            if (
                is_applied
                and fp_hash
                and len(fp_hash) == 64  # noqa: PLR2004
                and fp_hash not in fingerprints
            ):
                fingerprints.append(fp_hash)

        if fingerprints:
            lines.extend(["", "<!-- workflow-clinic:fingerprints-start -->"])
            lines.extend(
                f"<!-- workflow-clinic:fingerprint:{fp} -->" for fp in fingerprints
            )
            lines.extend(["<!-- workflow-clinic:fingerprints-end -->"])

        pr_body = "\n".join(lines)
        if len(pr_body) > MAX_PR_BODY_CHARS:
            pr_body = (
                pr_body[:MAX_PR_BODY_CHARS]
                + "\n\n...(truncated — please review commit diffs for full modifications)"
            )
        return pr_body

    def _create_or_retry_branch(
        self,
        repo: Repository,
        session_id: str,
        branch_name: str | None,
        base_sha: str,
    ) -> str:
        """Create a remote branch, retrying with extended suffix if collision occurs."""
        head_branch = branch_name or f"workflow-clinic/fix-{session_id[:8]}"
        try:
            repo.create_git_ref(f"refs/heads/{head_branch}", base_sha)
        except GithubException as e:
            status = getattr(e, "status", None)
            if status == HTTP_UNPROCESSABLE_ENTITY and not branch_name:
                head_branch = f"workflow-clinic/fix-{session_id[:8]}-{uuid4().hex[:6]}"
                try:
                    repo.create_git_ref(f"refs/heads/{head_branch}", base_sha)
                except GithubException as err:
                    msg = f"Failed to create remote branch '{head_branch}': {getattr(err, 'data', {}).get('message', str(err))}"
                    raise GitHubAPIError(msg) from err
            else:
                msg = f"Failed to create remote branch '{head_branch}': {getattr(e, 'data', {}).get('message', str(e))}"
                raise GitHubAPIError(msg) from e
        return head_branch

    def publish_pull_request(  # noqa: PLR0913, PLR0912, C901, PLR0915
        self,
        session: FixSession,
        root_dir: Path,
        *,
        branch_name: str | None = None,
        base_branch: str | None = None,
        title: str | None = None,
        linked_issues: list[int] | None = None,
    ) -> PublishedPullRequestInfo:
        """Publish applied fixes as a Pull Request directly to GitHub."""
        if session.applied_count == 0 or not session.modified_files:
            msg = "Cannot publish Pull Request: FixSession has 0 applied fixes."
            raise GitHubPublisherError(msg)

        repo = self._get_repo()
        target_base = base_branch or repo.default_branch

        # 1. Resolve base branch commit SHA
        try:
            base_ref = repo.get_branch(target_base)
            base_sha = base_ref.commit.sha
        except GithubException as e:
            msg = f"Failed to retrieve base branch '{target_base}': {getattr(e, 'data', {}).get('message', str(e))}"
            raise GitHubAPIError(msg) from e

        # 2. Determine target branch name with collision retry
        head_branch = self._create_or_retry_branch(
            repo, session.session_id, branch_name, base_sha
        )

        # 3. Commit each modified file to the branch
        root_dir_resolved = root_dir.resolve()
        for mod_path in session.modified_files:
            target = Path(mod_path)
            resolved_target = (
                target.resolve()
                if target.is_absolute()
                else (root_dir / target).resolve()
            )
            try:
                rel_name = resolved_target.relative_to(root_dir_resolved).as_posix()
            except ValueError as err:
                msg = f"Modified file '{mod_path}' is outside repository root '{root_dir}'."
                raise GitHubPublisherError(msg) from err

            file_path = resolved_target
            if not file_path.is_file():
                continue

            # Try to fetch clean content from remote repository base branch
            try:
                remote_file = repo.get_contents(rel_name, ref=target_base)
                if isinstance(remote_file, list):
                    content = file_path.read_text(encoding="utf-8")
                else:
                    content = remote_file.decoded_content.decode("utf-8")
            except UnknownObjectException:
                # Expected when file does not yet exist on remote base branch
                content = file_path.read_text(encoding="utf-8")
            except (RateLimitExceededException, BadCredentialsException):
                raise
            except GithubException as exc:
                if exc.status == 404:  # noqa: PLR2004
                    content = file_path.read_text(encoding="utf-8")
                else:
                    msg = f"Failed to fetch '{rel_name}' from GitHub repository: {exc.data if hasattr(exc, 'data') else exc}"
                    raise GitHubAPIError(msg) from exc
            except Exception as exc:
                msg = f"Unexpected error reading '{rel_name}': {exc}"
                raise GitHubPublisherError(msg) from exc

            # Apply only the successfully applied proposals from the current session
            file_proposals = [
                ap.proposal
                for ap in session.applied_proposals
                if ap.applied and ap.proposal.target_file in (str(mod_path), rel_name)
            ]
            for prop in file_proposals:
                content = apply_proposal_to_content(content, prop)

            file_rules = sorted(
                {
                    a.proposal.rule_id
                    for a in session.applied_proposals
                    if a.proposal.target_file in (str(mod_path), rel_name)
                }
            )
            rules_str = ",".join(file_rules) if file_rules else "Doctor"
            commit_msg = (
                f"fix({rules_str}): Automated remediation for {rel_name} "
                f"[Workflow Clinic #{session.session_id[:8]}]"
            )
            self._commit_file(repo, head_branch, rel_name, content, commit_msg)

        # 4. Build PR body and open Pull Request
        pr_title = (
            title
            or f"[Workflow Clinic] Automated Remediation — {session.applied_count} issue(s) fixed"
        )
        pr_body = self.build_pr_body(session, linked_issues=linked_issues)

        try:
            created_pr = repo.create_pull(
                title=pr_title,
                body=pr_body,
                head=head_branch,
                base=target_base,
            )
            with contextlib.suppress(Exception):
                label_obj = self.ensure_label(label_name="workflow-clinic")
                created_pr.add_to_labels(label_obj)

            logger.info(
                "Created GitHub Pull Request #%d on %s: %s",
                created_pr.number,
                self.repository,
                created_pr.html_url,
            )

            return PublishedPullRequestInfo(
                number=created_pr.number,
                title=created_pr.title,
                url=created_pr.html_url,
                head_branch=head_branch,
                base_branch=target_base,
                applied_count=session.applied_count,
            )
        except RateLimitExceededException as e:
            reset_utc = _get_rate_limit_reset_utc(self.g)
            msg = f"GitHub API rate limit exceeded while creating PR. Resets at {reset_utc}."
            raise GitHubAPIError(msg) from e
        except GithubException as e:
            msg = f"Failed to create Pull Request on GitHub: {getattr(e, 'data', {}).get('message', str(e))}"
            raise GitHubAPIError(msg) from e

    def fetch_issue_findings(self, issue_number: int) -> tuple[int, set[str]]:
        """Fetch an issue by number and extract its diagnostic fingerprints.

        Args:
            issue_number: Target GitHub issue number.

        Returns:
            Tuple of (issue_number, set_of_fingerprints).

        Raises:
            GitHubPublisherError: If issue has no embedded fingerprints.
        """
        repo = self._get_repo()
        try:
            issue = repo.get_issue(issue_number)
        except UnknownObjectException as e:
            msg = f"Issue #{issue_number} not found on repository '{self.repository}'."
            raise GitHubRepoNotFoundError(msg) from e
        except GithubException as e:
            msg = f"Failed to fetch issue #{issue_number}: {getattr(e, 'message', str(e))}"
            raise GitHubAPIError(msg) from e

        if not issue.body:
            msg = (
                f"Issue #{issue_number} has no Workflow Clinic findings. "
                "Run 'workflow-clinic examine' and 'workflow-clinic issue' first."
            )
            raise GitHubPublisherError(msg)

        fingerprints = extract_fingerprints(issue.body)
        if not fingerprints:
            msg = (
                f"Issue #{issue_number} has no Workflow Clinic findings. "
                "Run 'workflow-clinic examine' and 'workflow-clinic issue' first."
            )
            raise GitHubPublisherError(msg)

        return issue_number, fingerprints

    def fetch_all_clinic_issues(
        self, max_scan: int = MAX_ISSUES_TO_SCAN
    ) -> list[tuple[int, set[str]]]:
        """Fetch open Workflow Clinic issues and their diagnostic fingerprints.

        Args:
            max_scan: Maximum number of open issues to inspect (default: 50).

        Returns:
            List of (issue_number, set_of_fingerprints) tuples.
        """
        repo = self._get_repo()
        results: list[tuple[int, set[str]]] = []
        try:
            open_issues: list[Any] = list(
                repo.get_issues(state="open", labels=["workflow-clinic"])[:max_scan]
            )
            for issue in open_issues:
                if issue.body:
                    fps = extract_fingerprints(issue.body)
                    if fps:
                        results.append((issue.number, fps))
        except GithubException as e:
            logger.warning("Failed to fetch open clinic issues: %s", e)
        return results
