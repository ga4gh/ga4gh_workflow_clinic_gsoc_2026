"""Unit and integration tests for the parser infrastructure."""

from pathlib import Path

import pytest

from workflow_clinic.exceptions import (
    InvalidWorkflowError,
    ParserError,
    UnsupportedWorkflowError,
    WorkflowClinicError,
)
from workflow_clinic.models import WorkflowBundle, WorkflowMetadata
from workflow_clinic.parsers.base import BaseParser
from workflow_clinic.parsers.registry import ParserRegistry


# Define a MockParser for testing
class MockParser(BaseParser):
    """Mock parser implementation for testing parser infrastructure."""

    @classmethod
    def can_parse(cls, path: Path) -> bool:
        return path.suffix == ".mock"

    def parse(self, path: Path, entrypoint: str | None = None) -> WorkflowBundle:
        _ = entrypoint
        if not path.exists():
            msg = f"File not found: {path}"
            raise ParserError(msg)
        if "invalid" in path.name:
            msg = "Invalid syntax in mock file"
            raise InvalidWorkflowError(msg)
        return WorkflowBundle(
            metadata=WorkflowMetadata(
                name="mock-workflow",
                version="1.0.0",
                author="Test Author",
                description="A mock workflow for testing",
            )
        )


def test_exception_hierarchy() -> None:
    """Verify exceptions inherit from WorkflowClinicError and relate correctly."""
    assert issubclass(ParserError, WorkflowClinicError)
    assert issubclass(InvalidWorkflowError, ParserError)
    assert issubclass(UnsupportedWorkflowError, ParserError)

    # Test instantiation
    msg = "test error"
    err = WorkflowClinicError(msg)
    assert str(err) == msg

    # Test catching behavior
    with pytest.raises(WorkflowClinicError):
        raise InvalidWorkflowError(msg)


def test_base_parser_abstract() -> None:
    """Verify that BaseParser cannot be instantiated directly."""
    with pytest.raises(TypeError):
        BaseParser()  # type: ignore[abstract]


def test_registry_registration_and_retrieval() -> None:
    """Verify parser registration, retrieval, and error scenarios."""
    # Register mock parser
    ParserRegistry.register("mock", MockParser)

    # Get parser and verify class/instance
    parser = ParserRegistry.get_parser("mock")
    assert isinstance(parser, MockParser)

    # Test registering invalid class (does not inherit from BaseParser)
    class InvalidParser:
        pass

    with pytest.raises(ParserError) as exc_info:
        ParserRegistry.register("invalid", InvalidParser)  # type: ignore[arg-type]
    assert "must inherit from BaseParser" in str(exc_info.value)

    # Test get unregistered parser name
    with pytest.raises(ParserError) as exc_info:
        ParserRegistry.get_parser("unknown")
    assert "not registered" in str(exc_info.value)


def test_registry_detection(tmp_path: Path) -> None:
    """Verify parser registry detection behavior on different files/folders."""
    # Ensure mock is registered
    ParserRegistry.register("mock", MockParser)

    # Create mock file
    mock_file = tmp_path / "test.mock"
    mock_file.write_text("content")

    # Create unsupported file
    unsupported_file = tmp_path / "test.txt"
    unsupported_file.write_text("content")

    # Test successful detection
    detected = ParserRegistry.detect_parser(mock_file)
    assert detected == "mock"

    # Test non-existent path
    non_existent = tmp_path / "does_not_exist.mock"
    with pytest.raises(ParserError) as exc_info:
        ParserRegistry.detect_parser(non_existent)
    assert "Path does not exist" in str(exc_info.value)

    # Test unsupported workflow path
    with pytest.raises(UnsupportedWorkflowError) as exc_info:
        ParserRegistry.detect_parser(unsupported_file)
    assert "No registered parser can handle" in str(exc_info.value)


def test_integration_flow(tmp_path: Path) -> None:
    """Test full integration: detect -> get -> parse."""
    ParserRegistry.register("mock", MockParser)

    # Valid workflow
    valid_file = tmp_path / "test.mock"
    valid_file.write_text("mock content")

    # Detect
    name = ParserRegistry.detect_parser(valid_file)
    assert name == "mock"

    # Retrieve
    parser = ParserRegistry.get_parser(name)
    assert isinstance(parser, MockParser)

    # Parse
    bundle = parser.parse(valid_file)
    assert bundle.metadata.name == "mock-workflow"

    # Invalid workflow syntax
    invalid_file = tmp_path / "invalid_test.mock"
    invalid_file.write_text("invalid content")
    with pytest.raises(InvalidWorkflowError):
        parser.parse(invalid_file)
