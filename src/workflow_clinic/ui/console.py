"""Shared Rich console instances and UI styling constants for Workflow Clinic."""

from rich.console import Console

from workflow_clinic.rules import Severity

console = Console()
err_console = Console(stderr=True)

SEVERITY_COLORS: dict[Severity, str] = {
    Severity.INFO: "blue",
    Severity.WARNING: "yellow",
    Severity.ERROR: "red",
}
