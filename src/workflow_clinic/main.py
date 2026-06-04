"""
Main entrypoint for the Workflow Clinic CLI.
"""

import typer

app = typer.Typer(
    name="workflow-clinic",
    help="AI-Powered Cloudification of Bioinformatics Workflows",
)


@app.callback()
def main():
    """Workflow Clinic CLI tool."""
    pass


if __name__ == "__main__":
    app()
