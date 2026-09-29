"""Interactive selection prompts and terminal input parsers for Workflow Clinic."""

import sys
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table

from workflow_clinic.models.diagnosis import Finding
from workflow_clinic.reporting import GeneratedIssue
from workflow_clinic.ui.console import (
    console as default_console,
)
from workflow_clinic.ui.console import (
    err_console as default_err_console,
)

if TYPE_CHECKING:
    from workflow_clinic.services.fix_coordinator import FixCoordinator


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


def prompt_issue_selection(
    issues: list[GeneratedIssue],
    workflow_name: str = "",
    *,
    all_issues: bool = False,
    console: Console | None = None,
    err_console: Console | None = None,
) -> list[GeneratedIssue]:
    """Render interactive selection table and prompt user for issue choices."""
    out = console or default_console
    err = err_console or default_err_console

    title = (
        f"Diagnostic Issue Groups for '{workflow_name}'"
        if workflow_name
        else "Diagnostic Issue Groups"
    )
    table = Table(title=title, show_lines=True)
    table.add_column("Option", style="bold cyan", width=8)
    table.add_column("Severity", style="bold", width=10)
    table.add_column("Category", width=20)
    table.add_column("Locations")

    for idx, iss in enumerate(issues, 1):
        sev_color = "red" if iss.severity in ("CRITICAL", "HIGH", "ERROR") else "yellow"
        table.add_row(
            f"[{idx}]",
            f"[{sev_color}]{iss.severity}[/{sev_color}]",
            iss.category.replace("_", " ").title(),
            f"{len(iss.fingerprints)} location(s)",
        )

    out.print()
    out.print(table)

    is_tty = sys.stdin.isatty()
    if all_issues or not is_tty:
        if not is_tty and not all_issues:
            out.print(
                "[yellow]Non-interactive terminal detected — auto-selecting all findings.[/yellow]"
            )
        return issues

    prompt_msg = f"Select issues to publish (e.g. 1,{len(issues)} or all) [all]"
    raw_input_str = typer.prompt(prompt_msg, default="all")
    selected_indices = parse_selection(raw_input_str, len(issues))
    if not selected_indices:
        err.print("[yellow]No valid issues selected. Exiting.[/yellow]")
        raise typer.Exit(code=0)

    return [issues[i] for i in selected_indices]


def prompt_category_selection(
    coordinator: "FixCoordinator",
    actionable_findings: list[Finding],
    workflow_name: str,
    console: Console | None = None,
    err_console: Console | None = None,
) -> list[Finding]:
    """Display interactive category table and prompt user for selection."""
    out = console or default_console
    err = err_console or default_err_console

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

    out.print()
    out.print(table)

    prompt_msg = (
        f"Select category domains to fix (e.g. 1,{len(categories)} or all) [all]"
    )
    raw_input_str = typer.prompt(prompt_msg, default="all")
    selected_indices = parse_selection(raw_input_str, len(categories))
    if not selected_indices:
        err.print("[yellow]No valid category domains selected. Exiting.[/yellow]")
        raise typer.Exit(code=0)

    selected_categories = {categories[i] for i in selected_indices}
    selected_set = {id(f) for cat in selected_categories for f in grouped[cat]}
    return [f for f in actionable_findings if id(f) in selected_set]
