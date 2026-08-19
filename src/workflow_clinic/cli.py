"""Command-line interface (CLI) definition for Workflow Clinic.

This module houses the Typer application, global option callbacks,
and CLI command routing.
"""

import json
import logging
import os
import sys
import tempfile
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
from workflow_clinic.exceptions import (
    InvalidWorkflowError,
    ParserError,
    UnsupportedWorkflowError,
)
from workflow_clinic.models.diagnosis import (
    DiagnosisReport,
)
from workflow_clinic.models.diagnosis import (
    Finding as DiagnosisFinding,
)
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.reporting import (
    GitHubPublisher,
    GitHubPublisherError,
    compute_fingerprint,
    filter_new_findings,
    generate_issues,
)
from workflow_clinic.rules import RuleRunner, Severity
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
    ("GEMINI_API_KEY", "gemini/gemini-2.5-flash"),
    ("OPENAI_API_KEY", "gpt-4o-mini"),
    ("ANTHROPIC_API_KEY", "claude-3-5-sonnet-20240620"),
    ("MISTRAL_API_KEY", "mistral/mistral-large-latest"),
    ("GROQ_API_KEY", "groq/llama-3.1-8b-instant"),
    ("COHERE_API_KEY", "cohere/command-r"),
]


def _resolve_model(explicit_model: str | None, api_key: str | None) -> str:
    """Resolve the LiteLLM model using CLI flags, env vars, or auto-detection."""
    if explicit_model:
        return explicit_model
    if clinic_model := os.getenv("CLINIC_MODEL"):
        return clinic_model
    for env_var, model_name in PROVIDER_MODEL_MAP:
        if os.getenv(env_var):
            return model_name
    if api_key:
        logger.warning(
            "--api-key provided without --model. Defaulting to gemini/gemini-2.5-flash."
        )
    return "gemini/gemini-2.5-flash"


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
def examine(  # noqa: C901, PLR0912, PLR0915
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
            help="Enhance findings with AI Critic remediation advice.",
        ),
    ] = False,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="LiteLLM model string to override default.",
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
    temp_dir_obj = None
    try:
        if is_remote_url(target):
            console.print(
                f"\n[cyan]Cloning remote repository from '{escape(target)}'...[/cyan]"
            )
            temp_dir_obj = tempfile.TemporaryDirectory()
            try:
                scan_path = clone_remote_repo(target, Path(temp_dir_obj.name))
            except ParserError as e:
                err_console.print(f"[red]Remote clone error:[/red] {escape(str(e))}")
                raise typer.Exit(code=1) from e
        else:
            scan_path = Path(target).resolve()
            if not scan_path.exists():
                err_console.print(
                    f"[red]Error:[/red] Path '{escape(target)}' does not exist."
                )
                raise typer.Exit(code=2)

        # 1. Detect parser
        if parser_type:
            parser_name = parser_type
        else:
            try:
                parser_name = ParserRegistry.detect_parser(scan_path)
            except UnsupportedWorkflowError as e:
                err_console.print(f"[red]Error:[/red] {escape(str(e))}")
                raise typer.Exit(code=1) from e

        logger.info("Detected parser: %s", parser_name)

        # 2. Parse workflow
        console.print(f"\n[cyan]Scanning workflow at '{scan_path.name}'...[/cyan]")
        try:
            parser = ParserRegistry.get_parser(parser_name)
            bundle = parser.parse(scan_path)
        except (InvalidWorkflowError, ParserError) as e:
            err_console.print(f"[red]Parse error:[/red] {escape(str(e))}")
            is_missing_dependency = isinstance(e, ParserError) or isinstance(
                e.__cause__, ModuleNotFoundError
            )
            if is_missing_dependency:
                install_cmd = f"pip install 'workflow-clinic[{parser_name}]'"
                err_console.print(
                    f"[bold]Tip:[/bold] Try installing with: "
                    f"[green]{escape(install_cmd)}[/green]"
                )
            raise typer.Exit(code=1) from e

        logger.info(
            "Parsed workflow '%s' with %d task(s)",
            bundle.metadata.name,
            len(bundle.tasks),
        )

        runner = RuleRunner()
        raw_findings = runner.run(bundle)

        findings = []
        for f in raw_findings:
            fp = compute_fingerprint(
                file_path=target,
                rule_id=f.rule_id,
                task_id=f.task_id,
                target_token=f.message,
            )
            f_dict = f.model_dump()
            f_dict["file_path"] = target
            f_dict["fingerprint"] = fp.model_dump()
            f_dict["id"] = fp.hash
            findings.append(DiagnosisFinding.model_validate(f_dict))

        report = DiagnosisReport(
            workflow_name=bundle.metadata.name,
            tasks_count=len(bundle.tasks),
            findings_count=len(findings),
            findings=findings,
        )

        resolved_model: str | None = None
        has_key = False
        enhance_failed = False

        if enhance:
            load_dotenv(override=False)

            resolved_model = _resolve_model(model, api_key)

            # Mask API key if logged / traced
            masked_key = "[MASKED]" if api_key else "None"

            has_key = check_model_api_key(resolved_model, api_key)

            if not has_key:
                console.print(
                    f"[yellow]Notice: No LLM API key found for model '{resolved_model}'. Defaulting to local Knowledge Store fallback.[/yellow]"
                )

            agent = None
            try:
                agent = AICriticAgent(
                    model_name=resolved_model,
                    api_key=api_key,
                )
                logger.info(
                    "Enhancing report with AI Critic using model %s and API key %s",
                    resolved_model,
                    masked_key,
                )
                result = agent.enhance_report(report)
                report = result.report
                fallback_count = result.fallback_count
            except Exception as e:  # noqa: BLE001
                err_console.print(
                    f"[yellow]AI Critic enhancement failed: {e}. Using offline fallback.[/yellow]"
                )
                enhance_failed = True

        # Export diagnosis.json
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(report.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
            console.print(
                f"[green]✓[/green] Saved diagnosis report to [bold]{escape(str(output))}[/bold]"
            )
        except OSError as e:
            err_console.print(
                f"[red]Error:[/red] Could not write diagnosis report to "
                f"'{escape(str(output))}': {escape(str(e))}"
            )
            raise typer.Exit(code=1) from e

        # 4. Display results
        if not findings:
            console.print(
                "\n[bold green]✓[/bold green] No issues found — "
                "workflow is clean and cloud-ready!\n"
            )
            raise typer.Exit(code=0)

        table = Table(
            title=f"Diagnostic Findings for '{bundle.metadata.name}'",
            show_lines=True,
        )
        table.add_column("Severity", style="bold", width=10)
        table.add_column("Rule", width=20)
        table.add_column("Process", width=18)
        table.add_column("Message")

        for finding in findings:
            try:
                sev_enum = Severity(finding.severity.lower())
                color = _SEVERITY_COLORS.get(sev_enum, "white")
            except ValueError:
                color = "white"
            table.add_row(
                f"[{color}]{finding.severity.upper()}[/{color}]",
                finding.rule_id,
                finding.location or "—",
                finding.message,
            )

        console.print()
        console.print(table)

        # Summary line
        n_err = sum(1 for f in findings if f.severity.lower() == "error")
        n_warn = sum(1 for f in findings if f.severity.lower() == "warning")
        n_info = sum(1 for f in findings if f.severity.lower() == "info")
        console.print(
            f"\n[bold]Summary:[/bold] {n_err} error(s), "
            f"{n_warn} warning(s), {n_info} info(s)"
        )

        if enhance:
            if enhance_failed:
                err_console.print(
                    f"[yellow]⚠️  AI Critic Enhancement Failed: Using offline Knowledge Store. "
                    f"All {len(findings)} findings using offline Knowledge Store.[/yellow]"
                )
            elif not has_key:
                console.print(
                    f"[green]✓[/green] Offline remediation guidance added to {len(findings)}/{len(findings)} findings (Knowledge Store fallback)"
                )
            elif fallback_count == len(findings) and len(findings) > 0:
                console.print(
                    f"[yellow]⚠️  AI Critic Enhancement Failed: All {len(findings)} findings fell back to the offline Knowledge Store.[/yellow]"
                )
            elif fallback_count > 0:
                console.print(
                    f"[yellow]⚠️  AI Critic Partial Failure: {fallback_count}/{len(findings)} findings fell back to the offline Knowledge Store.[/yellow]"
                )
            else:
                console.print(
                    f"[green]✓[/green] AI remediation guidance added to {len(findings)}/{len(findings)} findings (model: {resolved_model})"
                )
        console.print()

        exit_code = 1 if n_err > 0 else 0
        raise typer.Exit(code=exit_code)
    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()


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
