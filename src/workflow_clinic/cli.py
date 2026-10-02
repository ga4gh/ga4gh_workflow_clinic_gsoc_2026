"""Command-line interface (CLI) definition for Workflow Clinic.

This module houses the Typer application, global option callbacks,
and CLI command routing.
"""

import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.markup import escape

from workflow_clinic import __version__
from workflow_clinic.critic import AICriticAgent
from workflow_clinic.critic.agent import check_model_api_key
from workflow_clinic.doctor import DoctorRunner
from workflow_clinic.exceptions import (
    InvalidWorkflowError,
    ParserError,
    UnsupportedWorkflowError,
)
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.reporting import (
    GitHubPublisher,
    GitHubPublisherError,
)
from workflow_clinic.services import (
    ExamineCallbacks,
    ExamineConfig,
    ExamineCoordinator,
    ExamineDependencies,
    FixCallbacks,
    FixConfig,
    FixCoordinator,
    FixDependencies,
    PublishCallbacks,
    PublishConfig,
    PublishCoordinator,
    PublishDependencies,
)
from workflow_clinic.services.coordinator import resolve_model
from workflow_clinic.ui import (
    console,
    display_enhance_status,
    display_examine_results,
    display_fix_results,
    display_models_table,
    display_publish_results,
    err_console,
    parse_selection,  # noqa: F401
    prompt_category_selection,
    prompt_issue_selection,
    verify_github_repo,
)
from workflow_clinic.utils import clone_remote_repo, is_remote_url
from workflow_clinic.utils.llm import PROVIDER_MODEL_MAP  # noqa: F401

logger = logging.getLogger(__name__)

# Backward-compatibility aliases for tests and external callers
_display_examine_results = display_examine_results
_display_enhance_status = display_enhance_status
_display_fix_results = display_fix_results
_display_publish_results = display_publish_results
_prompt_issue_selection = prompt_issue_selection
_prompt_category_selection = prompt_category_selection
_verify_github_repo = verify_github_repo

# Create the Typer application instance
app = typer.Typer(
    name="workflow-clinic",
    help="AI-Powered Cloudification of Bioinformatics Workflows",
    no_args_is_help=True,
)


def setup_logging(*, verbose: bool) -> None:
    """Configure the standard logging handler across the application.

    By default, sets the log level to WARNING. If verbose is True, sets the
    log level to INFO.
    """
    log_level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
        force=True,
    )


def version_callback(value: bool) -> None:  # noqa: FBT001
    """Callback to print application version and exit."""
    if value:
        typer.echo(f"workflow-clinic version {__version__}")
        raise typer.Exit


@app.callback(invoke_without_command=True)
def main(
    version: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--version",
            "-V",
            callback=version_callback,
            is_eager=True,
            help="Print version and exit.",
        ),
    ] = False,
    verbose: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable verbose (INFO) logging.",
        ),
    ] = False,
) -> None:
    """Run the main command-line interface for Workflow Clinic."""
    _ = version
    setup_logging(verbose=verbose)


def _resolve_model(explicit_model: str | None, api_key: str | None) -> str:
    """Resolve the LiteLLM model using CLI flags, env vars, or auto-detection."""
    return resolve_model(explicit_model, api_key, custom_logger=logger)


@app.command()
def list_models() -> None:
    """List supported LiteLLM model strings and their required environment variables."""
    display_models_table()


@app.command()
def examine(
    target: Annotated[
        str,
        typer.Argument(
            help="Path to local workflow file/directory or remote GitHub repository URL.",
        ),
    ] = ".",
    parser_type: Annotated[
        str | None,
        typer.Option(
            "--type",
            "-t",
            help="Explicitly specify the parser type, bypassing auto-detection.",
        ),
    ] = None,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Path to save diagnosis JSON report (default: diagnosis.json).",
        ),
    ] = Path("diagnosis.json"),
    enhance: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--enhance",
            "-e",
            help="Enable AI Critic to audit for complex issues and generate remediation guidance for all findings.",
        ),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="LiteLLM model name (e.g. gpt-4o, gemini/gemini-3.6-flash).",
        ),
    ] = None,
    api_key: Annotated[
        str | None,
        typer.Option(
            "--api-key",
            "-k",
            help="Explicit API key override. Warning: Shell history might expose keys.",
        ),
    ] = None,
) -> None:
    """Examine a workflow for portability and cloud-readiness issues."""
    config = ExamineConfig(
        target=target,
        parser_type=parser_type,
        output=output,
        enhance=enhance,
        model=model,
        api_key=api_key,
    )
    callbacks = ExamineCallbacks(
        on_clone_start=lambda url: console.print(
            f"\n[cyan]Cloning remote repository from '{escape(url)}'...[/cyan]"
        ),
        on_scan_start=lambda name: console.print(
            f"\n[cyan]Scanning workflow at '{name}'...[/cyan]"
        ),
        on_audit_start=lambda: console.print(
            "[cyan]Performing AI Audit for new issues...[/cyan]"
        ),
        on_report_saved=lambda p: console.print(
            f"[green]✓[/green] Saved diagnosis report to [bold]{escape(str(p))}[/bold]"
        ),
    )
    dependencies = ExamineDependencies(
        clone_repo_fn=clone_remote_repo,
        detect_parser_fn=ParserRegistry.detect_parser,
        get_parser_fn=ParserRegistry.get_parser,
        load_dotenv_fn=load_dotenv,
        custom_logger=logger,
    )
    coordinator = ExamineCoordinator(
        config=config,
        callbacks=callbacks,
        dependencies=dependencies,
    )

    if enhance:
        load_dotenv(override=False)
        resolved_model = _resolve_model(model, api_key)
        if not check_model_api_key(resolved_model, api_key):
            err_console.print(
                "[yellow]Warning: --enhance requires an LLM API key for auditing and full remediation. "
                "Falling back to local knowledge store for remediation only.[/yellow]"
            )
            console.print(
                f"[yellow]Notice: No LLM API key found for model '{resolved_model}'. "
                f"Defaulting to local Knowledge Store fallback.[/yellow]"
            )

    try:
        with coordinator:
            result = coordinator.run()
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(code=2) from e
    except UnsupportedWorkflowError as e:
        err_console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(code=1) from e
    except (InvalidWorkflowError, ParserError) as e:
        if is_remote_url(target) and (
            "Failed to clone remote repository" in str(e)
            or "Git executable not found" in str(e)
        ):
            err_console.print(f"[red]Remote clone error:[/red] {escape(str(e))}")
        else:
            err_console.print(f"[red]Parse error:[/red] {escape(str(e))}")
            is_missing_dependency = isinstance(e, ParserError) or isinstance(
                getattr(e, "__cause__", None), ModuleNotFoundError
            )
            if is_missing_dependency:
                p_name = parser_type or "nextflow"
                install_cmd = f"pip install 'workflow-clinic[{p_name}]'"
                err_console.print(
                    f"[bold]Tip:[/bold] Try installing with: "
                    f"[green]{escape(install_cmd)}[/green]"
                )
        raise typer.Exit(code=1) from e
    except OSError as e:
        err_console.print(
            f"[red]Error:[/red] Could not write diagnosis report to "
            f"'{escape(str(output))}': {escape(str(e))}"
        )
        raise typer.Exit(code=1) from e

    display_examine_results(result, enhance=enhance)


@app.command(name="create-issue")
def create_issue(
    target: Annotated[
        str,
        typer.Argument(
            help="Path to local workflow directory or diagnosis.json file.",
        ),
    ] = ".",
    all_issues: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--all",
            "-y",
            help="Select all findings without interactive prompt.",
        ),
    ] = False,
    dry_run: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--dry-run",
            help="Print generated issue Markdown to stdout without saving to disk.",
        ),
    ] = False,
    preview: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--preview",
            help="Render a Markdown preview in terminal before writing to disk.",
        ),
    ] = False,
    token: Annotated[
        str | None,
        typer.Option(
            "--token",
            "-t",
            help="GitHub Personal Access Token (PAT). Overrides GITHUB_TOKEN env var.",
        ),
    ] = None,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo",
            "-r",
            help="Target GitHub repository in 'owner/repo' format. Overrides GITHUB_REPOSITORY env var.",
        ),
    ] = None,
    local: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--local",
            help="Force local Markdown file export without publishing to GitHub API.",
        ),
    ] = False,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output Markdown file path for local export (default: issue.md).",
        ),
    ] = Path("issue.md"),
) -> None:
    """Export grouped findings from diagnosis.json into GitHub issue markdown format or publish directly to GitHub."""
    config = PublishConfig(
        target=target,
        all_issues=all_issues,
        dry_run=dry_run,
        preview=preview,
        token=token,
        repo=repo,
        local=local,
        output=output,
    )
    callbacks = PublishCallbacks(
        on_error=lambda msg: err_console.print(f"[red]Error:[/red] {msg}"),
        on_warning=lambda msg: err_console.print(f"[yellow]{msg}[/yellow]"),
    )
    deps = PublishDependencies(publisher_factory=GitHubPublisher)
    coordinator = PublishCoordinator(config, callbacks=callbacks, dependencies=deps)

    try:
        diag_path = coordinator.resolve_diagnosis_path()
        report = coordinator.load_diagnosis(diag_path)
    except FileNotFoundError as e:
        target_path = Path(target).resolve()
        dp = (
            target_path
            if target_path.is_file() and target_path.suffix == ".json"
            else target_path / "diagnosis.json"
        )
        err_console.print(
            f"[red]Error:[/red] Could not find '[bold]{dp.name}[/bold]' at '{escape(str(dp.parent))}'."
        )
        err_console.print(
            f"[bold]Tip:[/bold] Run [green]workflow-clinic examine {escape(target)}[/green] first to generate diagnostic findings."
        )
        raise typer.Exit(code=1) from e
    except Exception as e:
        err_console.print(
            f"[red]Error:[/red] Failed to parse diagnosis report '{escape(str(target))}': {escape(str(e))}"
        )
        raise typer.Exit(code=1) from e

    try:
        publisher, _ = coordinator.resolve_publisher()
        existing_fingerprints = coordinator.fetch_active_fingerprints(publisher)
    except ValueError as e:
        err_console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(code=1) from e
    except GitHubPublisherError as e:
        err_console.print(
            f"[red]GitHub Authentication/API Error:[/red] {escape(str(e))}"
        )
        raise typer.Exit(code=1) from e

    generated_issues, _ = coordinator.get_actionable_issues(
        report, existing_fingerprints
    )

    if not generated_issues:
        console.print(
            "\n[bold green]✓[/bold green] No new actionable findings to report!\n"
        )
        raise typer.Exit(code=0)

    selected = prompt_issue_selection(
        generated_issues,
        workflow_name=report.workflow_name,
        all_issues=all_issues,
    )

    try:
        result = coordinator.execute(selected_issues=selected)
    except OSError as e:
        err_console.print(
            f"[red]Error:[/red] Could not write issue file to '{escape(str(output))}': {escape(str(e))}"
        )
        raise typer.Exit(code=1) from e

    display_publish_results(
        result,
        preview=preview,
        repo_name=publisher.repository if publisher else None,
    )


@app.command(name="fix")
def fix(
    target: Annotated[
        str,
        typer.Argument(
            help="Path to local workflow directory, diagnosis.json file, or target repository.",
        ),
    ] = ".",
    rule: Annotated[
        list[str] | None,
        typer.Option(
            "--rule",
            "-r",
            help="Filter fix proposals to specific rule IDs (e.g. -r W001 -r W002).",
        ),
    ] = None,
    all_issues: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--all",
            "-y",
            help="Select all findings to fix without interactive prompt.",
        ),
    ] = False,
    dry_run: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--dry-run",
            help="Render proposed code diffs and dry-run summary without modifying files on disk.",
        ),
    ] = False,
    enhance: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--enhance",
            "-e",
            help="Run hybrid 3-tier cascade with AI Critic to identify and repair complex semantic issues (AI001+).",
        ),
    ] = False,
    ai_only: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--ai-only",
            help="Force all findings (W001-W004, AI001+) to be repaired exclusively using AI/LLM.",
        ),
    ] = False,
    token: Annotated[
        str | None,
        typer.Option(
            "--token",
            "-t",
            help="GitHub Personal Access Token (PAT). Overrides GITHUB_TOKEN env var.",
        ),
    ] = None,
    repo: Annotated[
        str | None,
        typer.Option(
            "--repo",
            help="Target GitHub repository in 'owner/repo' format. Overrides GITHUB_REPOSITORY env var.",
        ),
    ] = None,
) -> None:
    """Automated Workflow Doctor engine for repairing diagnostic findings in Nextflow & Snakemake pipelines."""
    config = FixConfig(
        target=target,
        rules=rule,
        dry_run=dry_run,
        all_findings=all_issues,
        enhance=enhance,
        ai_only=ai_only,
        token=token,
        repo=repo,
    )
    callbacks = FixCallbacks(
        on_warning=lambda msg: console.print(f"[yellow]⚠️  {escape(msg)}[/yellow]"),
        on_ai_findings_discovered=lambda count: console.print(
            f"[bold cyan]AI Critic identified {count} enhancement finding(s).[/bold cyan]"
        ),
    )
    deps = FixDependencies(
        runner_factory=DoctorRunner,
        critic_factory=AICriticAgent,
        parser_registry=ParserRegistry,
        check_model_api_key_fn=check_model_api_key,
    )
    coordinator = FixCoordinator(config=config, callbacks=callbacks, dependencies=deps)

    try:
        diag_path, root_dir, target_file_filter = coordinator.resolve_paths()
    except FileNotFoundError as e:
        err_console.print(f"[red]Error:[/red] {escape(str(e))}")
        err_console.print(
            f"[bold]Tip:[/bold] Run [green]workflow-clinic examine {escape(target)}[/green] first to generate diagnostic findings."
        )
        raise typer.Exit(code=1) from e

    try:
        report = coordinator.load_diagnosis(diag_path)
    except Exception as e:
        err_console.print(
            f"[red]Error:[/red] Failed to parse diagnosis report '{escape(str(diag_path))}': {escape(str(e))}"
        )
        raise typer.Exit(code=1) from e

    verify_github_repo(token, repo)

    try:
        actionable_findings = coordinator.get_actionable_findings(
            report, root_dir, target_file_filter
        )
    except ValueError as e:
        err_console.print(f"[red]Error:[/red] {escape(str(e))}")
        raise typer.Exit(code=1) from e

    if not actionable_findings:
        if rule:
            console.print(
                f"\n[bold yellow]![/bold yellow] No findings matching rule filter(s): {', '.join({r.upper() for r in rule})}\n"
            )
        else:
            console.print(
                "\n[bold green]✓[/bold green] No actionable findings to fix!\n"
            )
        raise typer.Exit(code=0)

    # Interactive Category Selection
    is_tty = sys.stdin.isatty() or os.environ.get("FORCE_INTERACTIVE") == "1"
    if all_issues or not is_tty:
        if not is_tty and not all_issues:
            console.print(
                "[yellow]Non-interactive terminal detected — auto-selecting all findings for fix.[/yellow]"
            )
        selected_findings = actionable_findings
    else:
        selected_findings = prompt_category_selection(
            coordinator, actionable_findings, report.workflow_name
        )

    result = coordinator.execute(selected_findings=selected_findings)
    display_fix_results(result)
