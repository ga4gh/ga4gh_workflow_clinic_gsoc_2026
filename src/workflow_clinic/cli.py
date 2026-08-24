"""Command-line interface (CLI) definition for Workflow Clinic.

This module houses the Typer application, global option callbacks,
and CLI command routing.
"""

import contextlib
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from dotenv import load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
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
)
from workflow_clinic.models.diagnosis import (
    Finding as DiagnosisFinding,
)
from workflow_clinic.models.fix import AppliedProposal, ApplyOutcome, FixSession
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


def _save_fix_session(fixes_path: Path, session: FixSession) -> None:
    """Save applied fix proposals to fixes.json, merging with any existing fixes."""
    merged_proposals: dict[str, AppliedProposal] = {}
    if fixes_path.exists():
        with contextlib.suppress(Exception):
            existing = FixSession.model_validate_json(
                fixes_path.read_text(encoding="utf-8")
            )
            for ap in existing.applied_proposals:
                if ap.applied:
                    key = f"{ap.proposal.finding_id}:{ap.proposal.rule_id}:{ap.proposal.target_file}"
                    merged_proposals[key] = ap
    for ap in session.applied_proposals:
        if ap.applied:
            key = f"{ap.proposal.finding_id}:{ap.proposal.rule_id}:{ap.proposal.target_file}"
            merged_proposals[key] = ap

    combined_session = FixSession(
        session_id=session.session_id,
        source=session.source,
        proposals=[ap.proposal for ap in merged_proposals.values()],
        applied_proposals=list(merged_proposals.values()),
    )
    fixes_path.write_text(combined_session.model_dump_json(indent=2), encoding="utf-8")


def _load_fix_session(fixes_path: Path) -> FixSession | None:
    """Load saved fix session from fixes.json if it exists."""
    if not fixes_path.exists():
        return None
    try:
        return FixSession.model_validate_json(fixes_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


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
    if explicit_model:
        return explicit_model
    if clinic_model := os.getenv("CLINIC_MODEL"):
        return clinic_model
    for env_var, model_name in PROVIDER_MODEL_MAP:
        if os.getenv(env_var):
            return model_name
    if api_key:
        logger.warning(
            "--api-key provided without --model. Defaulting to gemini/gemini-3.6-flash."
        )
    return "gemini/gemini-3.6-flash"


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

        resolved_model: str | None = None
        has_key = False
        enhance_failed = False

        if enhance:
            load_dotenv(override=False)
            resolved_model = _resolve_model(model, api_key)
            has_key = check_model_api_key(resolved_model, api_key)

        if enhance:
            if not has_key:
                err_console.print(
                    "[yellow]Warning: --enhance requires an LLM API key for auditing and full remediation. "
                    "Falling back to local knowledge store for remediation only.[/yellow]"
                )
            else:
                console.print("[cyan]Performing AI Audit for new issues...[/cyan]")
                agent = AICriticAgent(
                    model_name=resolved_model or "gemini/gemini-3.6-flash",
                    api_key=api_key,
                )
                audit_findings = agent.audit_workflow(
                    bundle, static_findings=list(raw_findings)
                )  # type: ignore[arg-type]
                raw_findings.extend(audit_findings)  # type: ignore[arg-type]

        findings = []
        for f in raw_findings:
            file_p = getattr(f, "file_path", None) or target
            task_id_val = getattr(f, "task_id", None) or None
            fp = compute_fingerprint(
                file_path=file_p,
                rule_id=f.rule_id,
                task_id=task_id_val,
                target_token=f.message,
            )
            f_dict = f.model_dump()
            f_dict["file_path"] = f.file_path or target
            f_dict["fingerprint"] = fp.model_dump()
            f_dict["id"] = fp.hash
            findings.append(DiagnosisFinding.model_validate(f_dict))

        report = DiagnosisReport(
            workflow_name=bundle.metadata.name,
            tasks_count=len(bundle.tasks),
            findings_count=len(findings),
            findings=findings,
        )

        if enhance:
            # Mask API key if logged / traced
            masked_key = "[MASKED]" if api_key else "None"

            has_key = check_model_api_key(resolved_model, api_key)
            if not has_key:
                console.print(
                    f"[yellow]Notice: No LLM API key found for model '{resolved_model}'. Defaulting to local Knowledge Store fallback.[/yellow]"
                )

            critic_agent = None
            try:
                critic_agent = AICriticAgent(
                    model_name=resolved_model or "gemini/gemini-3.6-flash",
                    api_key=api_key,
                )
                logger.info(
                    "Enhancing report with AI Critic using model %s and API key %s",
                    resolved_model,
                    masked_key,
                )
                result = critic_agent.enhance_report(report)
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
            fixes_file = output.parent / "fixes.json"
            if fixes_file.exists():
                fixes_file.unlink()
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
                    f"{loc_name}:{finding.line_number}"
                    if finding.line_number
                    else loc_name
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


def parse_selection(raw: str, max_index: int) -> list[int]:  # noqa: C901
    """Parse interactive selection string into 0-indexed integer list.

    Supports comma lists ("1, 3"), ranges ("1-3"), "all", and default empty.
    Ignores out-of-bounds indices.
    """

    clean = raw.strip().lower()
    if not clean:
        return []
    if clean in ("all", "a"):
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
        prompt_msg = f"Select issues to publish (e.g. 1,{len(generated_issues)} or all)"
        raw_input_str = typer.prompt(prompt_msg, default="")
        selected_indices = parse_selection(raw_input_str, len(generated_issues))
        if not selected_indices:
            err_console.print("[yellow]No issues selected. Exiting.[/yellow]")
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


@app.command(name="fix")
def fix(  # noqa: C901, PLR0912, PLR0915
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
    create_pr: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--create-pr",
            "-p",
            help="Publish applied fixes as a Pull Request directly to the target GitHub repository.",
        ),
    ] = False,
    issue: Annotated[
        int | None,
        typer.Option(
            "--issue",
            "-i",
            help="Target a specific GitHub issue number to resolve with automated fixes.",
        ),
    ] = None,
    base_branch: Annotated[
        str | None,
        typer.Option(
            "--base-branch",
            help="Base branch for the published Pull Request (defaults to repository default branch).",
        ),
    ] = None,
    branch_name: Annotated[
        str | None,
        typer.Option(
            "--branch-name",
            help="Custom head branch name for the published Pull Request.",
        ),
    ] = None,
    local: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--local",
            help="Force local Markdown file export (default: pr.md) without publishing to GitHub API.",
        ),
    ] = False,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output Markdown file path for local export when --create-pr or --local is used (default: pr.md).",
        ),
    ] = Path("pr.md"),
    standalone_create_pr: bool = False,  # noqa: FBT001, FBT002
) -> None:
    """Automated Workflow Doctor engine for repairing diagnostic findings in Nextflow & Snakemake pipelines."""
    target_path = Path(target).resolve()
    if target_path.is_file() and target_path.suffix == ".json":
        diag_path = target_path
        root_dir = target_path.parent
    elif target_path.is_file():
        diag_path = target_path.parent / "diagnosis.json"
        root_dir = target_path.parent
    else:
        diag_path = target_path / "diagnosis.json"
        root_dir = target_path

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
    findings = list(report.findings)

    use_github = not local and bool(token_val and repo_val)

    if use_github and create_pr:
        try:
            publisher = GitHubPublisher(token=str(token_val), repository=str(repo_val))
            with console.status(
                "[bold cyan]Fetching active issues and Pull Requests from GitHub...[/bold cyan]"
            ):
                active_fps = publisher.fetch_active_fingerprints()
            if active_fps:
                original_count = len(findings)
                findings = filter_new_findings(findings, active_fps)
                diff_count = original_count - len(findings)
                if diff_count > 0:
                    console.print(
                        f"[cyan]Deduplication:[/cyan] filtered out {diff_count} finding(s) already tracked in open PRs."
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to fetch active fingerprints for PR filtering: %s", e
            )

    # Only validate token/repo when GitHub features or CLI options are explicitly requested
    if (
        (create_pr or issue is not None or repo is not None or token is not None)
        and not local
        and not dry_run
    ):
        if repo_val and not token_val:
            err_console.print(
                "[red]Error:[/red] GitHub repository specified but GitHub token is missing. Provide via --token or GITHUB_TOKEN."
            )
            raise typer.Exit(code=1)
        if token_val and not repo_val:
            err_console.print(
                "[red]Error:[/red] GitHub token specified but repository is missing. Provide via --repo or GITHUB_REPOSITORY."
            )
            raise typer.Exit(code=1)

    if issue is not None:
        if not use_github:
            err_console.print(
                "[red]Error:[/red] Targeting a GitHub issue requires --repo and --token (or GITHUB_REPOSITORY and GITHUB_TOKEN)."
            )
            raise typer.Exit(code=1)
        try:
            publisher = GitHubPublisher(token=str(token_val), repository=str(repo_val))
            _, issue_fps = publisher.fetch_issue_findings(issue)
            matched_findings = [
                f
                for f in report.findings
                if f.fingerprint and f.fingerprint.hash in issue_fps
            ]
            if matched_findings:
                findings = matched_findings
            else:
                console.print(
                    f"[yellow]No matching findings in '{diag_path.name}' for Issue #{issue}.[/yellow]"
                )
                raise typer.Exit(code=0)
            console.print(
                f"[bold cyan]Targeting Issue #{issue}:[/bold cyan] found {len(findings)} matching finding(s)."
            )
        except GitHubPublisherError as e:
            err_console.print(f"[red]GitHub Issue Error:[/red] {escape(str(e))}")
            raise typer.Exit(code=1) from e

    if target_path.is_file() and target_path.suffix in (".nf", ".smk"):
        target_name = target_path.name
        matched = [
            f
            for f in findings
            if f.file_path
            and (
                Path(f.file_path).name == target_name
                or (root_dir / f.file_path).resolve() == target_path
            )
        ]
        if matched:
            findings = matched

    # AI Critic audit (enabled in --enhance and --ai-only modes)
    if enhance or ai_only:
        model_name = _resolve_model(None, None)
        if not check_model_api_key(model_name):
            if ai_only:
                err_console.print(
                    f"[red]Error:[/red] '--ai-only' requires an active LLM API key for '{model_name}'. "
                    "Please set GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY, or specify model environment variables."
                )
                raise typer.Exit(code=1)
            console.print(
                f"[yellow]⚠️  No API key found for '{model_name}' - running offline fixes only (W001-W004).[/yellow]"
            )
        else:
            try:
                workflow_file: Path | None = None
                if target_path.is_file() and target_path.suffix in (".nf", ".smk"):
                    workflow_file = target_path
                else:
                    for f in report.findings:
                        if f.file_path:
                            candidate = (root_dir / f.file_path).resolve()
                            if candidate.is_file():
                                workflow_file = candidate
                                break

                if workflow_file:
                    parser_name = ParserRegistry.detect_parser(workflow_file)
                    parser = ParserRegistry.get_parser(parser_name)
                    bundle = parser.parse(workflow_file)
                    critic = AICriticAgent(model_name=model_name)
                    ai_findings = critic.audit_workflow(bundle)
                    if ai_findings:
                        findings.extend(ai_findings)
                        console.print(
                            f"[bold cyan]AI Critic identified {len(ai_findings)} enhancement finding(s).[/bold cyan]"
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning("AI Critic audit failed: %s", e)

    if create_pr:
        findings = [
            f
            for f in findings
            if getattr(f, "severity", "").upper() not in ("INFO", "LOW")
        ]

    if not findings:
        if create_pr:
            console.print(
                "\n[bold yellow]![/bold yellow] There are no fixes to commit in a PR. Please run [green]workflow-clinic fix[/green] first.\n"
            )
        else:
            console.print(
                "\n[bold green]✓[/bold green] No actionable findings to fix!\n"
            )
        raise typer.Exit(code=0)

    if rule:
        rule_set = {r.upper() for r in rule}
        findings = [f for f in findings if f.rule_id.upper() in rule_set]
        if not findings:
            console.print(
                f"\n[bold yellow]![/bold yellow] No findings matching rule filter(s): {', '.join(rule_set)}\n"
            )
            raise typer.Exit(code=0)

    # Group findings by Category domain for interactive selection
    grouped_findings: dict[str, list] = {}
    for f in findings:
        grouped_findings.setdefault(f.category, []).append(f)

    # Known category domains in a stable order
    all_known_categories = ["containerization", "resources", "portability", "security"]
    stable_categories = list(all_known_categories)
    for cat in grouped_findings:
        if cat not in stable_categories:
            stable_categories.append(cat)
    # Filter to only categories actually present in grouped_findings so displayed option numbers are contiguous
    stable_categories = [cat for cat in stable_categories if cat in grouped_findings]

    # Determine selected findings
    is_tty = sys.stdin.isatty() or os.environ.get("FORCE_INTERACTIVE") == "1"
    if all_issues or not is_tty:
        if not is_tty and not all_issues:
            console.print(
                "[yellow]Non-interactive terminal detected — auto-selecting all findings for fix.[/yellow]"
            )
        selected_findings = findings
    else:
        table = Table(
            title=f"Diagnostic Categories to Repair for '{report.workflow_name}'",
            show_lines=True,
        )
        table.add_column("Option", style="bold cyan", width=8)
        table.add_column("Category Domain", width=22)
        table.add_column("Rules", style="bold green", width=16)
        table.add_column("Findings Count", style="bold yellow", width=16)

        # Build map of stable index (1-based) -> category name
        option_to_category: dict[int, str] = {}
        for cat in stable_categories:
            if cat in grouped_findings:
                opt_idx = stable_categories.index(cat) + 1
                option_to_category[opt_idx] = cat

        for opt_idx, cat in sorted(option_to_category.items()):
            cat_findings = grouped_findings[cat]
            cat_rules = sorted({f.rule_id for f in cat_findings if f.rule_id})
            rules_str = ", ".join(cat_rules) if cat_rules else "-"
            table.add_row(
                f"[{opt_idx}]",
                cat.replace("_", " ").title(),
                rules_str,
                f"{len(cat_findings)} issue(s)",
            )

        console.print()
        console.print(table)

        prompt_msg = (
            f"Select category domains to fix (e.g. 1,{len(stable_categories)} or all)"
        )
        raw_input_str = typer.prompt(prompt_msg, default="")
        selected_indices = parse_selection(raw_input_str, len(stable_categories))
        if not selected_indices:
            err_console.print("[yellow]No category domains selected. Exiting.[/yellow]")
            raise typer.Exit(code=0)

        # Map stable indices back to actual categories
        selected_categories = {
            stable_categories[i]
            for i in selected_indices
            if i < len(stable_categories) and stable_categories[i] in grouped_findings
        }
        if not selected_categories:
            err_console.print(
                "[yellow]No active category domains selected from input. Exiting.[/yellow]"
            )
            raise typer.Exit(code=0)

        selected_findings = [f for f in findings if f.category in selected_categories]

    if standalone_create_pr:
        fixes_path = diag_path.parent / "fixes.json"
        saved_session = _load_fix_session(fixes_path)

        unfixed_findings: list[DiagnosisFinding] = []
        matched_applied_proposals: list[AppliedProposal] = []

        if not saved_session or not saved_session.applied_proposals:
            unfixed_findings = list(selected_findings)
        else:
            for f in selected_findings:
                f_hash = f.fingerprint.hash if f.fingerprint else f.id
                f_rule = f.rule_id
                f_file = f.file_path or ""
                f_filename = Path(f_file).name

                matched_ap = None
                for ap in saved_session.applied_proposals:
                    if not ap.applied:
                        continue
                    ap_file = ap.proposal.target_file
                    ap_filename = Path(ap_file).name
                    if (
                        ap.proposal.finding_id and ap.proposal.finding_id == f_hash
                    ) or (
                        ap.proposal.rule_id == f_rule
                        and (ap_file == f_file or ap_filename == f_filename)
                    ):
                        matched_ap = ap
                        break

                if matched_ap:
                    matched_applied_proposals.append(matched_ap)
                else:
                    unfixed_findings.append(f)

        if unfixed_findings:
            console.print(
                "\n[bold yellow]![/bold yellow] The issue is not yet fixed. Kindly run the fix command and choose this issue to fix:\n"
            )
            for f in unfixed_findings:
                loc = (
                    f"{f.file_path}:{f.line_number}"
                    if f.line_number
                    else str(f.file_path)
                )
                console.print(
                    f"  - [bold cyan][{f.rule_id}][/bold cyan] {f.message or f.title} (at {loc})"
                )
            console.print("")
            raise typer.Exit(code=0)

        # Build session using matched applied proposals
        unique_applied: list[AppliedProposal] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for ap in matched_applied_proposals:
            k = (ap.proposal.finding_id, ap.proposal.rule_id, ap.proposal.target_file)
            if k not in seen_keys:
                seen_keys.add(k)
                unique_applied.append(ap)

        session = FixSession(
            session_id=saved_session.session_id if saved_session else str(uuid4()),
            source=str(diag_path),
            findings_input=selected_findings,
            proposals=[ap.proposal for ap in unique_applied],
            applied_proposals=unique_applied,
        )
    else:
        runner = DoctorRunner()
        session = runner.run(
            selected_findings,
            root_dir=root_dir,
            dry_run=dry_run,
            ai_only=ai_only,
            offline_only=(not enhance and not ai_only),
        )

        if not session.proposals:
            console.print(
                "\n[bold yellow]![/bold yellow] The issue is not yet fixed. Kindly run the fix command and choose this issue to fix:\n"
            )
            for f in selected_findings:
                loc = (
                    f"{f.file_path}:{f.line_number}"
                    if f.line_number
                    else str(f.file_path)
                )
                console.print(
                    f"  - [bold cyan][{f.rule_id}][/bold cyan] {f.message or f.title} (at {loc})"
                )
            console.print("")
            raise typer.Exit(code=0)

        if not dry_run and session.applied_count > 0:
            fixes_path = diag_path.parent / "fixes.json"
            _save_fix_session(fixes_path, session)

    # Handle dry-run or PR preview
    if dry_run:
        summary_table = Table(
            title=f"\n--- Workflow Doctor Dry Run ({len(session.proposals)} proposed fix(es)) ---",
            show_lines=True,
            header_style="bold cyan",
        )
        summary_table.add_column("Rule", style="bold magenta")
        summary_table.add_column("Target File", style="green")
        summary_table.add_column("Layer Strategy", style="cyan")
        summary_table.add_column("Rationale")

        for prop in session.proposals:
            prop_path = Path(prop.target_file)
            rel_file = (
                os.path.relpath(prop_path, root_dir)
                if prop_path.is_absolute()
                else str(prop_path)
            )
            summary_table.add_row(
                prop.rule_id,
                rel_file,
                prop.strategy_layer.name,
                prop.explanation,
            )
        console.print(summary_table)
        console.print(
            f"\n[bold green]✓[/bold green] Dry-run complete for session [bold cyan]{session.session_id[:8]}[/bold cyan] ({len(session.proposals)} proposal(s) ready).\n"
        )

        if create_pr:
            preview_pub = GitHubPublisher(
                token=token_val or "dummy_token",
                repository=repo_val or "owner/repo",
            )
            preview_session = session.model_copy(deep=True)
            if not preview_session.applied_proposals and preview_session.proposals:
                preview_session.applied_proposals = [
                    AppliedProposal(
                        proposal=p,
                        applied=True,
                        outcome=ApplyOutcome(
                            success=True,
                            modified_file=Path(p.target_file),
                            verification_passed=True,
                        ),
                    )
                    for p in preview_session.proposals
                ]
            preview_body = preview_pub.build_pr_body(
                preview_session, linked_issues=[issue] if issue else None
            )
            pr_preview_title = f"[Workflow Clinic] Automated Remediation — {preview_session.applied_count} issue(s) fixed"
            console.print(
                "\n[bold cyan]--- Pull Request Preview (Dry Run) ---[/bold cyan]\n"
            )
            console.print(
                Panel(
                    Markdown(preview_body),
                    title=f"[bold]{escape(pr_preview_title)}[/bold]",
                    border_style="cyan",
                )
            )
        raise typer.Exit(code=0)

    if not standalone_create_pr:
        console.print(
            f"\n[bold green]✓[/bold green] Workflow Doctor completed session [bold cyan]{session.session_id[:8]}[/bold cyan]: {session.applied_count}/{len(session.proposals)} fix(es) applied successfully.\n"
        )

    if create_pr:
        if session.applied_count == 0:
            console.print(
                "[yellow]No fixes were applied — skipping PR creation.[/yellow]\n"
            )
        elif not use_github:
            publisher = GitHubPublisher(
                token="local_token",  # noqa: S106
                repository="local/repo",
            )
            pr_body = publisher.build_pr_body(
                session, linked_issues=[issue] if issue else None
            )
            pr_title = f"[Workflow Clinic] Automated Remediation — {session.applied_count} issue(s) fixed"
            full_pr_content = f"# {pr_title}\n\n{pr_body}"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(full_pr_content, encoding="utf-8")
            console.print(
                f"[bold green]✓[/bold green] Exported Pull Request summary to [bold cyan]{output}[/bold cyan]\n"
            )
        else:
            try:
                publisher = GitHubPublisher(
                    token=str(token_val or ""),
                    repository=str(repo_val or ""),
                )
                linked_issues: list[int] = [issue] if issue else []
                if not issue:
                    with contextlib.suppress(Exception):
                        open_issues = publisher.fetch_all_clinic_issues()
                        modified_names = {
                            mf.name if isinstance(mf, Path) else str(mf)
                            for mf in session.modified_files
                        }
                        repaired_fps = {
                            f.fingerprint.hash
                            for f in report.findings
                            if f.fingerprint
                            and (
                                f.file_path in modified_names
                                or Path(f.file_path).name in modified_names
                            )
                        }
                        for open_num, open_fps in open_issues:
                            if open_fps & repaired_fps:
                                linked_issues.append(open_num)

                with console.status(
                    "[bold cyan]Publishing Pull Request to GitHub...[/bold cyan]"
                ):
                    pr_info = publisher.publish_pull_request(
                        session=session,
                        root_dir=root_dir,
                        branch_name=branch_name,
                        base_branch=base_branch,
                        linked_issues=linked_issues,
                    )
                console.print(
                    f"[bold green]✓ Created Pull Request #[/bold green][bold cyan]{pr_info.number}[/bold cyan] on [bold]{escape(repo_val or '')}[/bold]: [link={pr_info.url}]{pr_info.url}[/link]\n"
                )
            except GitHubPublisherError as e:
                err_console.print(
                    f"[red]Failed to publish Pull Request:[/red] {escape(str(e))}"
                )
                raise typer.Exit(code=1) from e


@app.command(name="create-pr")
def create_pr_cmd(
    target: Annotated[
        str,
        typer.Argument(
            help="Path to local workflow directory, diagnosis.json file, or target repository.",
        ),
    ] = ".",
    all_fixes: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--all",
            "-y",
            help="Apply all proposed fixes non-interactively before creating Pull Request.",
        ),
    ] = False,
    dry_run: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--dry-run",
            help="Preview PR body and diffs without modifying files on disk or posting to GitHub.",
        ),
    ] = False,
    preview: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--preview",
            help="Render a Markdown preview of the PR in terminal before writing to disk/GitHub.",
        ),
    ] = False,
    local: Annotated[  # noqa: FBT002
        bool,
        typer.Option(
            "--local",
            help="Force local Markdown file export (default: pr.md) without publishing to GitHub API.",
        ),
    ] = False,
    output: Annotated[
        Path,
        typer.Option(
            "--output",
            "-o",
            help="Output Markdown file path for local export (default: pr.md).",
        ),
    ] = Path("pr.md"),
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
    branch_name: Annotated[
        str | None,
        typer.Option(
            "--branch-name",
            "-b",
            help="Custom head branch name for the published Pull Request.",
        ),
    ] = None,
    base_branch: Annotated[
        str | None,
        typer.Option(
            "--base-branch",
            help="Base branch for the published Pull Request (defaults to repository default branch).",
        ),
    ] = None,
    issue: Annotated[
        int | None,
        typer.Option(
            "--issue",
            "-i",
            help="Target a specific GitHub issue number to resolve with automated fixes.",
        ),
    ] = None,
    rules: Annotated[
        list[str] | None,
        typer.Option(
            "--rule",
            "-r",
            help="Filter findings/fixes to specific rule codes (e.g. --rule W001 --rule W002).",
        ),
    ] = None,
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
) -> None:
    """Generate verified fixes and create a GitHub Pull Request or export PR details locally to pr.md."""
    fix(
        target=target,
        rule=rules,
        all_issues=all_fixes,
        dry_run=dry_run,
        enhance=enhance,
        ai_only=ai_only,
        token=token,
        repo=repo,
        create_pr=True,
        standalone_create_pr=True,
        local=local,
        output=output,
        issue=issue,
        base_branch=base_branch,
        branch_name=branch_name,
    )
    if preview and not dry_run and output.exists():
        console.print("\n[bold cyan]--- Pull Request Content ---[/bold cyan]\n")
        console.print(
            Panel(
                Markdown(output.read_text(encoding="utf-8")),
                title="[bold]Pull Request Export Preview[/bold]",
                border_style="cyan",
            )
        )
