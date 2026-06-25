"""Unit tests for the application logging configuration."""

import logging
import sys

from typer.testing import CliRunner

from workflow_clinic.main import app, main

runner = CliRunner()


def test_logging_setup() -> None:
    """Verify that executing the main callback configures the root logger handlers."""
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    root_logger.handlers.clear()

    try:
        # Run main callback directly to verify logging configuration logic
        main()

        # Verify that handlers were configured by basicConfig
        assert len(root_logger.handlers) >= 1
        assert root_logger.getEffectiveLevel() == logging.WARNING

        # Verify the handler configuration
        handler = root_logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream == sys.stderr

    finally:
        # Restore handlers and level
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)


def test_cli_help_on_no_args() -> None:
    """Verify that the CLI shows help when invoked with no arguments."""
    result = runner.invoke(app, [])
    assert result.exit_code == 2  # noqa: PLR2004
    assert "Usage: workflow-clinic" in result.output
    assert "--help" in result.output
