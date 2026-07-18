"""Unit tests for the command-line interface (CLI) options."""

import importlib.util
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workflow_clinic import __version__
from workflow_clinic.cli import app

runner = CliRunner()
HAS_NEXTFLOW = importlib.util.find_spec("groovy_parser") is not None


def test_version_option() -> None:
    """Verify that --version and -V output the correct version string and exit."""
    # Test --version
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert f"workflow-clinic version {__version__}" in result.output

    # Test -V
    result_short = runner.invoke(app, ["-V"])
    assert result_short.exit_code == 0
    assert f"workflow-clinic version {__version__}" in result_short.output


def test_verbose_option() -> None:
    """Verify that --verbose and -v configure the logging level to INFO."""
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    root_logger.handlers.clear()

    try:
        # Test --verbose
        result = runner.invoke(app, ["--verbose"])
        assert result.exit_code == 0
        assert root_logger.getEffectiveLevel() == logging.INFO

        # Reset handlers and level for short option test
        root_logger.handlers.clear()

        # Test -v
        result_short = runner.invoke(app, ["-v"])
        assert result_short.exit_code == 0
        assert root_logger.getEffectiveLevel() == logging.INFO

    finally:
        # Restore handlers and level
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
def test_examine_clean_workflow() -> None:
    """Verify examine CLI command on a clean workflow succeeds with exit code 0."""
    dummy_path = str(Path(__file__).parent / "fixtures" / "dummy.nf")
    result = runner.invoke(app, ["examine", dummy_path])
    assert result.exit_code == 0
    assert "No issues found" in result.output
    assert "clean and cloud-ready" in result.output


@pytest.mark.skipif(not HAS_NEXTFLOW, reason="Nextflow support not installed")
def test_examine_poor_practices() -> None:
    """Verify examine CLI command on a flawed workflow lists issues and exits with code 1."""
    poor_path = str(Path(__file__).parent / "fixtures" / "poor_practices.nf")
    result = runner.invoke(app, ["examine", poor_path])
    # Exits with 1 because there is at least one ERROR
    assert result.exit_code == 1
    assert "Diagnostic Findings for 'poor_practices'" in result.output
    assert "ERROR" in result.output
    assert "WARNING" in result.output
    assert "INFO" in result.output
    assert "NO_CONTAINER" in result.output
    assert "UNPINNED_TAG" in result.output
    assert "TAGLESS_IMAGE" in result.output
    assert "NO_RESOURCES" in result.output
    assert "Summary: 1 error(s), 4 warning(s), 1 info(s)" in result.output


def test_examine_unsupported_workflow() -> None:
    """Verify examine CLI command fails with code 1 for unsupported file formats."""
    unsupported_path = str(Path(__file__).parent / "test_cli.py")
    result = runner.invoke(app, ["examine", unsupported_path])
    assert result.exit_code == 1
    assert "Error:" in result.stderr
    assert "No registered parser can handle workflow" in result.stderr


def test_examine_nonexistent_file() -> None:
    """Verify examine CLI command fails with a Typer validation error on invalid path."""
    result = runner.invoke(app, ["examine", "non_existent_file.nf"])
    assert result.exit_code != 0
    assert "does not exist" in result.output
