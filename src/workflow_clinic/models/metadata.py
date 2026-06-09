from pydantic import BaseModel


class WorkflowMetadata(BaseModel):
    """Metadata fields for a scientific workflow."""

    name: str
    version: str | None = None
    author: str | None = None
    description: str | None = None
