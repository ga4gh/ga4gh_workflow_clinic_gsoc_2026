"""Command-line interface (CLI) definition for Workflow Clinic.

This module houses the Typer application, global option callbacks,
and CLI command routing.
"""

import logging
import sys
from typing import Annotated

import typer

from workflow_clinic import __version__

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
