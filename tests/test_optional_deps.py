"""Unit tests for verification of behavior under missing optional dependencies."""

import builtins
import contextlib
import importlib
import sys

import pytest
from typer.testing import CliRunner

from workflow_clinic.cli import app
from workflow_clinic.exceptions import ParserError
from workflow_clinic.parsers import ParserRegistry


@pytest.fixture
def missing_dependencies_env(monkeypatch):
    """Fixture to mock unimportable groovy_parser and lark, and reset the registry."""
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name.startswith(("groovy_parser", "lark")):
            err_msg = f"Mocked missing module: {name}"
            raise ModuleNotFoundError(err_msg, name=name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    # Clean cached modules from sys.modules
    for module_name in ["workflow_clinic.parsers.nextflow", "groovy_parser", "lark"]:
        if module_name in sys.modules:
            del sys.modules[module_name]

    # Reset registry state
    ParserRegistry._discovered = False
    ParserRegistry._parsers.clear()
    ParserRegistry._manual_parsers.clear()
    ParserRegistry._entry_points.clear()

    # Pre-populate entry points to simulate nextflow being registered
    # (so detect_parser thinks nextflow exists in metadata)
    class MockEntryPoint:
        def __init__(self, name):
            self.name = name

        def load(self):
            # Triggers import of the real module, which fails due to monkeypatch
            importlib.import_module("workflow_clinic.parsers.nextflow")

    ParserRegistry._entry_points = {"nextflow": MockEntryPoint("nextflow")}
    ParserRegistry._discovered = True  # Avoid overwriting mocked entry points

    yield

    # Restore normal environment after test
    monkeypatch.undo()
    for module_name in ["workflow_clinic.parsers.nextflow", "groovy_parser", "lark"]:
        if module_name in sys.modules:
            del sys.modules[module_name]
    ParserRegistry._discovered = False
    ParserRegistry._parsers.clear()
    ParserRegistry._manual_parsers.clear()
    ParserRegistry._entry_points.clear()
    with contextlib.suppress(ImportError):
        importlib.import_module("workflow_clinic.parsers.nextflow")


@pytest.mark.usefixtures("missing_dependencies_env")
def test_detect_parser_matches_builtin_pattern_without_importing(tmp_path) -> None:
    """Verify detect_parser identifies .nf files via static patterns without loading the parser."""
    nf_file = tmp_path / "test.nf"
    nf_file.write_text("process TEST {}", encoding="utf-8")

    # Detection should succeed and identify nextflow
    result = ParserRegistry.detect_parser(nf_file)
    assert result == "nextflow"

    # Confirm nextflow was NOT loaded or added to the parsers cache
    assert "nextflow" not in ParserRegistry._parsers


@pytest.mark.usefixtures("missing_dependencies_env")
def test_nextflow_parser_raises_import_error_when_groovy_parser_missing() -> None:
    """Verify that get_parser raises ParserError wrapping ModuleNotFoundError when dependencies are missing."""
    # Triggering registry loader raises ParserError wrapping ModuleNotFoundError
    with pytest.raises(ParserError) as exc_info:
        ParserRegistry.get_parser("nextflow")

    assert isinstance(exc_info.value.__cause__, ModuleNotFoundError)
    assert "Mocked missing module" in str(exc_info.value.__cause__)


@pytest.mark.usefixtures("missing_dependencies_env")
def test_cli_raises_actionable_error_when_groovy_parser_missing(tmp_path) -> None:
    """Verify that CLI output contains instructions to install nextflow when dependency is missing."""
    nf_file = tmp_path / "test.nf"
    nf_file.write_text("process TEST {}", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(app, ["examine", str(nf_file)])
    assert result.exit_code == 1

    output_clean = result.output.replace("\n", " ").replace("  ", " ")
    assert (
        "Parse error: Parser 'nextflow' could not be loaded due to missing optional dependencies."
        in output_clean
    )
    assert "pip install" in result.output
    assert "workflow-clinic[nextflow]" in result.output
