from pydantic import BaseModel, Field


class TaskResources(BaseModel):
    """Resource constraints for task execution."""

    cpus: int | None = None
    memory: str | None = None
    container: str | None = None


class Task(BaseModel):
    """A single unit of execution (e.g. Nextflow process, Snakemake rule)."""

    id: str
    name: str
    command: str | None = None
    resources: TaskResources = Field(default_factory=TaskResources)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
