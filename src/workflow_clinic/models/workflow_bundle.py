"""Workflow bundle representation.

This module defines the WorkflowBundle model, which acts as a language-independent
container holding metadata and all associated tasks of a scientific workflow.
"""

from pydantic import BaseModel, Field

from workflow_clinic.models.metadata import WorkflowMetadata
from workflow_clinic.models.task import Task


class WorkflowBundle(BaseModel):
    """Common language-independent representation of a workflow.

    Attributes:
        metadata: Metadata of the scientific workflow.
        tasks: List of execution tasks that compose the workflow.
    """

    metadata: WorkflowMetadata
    tasks: list[Task] = Field(default_factory=list)
