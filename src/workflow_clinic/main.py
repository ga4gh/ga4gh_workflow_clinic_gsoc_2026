"""Main entrypoint for the Workflow Clinic CLI.

This module defines the Typer application and CLI commands for interacting with
the Workflow Clinic system.
"""

import logging
import sys

import typer

app = typer.Typer(
    name="workflow-clinic",
    help="AI-Powered Cloudification of Bioinformatics Workflows",
    invoke_without_command=True,
)


def setup_logging() -> None:
    """Configure the standard logging handler across the application.

    By default, sets the log level to WARNING.
    """
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
        force=True,
    )


@app.callback()
def main() -> None:
    """Run the main command-line interface for Workflow Clinic.

    This function acts as the entrypoint callback for the Typer CLI.
    """
    setup_logging()


if __name__ == "__main__":
    app()
