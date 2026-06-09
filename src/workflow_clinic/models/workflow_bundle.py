from pydantic import BaseModel, Field

from workflow_clinic.models.metadata import WorkflowMetadata
from workflow_clinic.models.task import Task


class WorkflowBundle(BaseModel):
    """Common language-independent representation of a workflow."""

    metadata: WorkflowMetadata
    tasks: list[Task] = Field(default_factory=list)
