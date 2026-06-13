"""Workflow metadata representation.

This module defines the WorkflowMetadata model, which captures standard metadata
associated with a scientific workflow.
"""

from pydantic import BaseModel


class WorkflowMetadata(BaseModel):
    """Metadata fields for a scientific workflow.

    Attributes:
        name: The name of the scientific workflow.
        version: The version string of the workflow, or None if not specified.
        author: The author or creator of the workflow, or None if not specified.
        description: A text description of the workflow, or None if not specified.
    """

    name: str
    version: str | None = None
    author: str | None = None
    description: str | None = None
