"""Main entrypoint for the Workflow Clinic package.

This module imports and executes the CLI application.
"""

from workflow_clinic.cli import app


def run() -> None:
    """Execute the CLI application."""
    app()


if __name__ == "__main__":
    run()
