"""Unit tests for the application logging configuration."""

import logging

from typer.testing import CliRunner

from workflow_clinic.main import app

runner = CliRunner()


def test_logging_setup() -> None:
    """Verify that executing the CLI entrypoint configures the root logger handlers."""
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    root_logger.handlers.clear()

    try:
        # Run main callback
        result = runner.invoke(app, [])
        assert result.exit_code == 0

        # Verify that handlers were configured by basicConfig
        assert len(root_logger.handlers) >= 1
        assert root_logger.getEffectiveLevel() == logging.WARNING
    finally:
        # Restore handlers
        root_logger.handlers = original_handlers
