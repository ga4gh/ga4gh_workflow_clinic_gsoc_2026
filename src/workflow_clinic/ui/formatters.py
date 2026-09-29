"""Rich table and formatting renderers for Workflow Clinic CLI results."""

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table

from workflow_clinic.rules import Severity
from workflow_clinic.services import (
    ExamineResult,
    FixResult,
    PublishMode,
    PublishResult,
)
from workflow_clinic.ui.console import (
    SEVERITY_COLORS,
)
from workflow_clinic.ui.console import (
    console as default_console,
)
from workflow_clinic.ui.console import (
    err_console as default_err_console,
)
from workflow_clinic.utils.llm import PROVIDER_MODEL_MAP


def display_enhance_status(
    result: ExamineResult,
    count: int,
    console: Console | None = None,
    err_console: Console | None = None,
) -> None:
    """Display AI Critic enhance summary status in terminal."""
    out = console or default_console
    err = err_console or default_err_console

    if result.enhance_failed:
        if result.enhance_error:
            err.print(
                f"[yellow]AI Critic enhancement failed: {result.enhance_error}. Using offline fallback.[/yellow]"
            )
        err.print(
            f"[yellow]⚠️  AI Critic Enhancement Failed: Using offline Knowledge Store. "
            f"All {count} findings using offline Knowledge Store.[/yellow]"
        )
    elif not result.has_key:
        out.print(
            f"[green]✓[/green] Offline remediation guidance added to {count}/{count} findings (Knowledge Store fallback)"
        )
    elif result.fallback_count == count and count > 0:
        out.print(
            f"[yellow]⚠️  AI Critic Enhancement Failed: All {count} findings fell back to the offline Knowledge Store.[/yellow]"
        )
    elif result.fallback_count > 0:
        out.print(
            f"[yellow]⚠️  AI Critic Partial Failure: {result.fallback_count}/{count} findings fell back to the offline Knowledge Store.[/yellow]"
        )
    else:
        out.print(
            f"[green]✓[/green] AI remediation guidance added to {count}/{count} findings (model: {result.resolved_model})"
        )


def display_examine_results(
    result: ExamineResult,
    *,
    enhance: bool,
    console: Console | None = None,
    err_console: Console | None = None,
) -> None:
    """Render diagnostic table, counts, and exit with code."""
    out = console or default_console
    findings = result.report.findings
    if not findings:
        out.print(
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
            color = SEVERITY_COLORS.get(sev_enum, "white")
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

    out.print()
    out.print(table)

    n_err = sum(1 for f in findings if f.severity.lower() == "error")
    n_warn = sum(1 for f in findings if f.severity.lower() == "warning")
    n_info = sum(1 for f in findings if f.severity.lower() == "info")
    out.print(
        f"\n[bold]Summary:[/bold] {n_err} error(s), "
        f"{n_warn} warning(s), {n_info} info(s)"
    )

    if enhance:
        display_enhance_status(
            result, len(findings), console=out, err_console=err_console
        )

    out.print()
    exit_code = 1 if n_err > 0 else 0
    raise typer.Exit(code=exit_code)


def display_models_table(
    models: list[tuple[str, str]] | None = None,
    console: Console | None = None,
) -> None:
    """Display supported AI models table."""
    out = console or default_console
    model_list = models if models is not None else PROVIDER_MODEL_MAP
    table = Table(title="Supported AI Models")
    table.add_column("Provider Key")
    table.add_column("Default Model")
    for env_var, model in model_list:
        table.add_row(env_var, model)
    out.print(table)


def display_fix_results(
    result: FixResult,
    console: Console | None = None,
) -> None:
    """Render proposed fix diffs or completion summary and exit."""
    out = console or default_console
    session = result.session
    if not session.proposals:
        out.print(
            "\n[bold yellow]![/bold yellow] No registered fixers available for the selected findings yet.\n"
        )
        raise typer.Exit(code=0)

    if result.dry_run:
        out.print(
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

        out.print(diff_table)
        out.print(
            f"\n[bold green]✓[/bold green] Dry-run complete for session "
            f"[bold cyan]{session.session_id[:8]}[/bold cyan] ({len(session.proposals)} proposal(s) ready).\n"
        )
        raise typer.Exit(code=0)

    out.print(
        f"\n[bold green]✓[/bold green] Workflow Doctor completed session "
        f"[bold cyan]{session.session_id[:8]}[/bold cyan]: "
        f"{session.applied_count}/{len(session.proposals)} fix(es) applied successfully.\n"
    )


def display_publish_results(
    result: PublishResult,
    *,
    preview: bool = False,
    repo_name: str | None = None,
    console: Console | None = None,
    err_console: Console | None = None,
) -> None:
    """Render preview, dry run, published issues table, or local export confirmation."""
    out = console or default_console
    err = err_console or default_err_console

    if result.mode == PublishMode.DRY_RUN:
        out.print("\n[cyan]--- Issue Markdown Payload (Dry Run) ---[/cyan]\n")
        out.print(result.combined_markdown)
        out.print()
        raise typer.Exit(code=0)

    if preview:
        out.print("\n[cyan]--- Issue Markdown Preview ---[/cyan]\n")
        out.print(Markdown(result.combined_markdown))
        out.print()

    if result.mode == PublishMode.GITHUB:
        if result.published_issues:
            target_repo = repo_name or "GitHub"
            out.print(
                f"\n[bold green]✓[/bold green] Successfully published {len(result.published_issues)} issue(s) to GitHub repository '[bold]{target_repo}[/bold]':\n"
            )
            pub_table = Table(show_lines=True)
            pub_table.add_column("Issue #", style="bold cyan", width=10)
            pub_table.add_column("Title", width=35)
            pub_table.add_column("URL", overflow="fold")

            for res in result.published_issues:
                pub_table.add_row(
                    f"#{res.number}",
                    res.title,
                    f"[link={res.url}]{res.url}[/link]",
                )
            out.print(pub_table)
            out.print("\n[bold]Direct Links:[/bold]")
            for res in result.published_issues:
                out.print(
                    f" • [bold cyan]#{res.number}[/bold cyan]: {res.url}",
                    soft_wrap=True,
                )
            out.print()
        else:
            err.print("[red]Error:[/red] Failed to publish any issues to GitHub.")
            raise typer.Exit(code=1)
    elif result.local_output_path:
        out.print(
            f"\n[bold green]✓[/bold green] Exported {len(result.selected_issues)} issue group(s) to [bold]{escape(str(result.local_output_path))}[/bold]\n"
        )
