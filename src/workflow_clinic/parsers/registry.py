"""Registry for managing and dynamically detecting workflow parsers.

This module provides the registry class to register, select, and retrieve
parsers without hardcoding language-specific logic.
"""

import importlib.metadata
import logging
from pathlib import Path
from typing import Any, ClassVar

from workflow_clinic.exceptions import ParserError, UnsupportedWorkflowError
from workflow_clinic.parsers.base import BaseParser

logger = logging.getLogger(__name__)

# Fallback patterns for built-in optional parsers so we can detect them without importing
_BUILTIN_PATTERNS = {
    "nextflow": [".nf", "nextflow.config", "main.nf"],
}


def _match_pattern(path: Path, pattern: str) -> bool:
    """Check if the given path matches a specific workflow pattern."""
    if pattern.startswith("."):
        return path.suffix == pattern
    if path.is_dir():
        return (path / pattern).exists()
    return path.name == pattern


class ParserRegistry:
    """Registry for workflow parser management.

    Provides dynamic parser detection and retrieval without hardcoded logic.
    """

    _entry_points: ClassVar[dict[str, Any]] = {}
    _parsers: ClassVar[dict[str, type[BaseParser]]] = {}
    _manual_parsers: ClassVar[dict[str, type[BaseParser]]] = {}
    _discovered: ClassVar[bool] = False

    @classmethod
    def _discover_entry_points(cls) -> None:
        """Scan entry points metadata without loading the underlying modules."""
        if cls._discovered:
            return
        cls._discovered = True
        try:
            eps = importlib.metadata.entry_points(group="workflow_clinic.parsers")
            cls._entry_points = {ep.name: ep for ep in eps}
        except Exception:
            # We catch Exception broadly here because it wraps the system/metadata package-reading
            # APIs, which can raise various OS or permission errors during scanning.
            logger.exception("Failed to scan parser entry points")

    @classmethod
    def register(cls, name: str, parser_class: type[BaseParser]) -> None:
        """Register a parser class manually with a given name.

        Note: This writes to both `_parsers` (so get_parser() can fetch it normally)
        and `_manual_parsers` (so detect_parser() can match it immediately
        without checking built-in static patterns or loading entry points).
        """
        if not isinstance(parser_class, type) or not issubclass(
            parser_class, BaseParser
        ):
            parser_name = getattr(parser_class, "__name__", repr(parser_class))
            msg = f"Parser class {parser_name} must inherit from BaseParser"
            raise ParserError(msg)
        cls._parsers[name] = parser_class
        cls._manual_parsers[name] = parser_class

    @classmethod
    def detect_parser(cls, path: Path) -> str:  # noqa: C901
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

        cls._discover_entry_points()

        # 1. Check manually registered parsers first (used for testing mocks)
        for name, parser_class in list(cls._manual_parsers.items()):
            if parser_class.can_parse(path):
                return name

        # 2. Check known built-in optional patterns first (zero imports)
        for name, patterns in _BUILTIN_PATTERNS.items():
            if name in cls._entry_points and any(
                _match_pattern(path, pat) for pat in patterns
            ):
                return name

        # 3. For other plugins, load dynamically and check can_parse()
        for name, ep in cls._entry_points.items():
            if name in _BUILTIN_PATTERNS:
                continue

            if name not in cls._parsers:
                try:
                    cls._parsers[name] = ep.load()
                    logger.debug("Loaded parser plugin '%s'", name)
                except ImportError:
                    logger.debug(
                        "Failed to load parser plugin '%s' during detection due to missing dependencies",
                        name,
                    )
                    continue

            parser_class = cls._parsers[name]
            if parser_class.can_parse(path):
                return name

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
            ParserError: If parser name is not registered or failed to load
        """
        cls._discover_entry_points()

        if name not in cls._entry_points and name not in cls._parsers:
            available = ", ".join(
                set(cls._entry_points.keys()) | set(cls._parsers.keys())
            )
            msg = f"Parser '{name}' is not registered. Available parsers: {available}"
            raise ParserError(msg)

        if name not in cls._parsers:
            ep = cls._entry_points[name]
            try:
                cls._parsers[name] = ep.load()
            except ImportError as e:
                msg = f"Parser '{name}' could not be loaded due to missing optional dependencies."
                raise ParserError(msg) from e

        parser_class = cls._parsers[name]
        return parser_class()
