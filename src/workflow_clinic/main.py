"""Main entrypoint for the Workflow Clinic CLI.

This module defines the Typer application and CLI commands for interacting with
the Workflow Clinic system.
"""

import typer

app = typer.Typer(
    name="workflow-clinic",
    help="AI-Powered Cloudification of Bioinformatics Workflows",
)


@app.callback()
def main() -> None:
    """Run the main command-line interface for Workflow Clinic.

    This function acts as the entrypoint callback for the Typer CLI.
    """


if __name__ == "__main__":
    app()
