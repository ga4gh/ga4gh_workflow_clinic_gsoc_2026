"""Command-line interface (CLI) definition for Workflow Clinic.

This module houses the Typer application, global option callbacks,
and CLI command routing.
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from workflow_clinic import __version__
from workflow_clinic.critic import AICriticAgent
from workflow_clinic.critic.agent import check_model_api_key
from workflow_clinic.doctor import DoctorRunner
from workflow_clinic.exceptions import (
    InvalidWorkflowError,
    ParserError,
    UnsupportedWorkflowError,
)
from workflow_clinic.models.diagnosis import (
    DiagnosisReport,
    Finding,
)
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.reporting import (
    GitHubPublisher,
    GitHubPublisherError,
    filter_new_findings,
    generate_issues,
)
from workflow_clinic.rules import Severity
from workflow_clinic.services import (
    ExamineCallbacks,
    ExamineConfig,
    ExamineCoordinator,
    ExamineDependencies,
    ExamineResult,
    FixCallbacks,
    FixConfig,
    FixCoordinator,
    FixDependencies,
    FixResult,
)
from workflow_clinic.services.coordinator import resolve_model
from workflow_clinic.utils import clone_remote_repo, is_remote_url

logger = logging.getLogger(__name__)

# Create the Typer application instance
app = typer.Typer(
    name="workflow-clinic",
    help="AI-Powered Cloudification of Bioinformatics Workflows",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)


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


_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.INFO: "blue",
    Severity.WARNING: "yellow",
    Severity.ERROR: "red",
}

PROVIDER_MODEL_MAP = [
    ("GEMINI_API_KEY", "gemini/gemini-3.6-flash"),
    ("OPENAI_API_KEY", "gpt-4o-mini"),
    ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-20240620"),
    ("MISTRAL_API_KEY", "mistral/mistral-large-latest"),
    ("GROQ_API_KEY", "groq/llama-3.1-8b-instant"),
    ("COHERE_API_KEY", "cohere/command-r"),
]


def _resolve_model(explicit_model: str | None, api_key: str | None) -> str:
    """Resolve the LiteLLM model using CLI flags, env vars, or auto-detection."""
    return resolve_model(explicit_model, api_key, custom_logger=logger)


def _display_enhance_status(result: ExamineResult, count: int) -> None:
    """Display AI Critic enhance summary status in terminal."""
    if result.enhance_failed:
        if result.enhance_error:
            err_console.print(
                f"[yellow]AI Critic enhancement failed: {result.enhance_error}. Using offline fallback.[/yellow]"
            )
        err_console.print(
            f"[yellow]⚠️  AI Critic Enhancement Failed: Using offline Knowledge Store. "
            f"All {count} findings using offline Knowledge Store.[/yellow]"
        )
    elif not result.has_key:
        console.print(
            f"[green]✓[/green] Offline remediation guidance added to {count}/{count} findings (Knowledge Store fallback)"
        )
    elif result.fallback_count == count and count > 0:
        console.print(
            f"[yellow]⚠️  AI Critic Enhancement Failed: All {count} findings fell back to the offline Knowledge Store.[/yellow]"
        )
    elif result.fallback_count > 0:
        console.print(
            f"[yellow]⚠️  AI Critic Partial Failure: {result.fallback_count}/{count} findings fell back to the offline Knowledge Store.[/yellow]"
        )
    else:
        console.print(
            f"[green]✓[/green] AI remediation guidance added to {count}/{count} findings (model: {result.resolved_model})"
        )


def _display_examine_results(result: ExamineResult, *, enhance: bool) -> None:
    """Render diagnostic table, counts, and exit with code."""
    findings = result.report.findings
    if not findings:
        console.print(
            "\n[bold green]✓[/bold green] No issues found — "
            "workflow is clean and cloud-ready!\n"
        )
        raise typer.Exit(code=0)

    table = Table(
        title=f"Diagnostic Findings for '{result.bundle.metadata.name}'",
        show_lines=True,
    )
    table.add_column("Severity", style="bold", width=10)
    table.add_column("Rule", width=10)
    table.add_column("Location", style="cyan", no_wrap=True)
    table.add_column("Process", width=18)
    table.add_column("Message")

    for finding in findings:
        try:
            sev_enum = Severity(finding.severity.lower())
            color = _SEVERITY_COLORS.get(sev_enum, "white")
        except ValueError:
            color = "white"

        loc_str = ""
        if finding.file_path:
            loc_name = Path(finding.file_path).name
            loc_str = (
                f"{loc_name}:{finding.line_number}" if finding.line_number else loc_name
            )

        table.add_row(
            f"[{color}]{finding.severity.upper()}[/{color}]",
            finding.rule_id,
            loc_str or "—",
            finding.process_name or "—",
            finding.message,
        )

    console.print()
    console.print(table)

    n_err = sum(1 for f in findings if f.severity.lower() == "error")
    n_warn = sum(1 for f in findings if f.severity.lower() == "warning")
    n_info = sum(1 for f in findings if f.severity.lower() == "info")
    console.print(
        f"\n[bold]Summary:[/bold] {n_err} error(s), "
        f"{n_warn} warning(s), {n_info} info(s)"
    )

    if enhance:
        _display_enhance_status(result, len(findings))

    console.print()
    exit_code = 1 if n_err > 0 else 0
    raise typer.Exit(code=exit_code)


@app.command()
def list_models() -> None:
    """List supported LiteLLM model strings and their required environment variables."""
    table = Table(title="Supported AI Models")
    table.add_column("Provider Key")
    table.add_column("Default Model")
    for env_var, model in PROVIDER_MODEL_MAP:
        table.add_row(env_var, model)
    console.print(table)


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

    _display_examine_results(result, enhance=enhance)


def parse_selection(raw: str, max_index: int) -> list[int]:
    """Parse interactive selection string into 0-indexed integer list.

    Supports comma lists ("1, 3"), ranges ("1-3"), "all", and default empty.
    Ignores out-of-bounds indices.
    """

    clean = raw.strip().lower()
    if not clean or clean in ("all", "a"):
        return list(range(max_index))

    indices: list[int] = []
    for item in clean.split(","):
        part = item.strip()
        if not part:
            continue
        if "-" in part:
            try:
                start_str, end_str = part.split("-", maxsplit=1)
                start = int(start_str.strip())
                end = int(end_str.strip())
                for i in range(start, end + 1):
                    idx = i - 1
                    if 0 <= idx < max_index and idx not in indices:
                        indices.append(idx)
            except ValueError:
                continue
        else:
            try:
                val = int(part)
                idx = val - 1
                if 0 <= idx < max_index and idx not in indices:
                    indices.append(idx)
            except ValueError:
                continue

    return indices


@app.command(name="create-issue")
def create_issue(  # noqa: C901, PLR0912, PLR0915
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
    target_path = Path(target).resolve()
    diag_path = target_path if target_path.is_file() else target_path / "diagnosis.json"

    if not diag_path.exists():
        err_console.print(
            f"[red]Error:[/red] Could not find '[bold]{diag_path.name}[/bold]' at '{escape(str(diag_path.parent))}'."
        )
        err_console.print(
            f"[bold]Tip:[/bold] Run [green]workflow-clinic examine {escape(target)}[/green] first to generate diagnostic findings."
        )
        raise typer.Exit(code=1)

    try:
        raw_json = json.loads(diag_path.read_text(encoding="utf-8"))
        report = DiagnosisReport.model_validate(raw_json)
    except Exception as e:
        err_console.print(
            f"[red]Error:[/red] Failed to parse diagnosis report '{escape(str(diag_path))}': {escape(str(e))}"
        )
        raise typer.Exit(code=1) from e

    token_val = token or os.getenv("GITHUB_TOKEN")
    repo_val = repo or os.getenv("GITHUB_REPOSITORY")
    use_github = not local and bool(token_val and repo_val)

    existing_fingerprints: set[str] = set()
    publisher: GitHubPublisher | None = None

    if not local and (token_val or repo_val):
        if not token_val:
            err_console.print(
                "[red]Error:[/red] GitHub repository specified but GitHub token is missing. Provide via --token or GITHUB_TOKEN."
            )
            raise typer.Exit(code=1)
        if not repo_val:
            err_console.print(
                "[red]Error:[/red] GitHub token specified but repository is missing. Provide via --repo or GITHUB_REPOSITORY."
            )
            raise typer.Exit(code=1)

        try:
            publisher = GitHubPublisher(token=token_val, repository=repo_val)
            existing_fingerprints = publisher.fetch_active_fingerprints()
        except GitHubPublisherError as e:
            err_console.print(
                f"[red]GitHub Authentication/API Error:[/red] {escape(str(e))}"
            )
            raise typer.Exit(code=1) from e

    new_findings = filter_new_findings(report.findings, existing_fingerprints)
    report_to_process = DiagnosisReport(
        workflow_name=report.workflow_name,
        findings=new_findings,
    )

    generated_issues = generate_issues(report_to_process)
    if not generated_issues:
        console.print(
            "\n[bold green]✓[/bold green] No new actionable findings to report!\n"
        )
        raise typer.Exit(code=0)

    # Render interactive selection table
    table = Table(
        title=f"Diagnostic Issue Groups for '{report.workflow_name}'",
        show_lines=True,
    )
    table.add_column("Option", style="bold cyan", width=8)
    table.add_column("Severity", style="bold", width=10)
    table.add_column("Category", width=20)
    table.add_column("Locations")

    for idx, iss in enumerate(generated_issues, 1):
        sev_color = "red" if iss.severity in ("CRITICAL", "HIGH", "ERROR") else "yellow"
        table.add_row(
            f"[{idx}]",
            f"[{sev_color}]{iss.severity}[/{sev_color}]",
            iss.category.replace("_", " ").title(),
            f"{len(iss.fingerprints)} location(s)",
        )

    console.print()
    console.print(table)

    # Determine selected indices
    is_tty = sys.stdin.isatty()
    if all_issues or not is_tty:
        if not is_tty and not all_issues:
            console.print(
                "[yellow]Non-interactive terminal detected — auto-selecting all findings.[/yellow]"
            )
        selected_indices = list(range(len(generated_issues)))
    else:
        prompt_msg = (
            f"Select issues to publish (e.g. 1,{len(generated_issues)} or all) [all]"
        )
        raw_input_str = typer.prompt(prompt_msg, default="all")
        selected_indices = parse_selection(raw_input_str, len(generated_issues))
        if not selected_indices:
            err_console.print("[yellow]No valid issues selected. Exiting.[/yellow]")
            raise typer.Exit(code=0)

    selected_issues = [generated_issues[i] for i in selected_indices]
    combined_markdown = "\n\n---\n\n".join(iss.body for iss in selected_issues)

    if dry_run:
        console.print("\n[cyan]--- Issue Markdown Payload (Dry Run) ---[/cyan]\n")
        console.print(combined_markdown)
        console.print()
        raise typer.Exit(code=0)

    if preview:
        console.print("\n[cyan]--- Issue Markdown Preview ---[/cyan]\n")
        console.print(Markdown(combined_markdown))
        console.print()

    # Online GitHub Publishing or Local File Export Fallback
    if use_github and publisher is not None:
        published_results = []
        for iss in selected_issues:
            try:
                pub_info = publisher.publish_issue(iss)
                published_results.append(pub_info)
            except GitHubPublisherError as e:
                err_console.print(
                    f"[red]Failed to publish issue '{iss.title}':[/red] {escape(str(e))}"
                )

        if published_results:
            console.print(
                f"\n[bold green]✓[/bold green] Successfully published {len(published_results)} issue(s) to GitHub repository '[bold]{publisher.repository}[/bold]':\n"
            )
            pub_table = Table(show_lines=True)
            pub_table.add_column("Issue #", style="bold cyan", width=10)
            pub_table.add_column("Title", width=35)
            pub_table.add_column("URL", overflow="fold")

            for res in published_results:
                pub_table.add_row(
                    f"#{res.number}",
                    res.title,
                    f"[link={res.url}]{res.url}[/link]",
                )
            console.print(pub_table)
            console.print("\n[bold]Direct Links:[/bold]")
            for res in published_results:
                console.print(
                    f" • [bold cyan]#{res.number}[/bold cyan]: {res.url}",
                    soft_wrap=True,
                )
            console.print()
        else:
            err_console.print(
                "[red]Error:[/red] Failed to publish any issues to GitHub."
            )
            raise typer.Exit(code=1)
    else:
        # Export to local output file
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(combined_markdown + "\n", encoding="utf-8")
            console.print(
                f"\n[bold green]✓[/bold green] Exported {len(selected_issues)} issue group(s) to [bold]{escape(str(output))}[/bold]\n"
            )
        except OSError as e:
            err_console.print(
                f"[red]Error:[/red] Could not write issue file to '{escape(str(output))}': {escape(str(e))}"
            )
            raise typer.Exit(code=1) from e


def _display_fix_results(result: FixResult) -> None:
    """Render proposed fix diffs or completion summary and exit."""
    session = result.session
    if not session.proposals:
        console.print(
            "\n[bold yellow]![/bold yellow] No registered fixers available for the selected findings yet.\n"
        )
        raise typer.Exit(code=0)

    if result.dry_run:
        console.print(
            f"\n[cyan]--- Workflow Doctor Dry Run ({len(session.proposals)} proposed fix(es)) ---[/cyan]\n"
        )
        diff_table = Table(show_lines=True)
        diff_table.add_column("Rule", style="bold cyan", width=8)
        diff_table.add_column("Target File", width=20)
        diff_table.add_column("Layer Strategy", style="bold yellow", width=14)
        diff_table.add_column("Rationale", overflow="fold")

        for prop in session.proposals:
            prop_path = Path(prop.target_file)
            rel_file = (
                os.path.relpath(prop_path, result.root_dir)
                if prop_path.is_absolute()
                else str(prop_path)
            )
            diff_table.add_row(
                prop.rule_id,
                rel_file,
                prop.strategy_layer.name,
                prop.explanation,
            )

        console.print(diff_table)
        console.print(
            f"\n[bold green]✓[/bold green] Dry-run complete for session "
            f"[bold cyan]{session.session_id[:8]}[/bold cyan] ({len(session.proposals)} proposal(s) ready).\n"
        )
        raise typer.Exit(code=0)

    console.print(
        f"\n[bold green]✓[/bold green] Workflow Doctor completed session "
        f"[bold cyan]{session.session_id[:8]}[/bold cyan]: "
        f"{session.applied_count}/{len(session.proposals)} fix(es) applied successfully.\n"
    )


def _verify_github_repo(token: str | None, repo: str | None) -> None:
    """Validate GitHub token and repository credentials if provided."""
    token_val = token or os.getenv("GITHUB_TOKEN")
    repo_val = repo or os.getenv("GITHUB_REPOSITORY")
    if not repo_val and not token:
        return

    if not token_val:
        err_console.print(
            "[red]Error:[/red] GitHub repository specified but GitHub token is missing. Provide via --token or GITHUB_TOKEN."
        )
        raise typer.Exit(code=1)
    if not repo_val:
        err_console.print(
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
        err_console.print(
            f"[red]GitHub Authentication/API Error:[/red] {escape(str(e))}"
        )
        raise typer.Exit(code=1) from e


def _prompt_category_selection(
    coordinator: FixCoordinator,
    actionable_findings: list[Finding],
    workflow_name: str,
) -> list[Finding]:
    """Display interactive category table and prompt user for selection."""
    grouped = coordinator.group_findings_by_category(actionable_findings)
    categories = list(grouped.keys())

    table = Table(
        title=f"Diagnostic Categories to Repair for '{workflow_name}'",
        show_lines=True,
    )
    table.add_column("Option", style="bold cyan", width=8)
    table.add_column("Category Domain", width=22)
    table.add_column("Rules", style="bold green", width=16)
    table.add_column("Findings Count", style="bold yellow", width=16)

    for idx, cat in enumerate(categories, 1):
        cat_findings = grouped[cat]
        cat_rules = sorted({f.rule_id for f in cat_findings if f.rule_id})
        rules_str = ", ".join(cat_rules) if cat_rules else "-"
        table.add_row(
            f"[{idx}]",
            cat.replace("_", " ").title(),
            rules_str,
            f"{len(cat_findings)} issue(s)",
        )

    console.print()
    console.print(table)

    prompt_msg = (
        f"Select category domains to fix (e.g. 1,{len(categories)} or all) [all]"
    )
    raw_input_str = typer.prompt(prompt_msg, default="all")
    selected_indices = parse_selection(raw_input_str, len(categories))
    if not selected_indices:
        err_console.print(
            "[yellow]No valid category domains selected. Exiting.[/yellow]"
        )
        raise typer.Exit(code=0)

    selected_categories = {categories[i] for i in selected_indices}
    selected_set = {id(f) for cat in selected_categories for f in grouped[cat]}
    return [f for f in actionable_findings if id(f) in selected_set]


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

    _verify_github_repo(token, repo)

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
        selected_findings = _prompt_category_selection(
            coordinator, actionable_findings, report.workflow_name
        )

    result = coordinator.execute(selected_findings=selected_findings)
    _display_fix_results(result)
