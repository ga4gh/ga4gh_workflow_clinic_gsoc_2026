"""Abstract base parser interface for Workflow Clinic.

This module defines the contract that all concrete workflow parsers
(e.g., Nextflow, Snakemake) must implement.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from workflow_clinic.models import WorkflowBundle


class BaseParser(ABC):
    """Abstract base class for all workflow parsers.

    All concrete parser implementations must inherit from this class
    and implement the required abstract methods.
    """

    @classmethod
    @abstractmethod
    def can_parse(cls, path: Path) -> bool:
        """Determine if this parser can handle the given workflow path.

        Args:
            path: Path to a workflow file or directory

        Returns:
            True if this parser can handle the workflow, False otherwise
        """

    @abstractmethod
    def parse(self, path: Path, entrypoint: str | None = None) -> WorkflowBundle:
        """Parse a workflow file or directory into a WorkflowBundle.

        Args:
            path: Path to a workflow file or directory
            entrypoint: Optional entrypoint file name (for directory-based workflows)

        Returns:
            WorkflowBundle representation of the workflow

        Raises:
            InvalidWorkflowError: If workflow content is malformed
            ParserError: If parsing fails for other reasons
        """
