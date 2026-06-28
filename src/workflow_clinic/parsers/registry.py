"""Registry for managing and dynamically detecting workflow parsers.

This module provides the registry class to register, select, and retrieve
parsers without hardcoding language-specific logic.
"""

from pathlib import Path
from typing import ClassVar

from workflow_clinic.exceptions import ParserError, UnsupportedWorkflowError
from workflow_clinic.parsers.base import BaseParser


class ParserRegistry:
    """Registry for workflow parser management.

    Provides dynamic parser detection and retrieval without hardcoded logic.
    """

    _parsers: ClassVar[dict[str, type[BaseParser]]] = {}

    @classmethod
    def register(cls, name: str, parser_class: type[BaseParser]) -> None:
        """Register a parser class with a given name.

        Args:
            name: Unique identifier for the parser (e.g., "nextflow", "snakemake")
            parser_class: Parser class that inherits from BaseParser

        Raises:
            ParserError: If parser_class does not inherit from BaseParser
        """
        if not issubclass(parser_class, BaseParser):
            msg = f"Parser class {parser_class.__name__} must inherit from BaseParser"
            raise ParserError(msg)
        cls._parsers[name] = parser_class

    @classmethod
    def detect_parser(cls, path: Path) -> str:
        """Detect which parser can handle the given workflow path.

        Args:
            path: Path to a workflow file or directory

        Returns:
            Name of the parser that can handle this workflow

        Raises:
            ParserError: If path does not exist or is not accessible
            UnsupportedWorkflowError: If no registered parser can handle the workflow
        """
        if not path.exists():
            msg = f"Path does not exist: {path}"
            raise ParserError(msg)

        for parser_name, parser_class in cls._parsers.items():
            if parser_class.can_parse(path):
                return parser_name

        msg = f"No registered parser can handle workflow at: {path}"
        raise UnsupportedWorkflowError(msg)

    @classmethod
    def get_parser(cls, name: str) -> BaseParser:
        """Retrieve a parser instance by name.

        Args:
            name: Name of the registered parser

        Returns:
            Instance of the requested parser

        Raises:
            ParserError: If parser name is not registered
        """
        if name not in cls._parsers:
            available = ", ".join(cls._parsers.keys())
            msg = f"Parser '{name}' not registered. Available parsers: {available}"
            raise ParserError(msg)

        parser_class = cls._parsers[name]
        return parser_class()
