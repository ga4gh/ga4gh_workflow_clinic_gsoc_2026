"""Unit tests for the command-line interface (CLI) options."""

import logging

from typer.testing import CliRunner

from workflow_clinic import __version__
from workflow_clinic.cli import app

runner = CliRunner()


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
