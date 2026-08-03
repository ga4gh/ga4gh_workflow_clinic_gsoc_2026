"""Command-line interface (CLI) definition for Workflow Clinic.

This module houses the Typer application, global option callbacks,
and CLI command routing.
"""

import json
import logging
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from workflow_clinic import __version__
from workflow_clinic.exceptions import (
    InvalidWorkflowError,
    ParserError,
    UnsupportedWorkflowError,
)
from workflow_clinic.parsers import ParserRegistry
from workflow_clinic.reporting import compute_fingerprint
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
        findings = runner.run(bundle)

        formatted_findings = []
        for f in findings:
            f_dict = f.model_dump()
            fp = compute_fingerprint(
                file_path=f.location or str(scan_path),
                rule_id=f.rule_id,
                task_id=f.task_id,
                target_token=f.message,
            )
            f_dict["fingerprint"] = fp.model_dump()
            f_dict["id"] = fp.hash
            formatted_findings.append(f_dict)

        # Export diagnosis.json (always generated per proposal spec)
        diagnosis_data = {
            "workflow_name": bundle.metadata.name,
            "tasks_count": len(bundle.tasks),
            "findings_count": len(findings),
            "findings": formatted_findings,
        }
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(diagnosis_data, indent=2) + "\n", encoding="utf-8"
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
            color = _SEVERITY_COLORS.get(finding.severity, "white")
            table.add_row(
                f"[{color}]{finding.severity.value.upper()}[/{color}]",
                finding.rule_id,
                finding.location or "—",
                finding.message,
            )

        console.print()
        console.print(table)

        # Summary line
        n_err = sum(1 for f in findings if f.severity == Severity.ERROR)
        n_warn = sum(1 for f in findings if f.severity == Severity.WARNING)
        n_info = sum(1 for f in findings if f.severity == Severity.INFO)
        console.print(
            f"\n[bold]Summary:[/bold] {n_err} error(s), "
            f"{n_warn} warning(s), {n_info} info(s)\n"
        )

        exit_code = 1 if n_err > 0 else 0
        raise typer.Exit(code=exit_code)
    finally:
        if temp_dir_obj is not None:
            temp_dir_obj.cleanup()
