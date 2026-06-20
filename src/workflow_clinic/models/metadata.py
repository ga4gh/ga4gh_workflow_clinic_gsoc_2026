"""Workflow metadata representation.

This module defines the WorkflowMetadata model, which captures standard metadata
associated with a scientific workflow.
"""

from pydantic import BaseModel, field_validator


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

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Ensure name is not empty or containing only whitespace."""
        if not v.strip():
            msg = "name must not be empty or contain only whitespace"
            raise ValueError(msg)
        return v
